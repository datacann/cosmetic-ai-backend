from decimal import Decimal, ROUND_HALF_UP

from app.models.compatibility_scoring import (
    DimensionScore,
    ScoreBreakdown,
    ScoreCompatibilityRequest,
    ScoreCompatibilityResponse,
)


DIMENSION_WEIGHTS = {
    "skin_type": 20,
    "sensitivity": 30,
    "goals": 20,
    "appearance": 20,
    "routine": 10,
}
ASSESSMENT_VALUES = {
    "supportive": 100,
    "neutral": 75,
    "caution": 40,
}
AVOID_PENALTY_PER_MATCH = 10
MAX_AVOID_PENALTY = 30
CAUTION_SCORE_CAP = 69
SCORE_MEANING = (
    "Bu skor ürün güvenliği, ürün kalitesi veya etkinlik yüzdesi değil; "
    "verilen kullanıcı profili için kişisel kozmetik uyum göstergesidir."
)


class ScoringInsufficientDataError(Exception):
    pass


def score_compatibility(
    request: ScoreCompatibilityRequest,
) -> ScoreCompatibilityResponse:
    active_weight = 0
    weighted_sum = 0
    active_dimensions: dict[str, int] = {}

    for name, weight in DIMENSION_WEIGHTS.items():
        dimension = getattr(request.compatibility, name)
        if dimension.available and dimension.assessment in ASSESSMENT_VALUES:
            value = ASSESSMENT_VALUES[dimension.assessment]
            active_dimensions[name] = value
            active_weight += weight
            weighted_sum += value * weight

    if not active_weight:
        raise ScoringInsufficientDataError(
            "Skor hesaplamak için en az bir değerlendirilebilir uyum başlığı gerekli."
        )

    raw_score = _round_half_up(Decimal(weighted_sum) / Decimal(active_weight))
    score = raw_score
    applied_rules = ["available_dimensions_reweighted"] if active_weight < 100 else []

    unique_avoid_matches = {
        match.strip().casefold() for match in request.avoid_matches if match.strip()
    }
    if unique_avoid_matches:
        penalty = min(
            len(unique_avoid_matches) * AVOID_PENALTY_PER_MATCH,
            MAX_AVOID_PENALTY,
        )
        score -= penalty
        applied_rules.append(f"avoid_preference_penalty_{penalty}")
        if score > CAUTION_SCORE_CAP:
            score = CAUTION_SCORE_CAP
            applied_rules.append("avoid_preference_score_cap_69")

    sensitivity = request.compatibility.sensitivity
    if sensitivity.available and sensitivity.assessment == "caution" and score > CAUTION_SCORE_CAP:
        score = CAUTION_SCORE_CAP
        applied_rules.append("sensitivity_caution_score_cap_69")

    caution_count = sum(
        1
        for name in active_dimensions
        if getattr(request.compatibility, name).assessment == "caution"
    )
    if caution_count >= 3 and score > CAUTION_SCORE_CAP:
        score = CAUTION_SCORE_CAP
        applied_rules.append("multiple_cautions_score_cap_69")

    score = max(0, min(100, score))
    breakdown = {}
    for name, configured_weight in DIMENSION_WEIGHTS.items():
        dimension = getattr(request.compatibility, name)
        included = name in active_dimensions
        normalized_weight = (
            Decimal(configured_weight) / Decimal(active_weight) if included else Decimal(0)
        )
        contribution = (
            Decimal(active_dimensions[name]) * normalized_weight if included else Decimal(0)
        )
        breakdown[name] = DimensionScore(
            assessment=dimension.assessment,
            value=active_dimensions.get(name),
            configured_weight=configured_weight,
            normalized_weight=float(normalized_weight.quantize(Decimal("0.0001"))),
            contribution=float(contribution.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            included=included,
        )

    return ScoreCompatibilityResponse(
        product=request.product,
        score=score,
        raw_score=raw_score,
        verdict=_verdict(score),
        coverage=active_weight,
        breakdown=ScoreBreakdown(**breakdown),
        applied_rules=applied_rules,
        score_meaning=SCORE_MEANING,
    )


def _round_half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _verdict(score: int) -> str:
    if score >= 85:
        return "Çok Uygun"
    if score >= 70:
        return "Uygun"
    if score >= 50:
        return "Dikkatli Değerlendir"
    return "Düşük Uyum"
