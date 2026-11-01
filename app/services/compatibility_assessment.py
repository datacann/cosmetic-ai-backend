import json
import os

from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from app.models.compatibility_assessment import (
    AnalyzeContextCompatibilityRequest,
    AnalyzeContextCompatibilityResponse,
    AvoidPreferenceCompatibility,
    CompatibilityAssessmentModelOutput,
    CompatibilitySignalResult,
    FormulaCompatibilityAssessment,
    FormulaCompatibilityModelOutput,
    GoalCompatibilityResult,
    GoalsCompatibilityResult,
    RoutineCompatibilityResult,
)
from app.services.openai_usage import record_openai_usage


load_dotenv()

DEFAULT_MODEL = "gpt-5.4-mini"
DISCLAIMER = (
    "Bu değerlendirme kozmetik uyum bilgisidir; tıbbi tanı, tedavi, "
    "etkinlik veya güvenlik garantisi değildir."
)


class CompatibilityAssessmentError(Exception):
    pass


class CompatibilityAssessmentConfigurationError(CompatibilityAssessmentError):
    pass


class CompatibilityAssessmentUnavailableError(CompatibilityAssessmentError):
    pass


def _get_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise CompatibilityAssessmentConfigurationError(
            "OPENAI_API_KEY ortam değişkeni tanımlı değil."
        )
    return OpenAI(
        api_key=api_key,
        timeout=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "45")),
        max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "0")),
    )


def analyze_context_compatibility(
    request: AnalyzeContextCompatibilityRequest,
) -> AnalyzeContextCompatibilityResponse:
    formula_inputs = [_compact_formula(formula) for formula in request.ingredient_knowledge.formulas]
    verified_total = sum(len(formula["ingredients"]) for formula in formula_inputs)
    warnings: list[str] = []
    if request.ingredient_knowledge.unresolved_count:
        warnings.append("incomplete_ingredient_knowledge")

    if not verified_total:
        return _unknown_response(request, warnings=[*warnings, "no_verified_ingredient_knowledge"])

    payload = {
        "product": request.product.model_dump(exclude_none=True),
        "formulas": formula_inputs,
        "user_context": request.user_context.model_dump(exclude_none=True),
    }
    model = os.getenv("OPENAI_COMPATIBILITY_MODEL", DEFAULT_MODEL)
    try:
        response = _get_client().responses.parse(
            model=model,
            reasoning={"effort": os.getenv("OPENAI_COMPATIBILITY_REASONING_EFFORT", "none")},
            text_format=CompatibilityAssessmentModelOutput,
            instructions=(
                "Assess cosmetic compatibility using only supplied canonical user context and "
                "verified ingredient knowledge. Assess skin_type, sensitivity, every selected "
                "goal individually, "
                "current_appearance, and routine independently for every formula. Use only "
                "the defined statuses. Missing context means unknown, never a negative signal. "
                "Return conflict_severity (none, mild, moderate, strong) independently from status "
                "for each dimension. A general match may still have mild severity; caution commonly "
                "has moderate severity. Use none when no supported concern exists. "
                "Return exactly one goals.assessments item for every supplied user_context.skin.goals "
                "value, preserving its canonical goal value and order. Never collapse goals into one "
                "status. An unassessable selected goal must be unknown, not neutral or negative. "
                "Evidence ingredients must be exact supplied canonical names. Do not research, "
                "invent ingredients or concentrations, diagnose, make medical/safety claims, "
                "or output any numeric suitability score. Avoid-preference identity matching "
                "is handled deterministically by the backend. Keep Turkish reasons brief."
            ),
            input=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
        record_openai_usage(
            operation="compatibility_analysis", model=model, response=response
        )
    except (APIConnectionError, APITimeoutError) as exc:
        raise CompatibilityAssessmentUnavailableError(
            "OpenAI uyumluluk servisine şu anda ulaşılamıyor."
        ) from exc
    except APIStatusError as exc:
        if exc.status_code in {400, 401, 403, 404}:
            raise CompatibilityAssessmentConfigurationError(
                "OpenAI kimlik bilgileri veya uyumluluk modeli ayarı geçersiz."
            ) from exc
        raise CompatibilityAssessmentUnavailableError(
            f"OpenAI uyumluluk isteğini tamamlayamadı (status={exc.status_code})."
        ) from exc

    output = response.output_parsed
    if output is None or len(output.formulas) != len(request.ingredient_knowledge.formulas):
        raise CompatibilityAssessmentUnavailableError(
            "OpenAI yanıtı beklenen doğrulanmış formül değerlendirmelerini içermiyor."
        )

    results = [
        _validate_formula(request, source_formula, model_formula)
        for source_formula, model_formula in zip(
            request.ingredient_knowledge.formulas, output.formulas, strict=True
        )
    ]
    return AnalyzeContextCompatibilityResponse(
        product=request.product,
        formulas=results,
        overall_cautions=output.overall_cautions,
        warnings=warnings,
        disclaimer=DISCLAIMER,
    )


def _compact_formula(formula) -> dict:
    ingredients = []
    for enriched in formula.ingredients:
        knowledge = enriched.knowledge
        if knowledge.evidence_status != "verified" or not knowledge.canonical_name:
            continue
        ingredients.append({
            "canonical_name": knowledge.canonical_name,
            "concentration": enriched.normalized.concentration,
            "functions": knowledge.functions,
            "cosmetic_properties": knowledge.cosmetic_properties,
            "caution_tags": knowledge.caution_tags,
        })
    return {"formula_name": formula.name, "ingredients": ingredients}


def _validate_formula(request, source_formula, output: FormulaCompatibilityModelOutput):
    allowed = {
        item.knowledge.canonical_name.casefold(): item.knowledge.canonical_name
        for item in source_formula.ingredients
        if item.knowledge.evidence_status == "verified" and item.knowledge.canonical_name
    }
    context = request.user_context
    availability = {
        "skin_type": context.skin.skin_type != "unknown",
        "sensitivity": context.skin.sensitivity != "unknown",
        "goals": bool(context.skin.goals),
        "current_appearance": context.skin.appearance not in ([], ["unknown"]),
        "routine": bool(context.routine.morning or context.routine.evening),
    }
    dimensions = {
        name: _validated_signal(getattr(output, name), available, allowed)
        for name, available in availability.items()
        if name not in {"routine", "goals"}
    }
    goals = _validated_goals(output.goals, context.skin.goals, allowed)
    routine = _validated_routine(output.routine, availability["routine"], allowed)
    deterministic_conflicts = _routine_active_conflicts(source_formula, context)
    if deterministic_conflicts:
        routine.status = "conflict"
        routine.conflict_severity = "strong"
        routine.potential_conflicts = list(dict.fromkeys([
            *routine.potential_conflicts, *deterministic_conflicts
        ]))

    avoid_matches = _avoid_matches(source_formula, context)
    avoid = AvoidPreferenceCompatibility(
        status=("conflict" if avoid_matches else
                "neutral" if context.preferences.avoid_ingredients else "unknown"),
        conflict_severity="strong" if avoid_matches else "none",
        matched_avoid_ingredients=avoid_matches,
    )
    confidence = _assessment_confidence(request, source_formula)
    return FormulaCompatibilityAssessment(
        formula_name=source_formula.name,
        **dimensions, goals=goals,
        avoid_preferences=avoid,
        routine=routine,
        goal_coverage=_goal_coverage(goals),
        assessment_confidence=confidence,
    )


def _validated_signal(signal, available: bool, allowed: dict[str, str]):
    if not available:
        return CompatibilitySignalResult(
            status="unknown", reasons=["Bu başlık için kullanıcı bilgisi bulunmuyor."],
            conflict_severity="none", evidence_ingredients=[], matched_context=[], unassessed_context=[], available=False,
        )
    evidence = list(dict.fromkeys(
        allowed[name.casefold()] for name in signal.evidence_ingredients
        if name.casefold() in allowed
    ))
    status = signal.status
    reasons = signal.reasons
    if status in {"strong_match", "match", "caution", "conflict"} and not evidence:
        status = "unknown"
        signal.conflict_severity = "none"
        reasons = ["Bu değerlendirme doğrulanmış içerik bilgisiyle desteklenemedi."]
    return CompatibilitySignalResult(
        **signal.model_dump(exclude={"status", "reasons", "evidence_ingredients"}),
        status=status, reasons=reasons, evidence_ingredients=evidence, available=True,
    )


def _validated_routine(signal, available: bool, allowed: dict[str, str]):
    base = _validated_signal(signal, available, allowed)
    if base.status == "unknown":
        return RoutineCompatibilityResult(
            **base.model_dump(), potential_conflicts=[], recommended_time="unknown",
        )
    return RoutineCompatibilityResult(
        **base.model_dump(), potential_conflicts=signal.potential_conflicts,
        recommended_time=signal.recommended_time if available else "unknown",
    )


def _validated_goals(output, selected_goals, allowed) -> GoalsCompatibilityResult:
    by_goal = {item.goal: item for item in output.assessments}
    assessments = []
    for goal in selected_goals:
        signal = by_goal.get(goal)
        if signal is None:
            assessments.append(GoalCompatibilityResult(
                goal=goal, status="unknown", conflict_severity="none",
                reasons=["Bu hedef doğrulanmış içerik bilgisiyle değerlendirilemedi."],
                evidence_ingredients=[], matched_context=[],
                unassessed_context=[goal], available=True,
            ))
            continue
        validated = _validated_signal(signal, True, allowed)
        assessments.append(GoalCompatibilityResult(
            goal=goal, **validated.model_dump()
        ))
    return GoalsCompatibilityResult(assessments=assessments)


def _avoid_matches(formula, context) -> list[str]:
    preferences = {
        item.canonical_name.casefold()
        for item in context.preferences.avoid_ingredients if item.canonical_name
    }
    return list(dict.fromkeys(
        item.knowledge.canonical_name
        for item in formula.ingredients
        if item.knowledge.canonical_name
        and item.knowledge.canonical_name.casefold() in preferences
    ))


def _routine_active_conflicts(formula, context) -> list[str]:
    actives = {
        item.knowledge.canonical_name.casefold(): item.knowledge.canonical_name
        for item in formula.ingredients
        if item.knowledge.canonical_name
        and "exfoliating_active" in item.knowledge.caution_tags
    }
    routine_names = {
        ingredient.canonical_name.casefold()
        for product in [*context.routine.morning, *context.routine.evening]
        for ingredient in product.ingredients if ingredient.canonical_name
    }
    return [f"duplicate_exfoliating_active:{name}" for key, name in actives.items() if key in routine_names]


def _assessment_confidence(request, formula) -> float:
    all_count = len(formula.ingredients)
    verified_count = sum(
        item.knowledge.evidence_status == "verified" for item in formula.ingredients
    )
    knowledge_coverage = verified_count / all_count if all_count else 0.0
    known = len(request.user_context.known_fields)
    total = known + len(request.user_context.unknown_fields)
    context_coverage = known / total if total else 0.0
    return round((knowledge_coverage + context_coverage) / 2, 2)


def _goal_coverage(goals: GoalsCompatibilityResult) -> float | None:
    if not goals.assessments:
        return None
    assessed = sum(item.status != "unknown" for item in goals.assessments)
    return round(assessed / len(goals.assessments), 4)


def _unknown_response(request, warnings):
    formulas = []
    for formula in request.ingredient_knowledge.formulas:
        signal = CompatibilitySignalResult(
            status="unknown", reasons=["Doğrulanmış içerik bilgisi bulunmuyor."],
            conflict_severity="none", evidence_ingredients=[], matched_context=[], unassessed_context=[], available=False,
        )
        routine = RoutineCompatibilityResult(
            **signal.model_dump(), potential_conflicts=[], recommended_time="unknown"
        )
        unknown_goals = GoalsCompatibilityResult(assessments=[
            GoalCompatibilityResult(
                goal=goal, **signal.model_dump()
            )
            for goal in request.user_context.skin.goals
        ])
        formulas.append(FormulaCompatibilityAssessment(
            formula_name=formula.name, skin_type=signal, sensitivity=signal.model_copy(),
            goals=unknown_goals,
            current_appearance=signal.model_copy(),
            avoid_preferences=AvoidPreferenceCompatibility(status="unknown", matched_avoid_ingredients=[]),
            routine=routine, goal_coverage=_goal_coverage(unknown_goals),
            assessment_confidence=_assessment_confidence(request, formula),
        ))
    return AnalyzeContextCompatibilityResponse(
        product=request.product, formulas=formulas, overall_cautions=[],
        warnings=warnings, disclaimer=DISCLAIMER,
    )
