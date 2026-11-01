from types import SimpleNamespace

import pytest

from app.models.compatibility_assessment import (
    AnalyzeContextCompatibilityResponse,
    AvoidPreferenceCompatibility,
    CompatibilitySignalResult,
    FormulaCompatibilityAssessment,
    GoalCompatibilityResult,
    GoalsCompatibilityResult,
    RoutineCompatibilityResult,
)
from app.models.ingredient_acquisition import (
    IngredientAcquisitionResponse,
    IngredientFormula,
    IngredientSource,
)
from app.models.ingredient_knowledge import (
    IngredientKnowledgeRecord,
    IngredientKnowledgeResponse,
    KnowledgeEnrichedFormula,
    KnowledgeEnrichedIngredient,
)
from app.models.ingredient_normalization import RawIngredientFormula
from app.models.product_recognition import ProductRecognitionResult
from app.models.user_context import BuildUserContextRequest, UserContextProfileInput
from app.services.analysis_orchestration import (
    AnalysisPipelineDependencies,
    run_end_to_end_analysis,
)
from app.services.ingredient_normalization import normalize_ingredients
from app.services.openai_usage import record_openai_usage
from app.services.suitability_scoring import calculate_suitability_score
from app.services.user_context import build_user_context


def product(**changes):
    values = dict(brand="Acme", product_name="Hydra", variant=None, category="Moisturizer", confidence=.95, model_confidence=.95, status="recognized", image_quality="good", quality_issues=[], needs_retake=False, retake_reasons=[])
    values.update(changes)
    return ProductRecognitionResult(**values)


def acquisition(source="package", status="complete"):
    formulas = [IngredientFormula(name=None, raw_text="Aqua, Glycerin", ingredients=["Aqua", "Glycerin"], status="complete", uncertain_segments=[])] if status == "complete" else []
    return IngredientAcquisitionResponse(status=status, source=IngredientSource(type=source, match_quality="exact", market="TR"), raw_text=None, ingredients=[], formulas=formulas, uncertain_segments=[], image_quality="good" if source == "package" else None, needs_package_photo=status != "complete", warnings=[])


def enrich(normalized, **_):
    formulas = []
    for formula in normalized.formulas:
        formulas.append(KnowledgeEnrichedFormula(name=formula.name, raw_text=formula.raw_text, ingredients=[KnowledgeEnrichedIngredient(normalized=item, knowledge=IngredientKnowledgeRecord(canonical_name=item.canonical_name, functions=["humectant"], cosmetic_properties=["supports_hydration"], caution_tags=[], evidence_status="verified", source="postgresql", source_references=["https://example.test/source"])) for item in formula.ingredients]))
    return IngredientKnowledgeResponse(provenance=normalized.provenance, formulas=formulas, unresolved_count=0, paid_fallback_count=0)


def assess(request):
    signal = lambda status="match": CompatibilitySignalResult(status=status, reasons=["Uyumlu kozmetik işlev"], evidence_ingredients=["Water"], matched_context=[], unassessed_context=[], available=True)
    routine = RoutineCompatibilityResult(**signal().model_dump(), potential_conflicts=[], recommended_time="both")
    goals = GoalsCompatibilityResult(assessments=[
        GoalCompatibilityResult(goal=goal, **signal().model_dump())
        for goal in request.user_context.skin.goals
    ])
    formulas = [FormulaCompatibilityAssessment(formula_name=item.name, skin_type=signal(), sensitivity=signal(), goals=goals, current_appearance=signal(), avoid_preferences=AvoidPreferenceCompatibility(status="neutral", matched_avoid_ingredients=[]), routine=routine, goal_coverage=1.0 if goals.assessments else None, assessment_confidence=.9) for item in request.ingredient_knowledge.formulas]
    return AnalyzeContextCompatibilityResponse(product=request.product, formulas=formulas, overall_cautions=[], warnings=[], disclaimer="Kozmetik değerlendirmedir.")


def dependencies(recognized=None, acquired=None, cached=None, calls=None):
    calls = calls if calls is not None else []
    def recognize(*_): calls.append("recognize"); return recognized or product()
    def acquire(**kwargs): calls.append("package" if kwargs["images"] else "online"); return acquired or acquisition("package" if kwargs["images"] else "official_online")
    return AnalysisPipelineDependencies(recognize=recognize, acquire=acquire, normalize=normalize_ingredients, enrich=enrich, build_context=build_user_context, assess=assess, score=calculate_suitability_score, cached_acquisition=cached, normalization_resolver=lambda **_: {}, knowledge_cache_resolver=lambda **_: {}, cosing_resolver=lambda **_: {})


def run(deps, *, images=None, manual=None, fallback=False, success=None, user_request=None):
    return run_end_to_end_analysis(product_image=(b"front", "image/jpeg"), ingredient_images=images or [], manual_formulas=manual or [], user_request=user_request or BuildUserContextRequest(profile=UserContextProfileInput(skin_type="oily")), market="TR", allow_online_fallback=fallback, dependencies=deps, on_success=success)


def test_happy_path_returns_typed_personalized_result_and_summary():
    result = run(dependencies(), images=[(b"inci", "image/jpeg")])
    assert result.outcome == "success"
    assert result.product.brand == "Acme"
    assert result.ingredients.formulas[0].ingredients[0].normalized.canonical_name == "Water"
    assert result.personal_suitability[0].result.score == 82
    assert result.summary.recommended_usage_time == "both"


def test_selected_goals_and_all_profile_fields_reach_compatibility_input():
    captured = []
    deps = dependencies()
    original_assess = deps.assess
    deps.assess = lambda request: (captured.append(request.user_context), original_assess(request))[1]
    request = BuildUserContextRequest(profile={
        "skin_type": "combination",
        "sensitivity": "very_sensitive",
        "appearance": ["flaking"],
        "goals": ["hydration", "uneven_tone"],
        "avoid_ingredients": ["Parfum"],
    }, routine=[{
        "name": "Existing cream", "routine_time": "evening",
        "ingredients": [{"raw_name": "Aqua"}],
    }])

    result = run(deps, images=[(b"inci", "image/jpeg")], user_request=request)

    assert result.outcome == "success"
    context = captured[0]
    assert context.skin.skin_type == "combination"
    assert context.skin.sensitivity == "very_sensitive"
    assert context.skin.appearance == ["flaking"]
    assert context.skin.goals == ["hydration", "uneven_tone"]
    assert context.preferences.avoid_ingredients[0].canonical_name == "Parfum"
    assert [item.name for item in context.routine.evening] == ["Existing cream"]


def test_uncertain_product_stops_before_ingredient_work():
    calls = []
    result = run(dependencies(product(status="uncertain", brand=None, product_name=None, confidence=.2), calls=calls), images=[(b"inci", "image/jpeg")])
    assert result.outcome == "needs_product_correction"
    assert calls == ["recognize"]


def test_unrecognized_product_is_controlled():
    result = run(dependencies(product(status="unrecognized", brand=None, product_name=None, confidence=0)))
    assert result.outcome == "needs_product_correction"


def test_unsupported_category_is_not_forced():
    result = run(dependencies(product(category=None)))
    assert result.outcome == "unsupported_product"
    assert result.diagnostics.unsupported_product


def test_package_images_have_priority_over_manual_and_cache():
    calls = []
    result = run(dependencies(cached=lambda **_: pytest.fail("cache must not run"), calls=calls), images=[(b"inci", "image/jpeg")], manual=[RawIngredientFormula(ingredients=["Niacinamide"])])
    assert result.outcome == "success"
    assert calls == ["recognize", "package"]
    assert result.ingredient_source.type == "package_image"


def test_manual_has_priority_over_cache_and_online():
    calls = []
    result = run(dependencies(cached=lambda **_: pytest.fail("cache must not run"), calls=calls), manual=[RawIngredientFormula(ingredients=["Aqua"])], fallback=True)
    assert result.outcome == "success"
    assert calls == ["recognize"]
    assert result.ingredient_source.type == "manual"


def test_cached_formula_has_priority_over_online():
    calls = []
    result = run(dependencies(cached=lambda **_: acquisition("database"), calls=calls), fallback=True)
    assert result.outcome == "success"
    assert calls == ["recognize"]
    assert result.ingredient_source.type == "database"


def test_new_package_formula_is_written_to_cache_once():
    saved = []
    deps = dependencies()
    deps.acquisition_cache_writer = lambda **kwargs: saved.append(kwargs)
    result = run(deps, images=[(b"inci", "image/jpeg")])
    assert result.outcome == "success"
    assert len(saved) == 1
    assert saved[0]["product"].product_name == "Hydra"


def test_online_fallback_requires_explicit_permission():
    calls = []
    result = run(dependencies(cached=lambda **_: None, calls=calls), fallback=False)
    assert result.outcome == "needs_ingredient_photo"
    assert calls == ["recognize"]


def test_online_fallback_runs_once_when_allowed():
    calls = []
    result = run(dependencies(cached=lambda **_: None, calls=calls), fallback=True)
    assert result.outcome == "success"
    assert calls == ["recognize", "online"]


@pytest.mark.parametrize("status", ["partial", "unreadable", "not_found"])
def test_bad_ingredient_acquisition_requests_photo(status):
    result = run(dependencies(acquired=acquisition(status=status)), images=[(b"bad", "image/jpeg")])
    assert result.outcome == "needs_ingredient_photo"


def test_unknown_knowledge_returns_insufficient_data():
    deps = dependencies()
    deps.enrich = lambda normalized, **_: IngredientKnowledgeResponse(provenance=normalized.provenance, formulas=[], unresolved_count=1, paid_fallback_count=0)
    result = run(deps, manual=[RawIngredientFormula(ingredients=["Mysteryium"])])
    assert result.outcome == "insufficient_ingredient_data"


def test_provider_exception_is_controlled_and_does_not_leak_message():
    deps = dependencies()
    deps.recognize = lambda *_: (_ for _ in ()).throw(RuntimeError("secret provider payload"))
    result = run(deps)
    assert result.outcome == "temporary_service_error"
    assert "secret" not in str(result.model_dump())


def test_success_hook_only_runs_for_success():
    events = []
    run(dependencies(), manual=[RawIngredientFormula(ingredients=["Aqua"])], success=lambda: events.append("charged"))
    run(dependencies(product(status="unrecognized", brand=None, product_name=None)), success=lambda: events.append("wrong"))
    assert events == ["charged"]


def test_openai_costs_are_aggregated_by_stage_without_raw_calls():
    deps = dependencies()
    original = deps.recognize
    def metered(*args):
        record_openai_usage(operation="product_recognition", model="gpt-5.4-mini", response=SimpleNamespace(usage=SimpleNamespace(input_tokens=1000, output_tokens=100), output=[]), image_count=1)
        return original(*args)
    deps.recognize = metered
    result = run(deps, manual=[RawIngredientFormula(ingredients=["Aqua"])])
    assert result.cost.product_recognition_usd > 0
    assert result.cost.total_usd == result.cost.product_recognition_usd
    assert "calls" not in result.cost.model_dump()


def test_multiple_formulas_are_scored_independently_in_order():
    result = run(dependencies(), manual=[RawIngredientFormula(name="A", ingredients=["Aqua"]), RawIngredientFormula(name="B", ingredients=["Glycerin"])])
    assert [item.formula_name for item in result.personal_suitability] == ["A", "B"]


def test_no_duplicate_recognition_or_compatibility_call():
    calls = []
    deps = dependencies(calls=calls)
    original_assess = deps.assess
    def one_assess(request): calls.append("assess"); return original_assess(request)
    deps.assess = one_assess
    assert run(deps, manual=[RawIngredientFormula(ingredients=["Aqua"])]).outcome == "success"
    assert calls.count("recognize") == 1
    assert calls.count("assess") == 1
