from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from app.models.analysis_orchestration import (
    AnalysisCostBreakdown,
    AnalysisDiagnostics,
    AnalysisIngredientSource,
    AnalysisSummary,
    EndToEndAnalysisResponse,
    FormulaSuitability,
)
from app.models.compatibility_analysis import CompatibilityProduct
from app.models.compatibility_assessment import AnalyzeContextCompatibilityRequest
from app.models.ingredient_normalization import (
    NormalizeIngredientsRequest,
    RawIngredientFormula,
)
from app.models.suitability_scoring import CalculateSuitabilityScoreRequest
from app.models.user_context import BuildUserContextRequest
from app.services.compatibility_assessment import analyze_context_compatibility
from app.services.ingredient_acquisition import acquire_ingredients
from app.services.ingredient_knowledge import attach_ingredient_knowledge
from app.services.ingredient_normalization import normalize_ingredients
from app.services.openai_usage import begin_usage_capture, finish_usage_capture
from app.services.product_recognition import recognize_product
from app.services.suitability_scoring import (
    SuitabilityScoringInsufficientDataError,
    calculate_suitability_score,
)
from app.services.user_context import build_user_context


logger = logging.getLogger(__name__)


@dataclass
class AnalysisPipelineDependencies:
    recognize: Callable = recognize_product
    acquire: Callable = acquire_ingredients
    normalize: Callable = normalize_ingredients
    enrich: Callable = attach_ingredient_knowledge
    build_context: Callable = build_user_context
    assess: Callable = analyze_context_compatibility
    score: Callable = calculate_suitability_score
    cached_acquisition: Callable | None = None
    acquisition_cache_writer: Callable | None = None
    normalization_resolver: Callable | None = None
    knowledge_cache_resolver: Callable | None = None
    cosing_resolver: Callable | None = None
    knowledge_researcher: Callable | None = None
    knowledge_cache_writer: Callable | None = None


def run_end_to_end_analysis(
    *,
    product_image: tuple[bytes, str],
    ingredient_images: list[tuple[bytes, str]],
    manual_formulas: list[RawIngredientFormula],
    user_request: BuildUserContextRequest,
    market: str,
    allow_online_fallback: bool,
    dependencies: AnalysisPipelineDependencies,
    on_success: Callable[[], None] | None = None,
) -> EndToEndAnalysisResponse:
    token = begin_usage_capture()
    product = source = knowledge = context = compatibility = None
    try:
        product = dependencies.recognize(*product_image)
        if product.status != "recognized" or not product.brand or not product.product_name:
            return _finish(token, "needs_product_correction", product=product, indicators=["product_identity_unconfirmed"], correction=True, retake=product.needs_retake)
        if product.category is None:
            return _finish(token, "unsupported_product", product=product, indicators=["unsupported_or_unclassified_category"], unsupported=True)

        raw_formulas: list[RawIngredientFormula]
        provenance: str
        acquisition = None
        if ingredient_images:
            acquisition = dependencies.acquire(brand=product.brand, product_name=product.product_name, variant=product.variant, market=market, images=ingredient_images)
            provenance = "package_image"
        elif manual_formulas:
            raw_formulas = manual_formulas
            provenance = "manual"
        else:
            if dependencies.cached_acquisition:
                acquisition = dependencies.cached_acquisition(brand=product.brand, product_name=product.product_name, variant=product.variant, market=market)
            if acquisition is None and allow_online_fallback:
                acquisition = dependencies.acquire(brand=product.brand, product_name=product.product_name, variant=product.variant, market=market, images=[])
            if acquisition is None:
                return _finish(token, "needs_ingredient_photo", product=product, indicators=["ingredient_source_missing"], retake=True)
            provenance = _provenance(acquisition.source.type)

        if acquisition is not None:
            source = AnalysisIngredientSource(type=_source_type(acquisition.source.type), status=acquisition.status, reliability=_reliability(acquisition))
            if acquisition.status != "complete" or not acquisition.formulas:
                return _finish(token, "needs_ingredient_photo", product=product, source=source, indicators=["ingredient_text_incomplete"], retake=True)
            raw_formulas = [RawIngredientFormula(name=item.name, raw_text=item.raw_text, ingredients=item.ingredients) for item in acquisition.formulas]
            if acquisition.source.type != "database" and dependencies.acquisition_cache_writer:
                dependencies.acquisition_cache_writer(
                    product=product, acquisition=acquisition, market=market
                )
        else:
            source = AnalysisIngredientSource(type="manual", status="complete", reliability="medium")

        normalized = dependencies.normalize(
            NormalizeIngredientsRequest(provenance=provenance, formulas=raw_formulas),
            resolver=dependencies.normalization_resolver,
        )
        knowledge = dependencies.enrich(
            normalized,
            cache_resolver=dependencies.knowledge_cache_resolver or (lambda **_: {}),
            cosing_resolver=dependencies.cosing_resolver or (lambda **_: {}),
            researcher=dependencies.knowledge_researcher,
            cache_writer=dependencies.knowledge_cache_writer,
            market=market,
        )
        verified = sum(item.knowledge.evidence_status == "verified" for formula in knowledge.formulas for item in formula.ingredients)
        if verified == 0:
            return _finish(token, "insufficient_ingredient_data", product=product, source=source, knowledge=knowledge, indicators=["no_verified_ingredient_knowledge"])

        context = dependencies.build_context(user_request)
        compatibility = dependencies.assess(AnalyzeContextCompatibilityRequest(
            product=CompatibilityProduct(brand=product.brand, product_name=product.product_name, category=product.category),
            ingredient_knowledge=knowledge,
            user_context=context,
        ))
        scores = []
        for formula in compatibility.formulas:
            try:
                result = dependencies.score(CalculateSuitabilityScoreRequest(
                    compatibility=formula,
                    ingredient_source_type=source.type if source else "none",
                    ingredient_source_reliability=source.reliability if source else "unknown",
                ))
            except SuitabilityScoringInsufficientDataError:
                return _finish(token, "insufficient_ingredient_data", product=product, source=source, knowledge=knowledge, context=context, compatibility=compatibility, indicators=["insufficient_personal_context"])
            scores.append(FormulaSuitability(formula_name=formula.formula_name, result=result))
        summary = _summary(compatibility)
        if on_success:
            on_success()
        return _finish(token, "success", product=product, source=source, knowledge=knowledge, context=context, compatibility=compatibility, scores=scores, summary=summary)
    except Exception:
        logger.exception("End-to-end analysis pipeline dependency failed")
        return _finish(token, "temporary_service_error", product=product, source=source, knowledge=knowledge, context=context, compatibility=compatibility, indicators=["pipeline_dependency_unavailable"])


def _finish(token, outcome, *, product=None, source=None, knowledge=None, context=None, compatibility=None, scores=None, summary=None, indicators=None, correction=False, retake=False, unsupported=False):
    usage = finish_usage_capture(token)
    return EndToEndAnalysisResponse(
        outcome=outcome, product=product, ingredient_source=source, ingredients=knowledge,
        user_context=context, compatibility=compatibility, personal_suitability=scores or [], summary=summary,
        diagnostics=AnalysisDiagnostics(indicators=indicators or [], correction_needed=correction, retake_needed=retake, unsupported_product=unsupported, unresolved_ingredient_count=knowledge.unresolved_count if knowledge else 0),
        cost=_cost(usage),
    )


def _cost(usage):
    calls = usage["calls"]
    total = lambda stages: round(sum(float(c["estimated_usd"]) for c in calls if c["stage"] in stages), 8)
    return AnalysisCostBreakdown(
        product_recognition_usd=total({"product_recognition"}),
        ingredient_acquisition_usd=total({"ingredient_extraction", "ingredient_lookup"}),
        ingredient_research_usd=total({"ingredient_analysis", "ingredient_knowledge_research"}),
        compatibility_usd=total({"compatibility_analysis"}),
        total_usd=usage["estimated_usd"], input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
        web_search_calls=sum(int(c["web_search_calls"]) for c in calls),
    )


def _source_type(value):
    return {"package": "package_image", "official_online": "official_online", "trusted_online": "trusted_external", "database": "database"}.get(value, "none")


def _provenance(value):
    return {"package": "package_image", "official_online": "official_online", "trusted_online": "trusted_external", "database": "database"}.get(value, "trusted_external")


def _reliability(acquisition):
    if acquisition.status == "complete" and acquisition.source.match_quality == "exact":
        return "high"
    if acquisition.status == "complete":
        return "medium"
    return "low"


def _summary(response):
    strengths, cautions, times = [], list(response.overall_cautions), []
    for formula in response.formulas:
        signals = [
            formula.skin_type, formula.sensitivity,
            *formula.goals.assessments,
            formula.current_appearance, formula.routine,
        ]
        for signal in signals:
            target = strengths if signal.status in {"strong_match", "match"} else cautions if signal.status in {"caution", "conflict"} else None
            if target is not None:
                target.extend(signal.reasons)
        times.append(formula.routine.recommended_time)
    known = [value for value in times if value != "unknown"]
    recommended = known[0] if known and len(set(known)) == 1 else "unknown"
    return AnalysisSummary(strengths=list(dict.fromkeys(strengths))[:5], cautions=list(dict.fromkeys(cautions))[:5], recommended_usage_time=recommended)
