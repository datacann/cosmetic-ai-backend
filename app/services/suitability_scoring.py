import math

from app.models.suitability_scoring import (
    CalculateSuitabilityScoreRequest,
    ScoreCap,
    ScorePenalty,
    SuitabilityAppliedWeights,
    SuitabilityDimensionScores,
    SuitabilityScoreResponse,
)


# Initial transparent policy. Keep calibration values centralized here.
STATUS_VALUES = {
    "strong_match": 100,
    "match": 85,
    "neutral": 70,
    "caution": 50,
    "conflict": 20,
    "unknown": None,
}
SEVERITY_ADJUSTMENTS = {
    "none": 0,
    "mild": -3,
    "moderate": -7,
    "strong": -12,
}
DIMENSION_WEIGHTS = {
    "skin_type": 0.15,
    "sensitivity": 0.20,
    "goals": 0.20,
    "current_appearance": 0.15,
    "avoid_preferences": 0.15,
    "routine": 0.15,
}
AVOID_FIRST_PENALTY = -15
AVOID_ADDITIONAL_PENALTY = -5
AVOID_MAX_PENALTY = -25
SENSITIVITY_CONFLICT_PENALTY = -10
ROUTINE_CONFLICT_PENALTY = -7
AVOID_CONFLICT_CAP = 69
TWO_SERIOUS_CONFLICTS_CAP = 59
THREE_SERIOUS_CONFLICTS_CAP = 49
NON_PACKAGE_EVIDENCE_PENALTY = -10
NON_PACKAGE_EVIDENCE_CAP = 79
LOW_RELIABILITY_EVIDENCE_PENALTY = -20
LOW_RELIABILITY_EVIDENCE_CAP = 69
VERDICT_THRESHOLDS = (
    (85, "Çok Uygun"),
    (70, "Uygun"),
    (50, "Dikkatli Değerlendir"),
    (0, "Düşük Uyum"),
)
SCORE_MEANING = (
    "Bu puan ürünün bu kullanıcının mevcut profili, hedefleri, tercihleri ve "
    "rutiniyle kişisel uyumunu gösterir; güvenlik yüzdesi veya tıbbi değerlendirme değildir."
)


class SuitabilityScoringInsufficientDataError(ValueError):
    pass


def calculate_suitability_score(
    request: CalculateSuitabilityScoreRequest,
) -> SuitabilityScoreResponse:
    compatibility = request.compatibility
    statuses = {
        "skin_type": compatibility.skin_type.status,
        "sensitivity": compatibility.sensitivity.status,
        "current_appearance": compatibility.current_appearance.status,
        "avoid_preferences": compatibility.avoid_preferences.status,
        "routine": compatibility.routine.status,
    }
    dimension_scores = {
        name: STATUS_VALUES[status] for name, status in statuses.items()
    }
    dimension_scores["goals"] = _goal_dimension_score(compatibility.goals)
    available = [name for name, value in dimension_scores.items() if value is not None]
    total_weight = sum(DIMENSION_WEIGHTS[name] for name in available)
    if total_weight <= 0:
        raise SuitabilityScoringInsufficientDataError(
            "Puan hesaplamak için en az bir değerlendirilebilir uyum boyutu gereklidir."
        )
    applied_weights = {
        name: (DIMENSION_WEIGHTS[name] / total_weight if name in available else 0.0)
        for name in DIMENSION_WEIGHTS
    }
    base_score = sum(
        dimension_scores[name] * applied_weights[name]
        for name in available
    )

    severity_adjustments = _severity_adjustments(compatibility)
    penalties = [*_penalties(compatibility), *_evidence_penalties(request)]
    penalized_score = base_score + sum(item.points for item in [*severity_adjustments, *penalties])
    caps = [*_caps(compatibility), *_evidence_caps(request)]
    capped_score = min(
        [penalized_score, *(item.max_score for item in caps)]
    )
    score = _round_score(capped_score)

    return SuitabilityScoreResponse(
        score=score,
        verdict=_verdict(score),
        dimension_scores=SuitabilityDimensionScores(**dimension_scores),
        applied_weights=SuitabilityAppliedWeights(**applied_weights),
        base_score=round(base_score, 2),
        severity_adjustments=severity_adjustments,
        penalties=penalties,
        caps=caps,
        analysis_confidence=compatibility.assessment_confidence,
        score_meaning=SCORE_MEANING,
    )


def _severity_adjustments(compatibility) -> list[ScorePenalty]:
    signals = {
        "skin_type": compatibility.skin_type,
        "sensitivity": compatibility.sensitivity,
        "current_appearance": compatibility.current_appearance,
        "routine": compatibility.routine,
    }
    adjustments = []
    for dimension, signal in signals.items():
        points = SEVERITY_ADJUSTMENTS[signal.conflict_severity]
        if not points or signal.status == "unknown":
            continue
        # Caution/conflict statuses already encode the concern in the weighted
        # base score. Severity differentiates a generally compatible/neutral
        # dimension that still carries a structured residual concern; applying
        # it again to caution/conflict would count the same concern twice.
        if signal.status in {"caution", "conflict"}:
            continue
        adjustments.append(ScorePenalty(
            code=f"{dimension}_{signal.conflict_severity}_severity",
            points=points,
        ))
    goal_adjustment = _goal_severity_adjustment(compatibility.goals)
    if goal_adjustment:
        adjustments.append(goal_adjustment)
    return adjustments


def _goal_dimension_score(goals) -> float | None:
    values = [
        STATUS_VALUES[item.status]
        for item in goals.assessments
        if STATUS_VALUES[item.status] is not None
    ]
    return sum(values) / len(values) if values else None


def _goal_severity_adjustment(goals) -> ScorePenalty | None:
    eligible = [
        item.conflict_severity for item in goals.assessments
        if item.status not in {"unknown", "caution", "conflict"}
        and item.conflict_severity != "none"
    ]
    if not eligible:
        return None
    severity = min(eligible, key=lambda item: SEVERITY_ADJUSTMENTS[item])
    return ScorePenalty(
        code=f"goals_{severity}_severity",
        points=SEVERITY_ADJUSTMENTS[severity],
    )


def _penalties(compatibility) -> list[ScorePenalty]:
    penalties: list[ScorePenalty] = []
    avoid_count = len(compatibility.avoid_preferences.matched_avoid_ingredients)
    if avoid_count:
        points = AVOID_FIRST_PENALTY + AVOID_ADDITIONAL_PENALTY * (avoid_count - 1)
        penalties.append(ScorePenalty(
            code="exact_avoid_preference_conflict",
            points=max(points, AVOID_MAX_PENALTY),
        ))
    if compatibility.sensitivity.status == "conflict":
        penalties.append(ScorePenalty(
            code="sensitivity_conflict", points=SENSITIVITY_CONFLICT_PENALTY
        ))
    if compatibility.routine.status == "conflict":
        penalties.append(ScorePenalty(
            code="routine_conflict", points=ROUTINE_CONFLICT_PENALTY
        ))
    return penalties


def _evidence_penalties(request: CalculateSuitabilityScoreRequest) -> list[ScorePenalty]:
    source_type = request.ingredient_source_type
    reliability = request.ingredient_source_reliability
    if source_type is None and reliability is None:
        return []
    if reliability in {"low", "unknown"} or source_type == "none":
        return [ScorePenalty(code="low_ingredient_evidence", points=LOW_RELIABILITY_EVIDENCE_PENALTY)]
    if source_type != "package_image":
        return [ScorePenalty(code="ingredient_list_not_package_verified", points=NON_PACKAGE_EVIDENCE_PENALTY)]
    return []


def _evidence_caps(request: CalculateSuitabilityScoreRequest) -> list[ScoreCap]:
    source_type = request.ingredient_source_type
    reliability = request.ingredient_source_reliability
    if source_type is None and reliability is None:
        return []
    if reliability in {"low", "unknown"} or source_type == "none":
        return [ScoreCap(code="low_ingredient_evidence_cap", max_score=LOW_RELIABILITY_EVIDENCE_CAP)]
    if source_type != "package_image":
        return [ScoreCap(code="ingredient_list_not_package_verified_cap", max_score=NON_PACKAGE_EVIDENCE_CAP)]
    return []


def _caps(compatibility) -> list[ScoreCap]:
    caps: list[ScoreCap] = []
    avoid_conflict = bool(compatibility.avoid_preferences.matched_avoid_ingredients)
    sensitivity_conflict = compatibility.sensitivity.status == "conflict"
    routine_conflict = compatibility.routine.status == "conflict"
    serious_count = sum((avoid_conflict, sensitivity_conflict, routine_conflict))
    if avoid_conflict:
        caps.append(ScoreCap(code="avoid_preference_conflict_cap", max_score=AVOID_CONFLICT_CAP))
    if serious_count >= 3:
        caps.append(ScoreCap(code="three_serious_conflicts_cap", max_score=THREE_SERIOUS_CONFLICTS_CAP))
    elif serious_count >= 2:
        caps.append(ScoreCap(code="multiple_serious_conflicts_cap", max_score=TWO_SERIOUS_CONFLICTS_CAP))
    return caps


def _round_score(value: float) -> int:
    return max(0, min(100, math.floor(value + 0.5)))


def _verdict(score: int) -> str:
    return next(verdict for minimum, verdict in VERDICT_THRESHOLDS if score >= minimum)
