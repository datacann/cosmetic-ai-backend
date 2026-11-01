from __future__ import annotations

from collections.abc import Callable

from app.database.repository import normalize_text
from app.models.ingredient_analysis import (
    AnalyzeIngredientsRequest,
    AnalyzeIngredientsResponse,
    IngredientAnalysisItem,
)
from app.models.ingredient_knowledge import (
    IngredientKnowledgeRecord,
    IngredientKnowledgeResponse,
    KnowledgeEnrichedFormula,
    KnowledgeEnrichedIngredient,
)
from app.models.ingredient_normalization import NormalizeIngredientsResponse


KnowledgeResolver = Callable[..., dict[str, IngredientAnalysisItem]]
KnowledgeResearcher = Callable[[AnalyzeIngredientsRequest], AnalyzeIngredientsResponse]
KnowledgeCacheWriter = Callable[..., None]

COSING_QUERY_ALIASES = {"water": "Aqua"}
PROPERTY_BY_FUNCTION = {
    "humectant": "supports_hydration",
    "emollient": "supports_a_softer_smoother_appearance",
    "occlusive": "helps_reduce_moisture_loss",
    "surfactant": "supports_cleansing",
    "emulsifier": "supports_formula_stability",
    "preservative": "supports_product_preservation",
    "antioxidant": "helps_protect_the_formula_from_oxidation",
    "solvent": "helps_dissolve_formula_components",
    "fragrance": "provides_fragrance",
    "colorant": "provides_color",
    "uv_filter": "provides_a_uv_filtering_function",
    "ph_adjuster": "supports_ph_adjustment",
    "chelating_agent": "supports_formula_stability",
    "exfoliant": "supports_a_smoother_looking_surface",
    "soothing": "may_support_skin_comfort",
    "skin_conditioning": "supports_a_conditioned_skin_feel",
    "hair_removal_active": "supports_cosmetic_hair_removal",
}
CAUTION_BY_FLAG = {
    "fragrance": ["fragrance_related"],
    "fragrance_allergen": ["fragrance_related", "sensitization_potential"],
    "exfoliating_acid": ["exfoliating_active"],
}


def attach_ingredient_knowledge(
    normalized: NormalizeIngredientsResponse,
    *,
    cache_resolver: KnowledgeResolver,
    cosing_resolver: KnowledgeResolver,
    researcher: KnowledgeResearcher | None = None,
    cache_writer: KnowledgeCacheWriter | None = None,
    market: str = "TR",
) -> IngredientKnowledgeResponse:
    canonical_names = list(dict.fromkeys(
        item.canonical_name
        for formula in normalized.formulas
        for item in formula.ingredients
        if item.canonical_name is not None
    ))

    resolved = cache_resolver(ingredients=canonical_names) if canonical_names else {}
    missing = [name for name in canonical_names if normalize_text(name) not in resolved]

    cosing_queries = [COSING_QUERY_ALIASES.get(normalize_text(name), name) for name in missing]
    cosing_items = cosing_resolver(ingredients=cosing_queries) if cosing_queries else {}
    for canonical_name, query_name in zip(missing, cosing_queries, strict=True):
        item = cosing_items.get(normalize_text(query_name))
        if item and item.evidence_status == "verified":
            resolved[normalize_text(canonical_name)] = item

    still_missing = [name for name in canonical_names if normalize_text(name) not in resolved]
    researched_items: list[IngredientAnalysisItem] = []
    if researcher and still_missing:
        research = researcher(
            AnalyzeIngredientsRequest(ingredients=still_missing, market=market)
        )
        for item in research.items:
            if item.evidence_status == "verified":
                resolved[normalize_text(item.input_name)] = item
                researched_items.append(item)
        if researched_items and cache_writer:
            cache_writer(items=researched_items, source="researched")

    formulas: list[KnowledgeEnrichedFormula] = []
    unresolved_count = 0
    for formula in normalized.formulas:
        enriched: list[KnowledgeEnrichedIngredient] = []
        for ingredient in formula.ingredients:
            item = (
                resolved.get(normalize_text(ingredient.canonical_name))
                if ingredient.canonical_name
                else None
            )
            if ingredient.canonical_name is None:
                record = _unresolved_record(None, "unresolved")
            elif item is None or item.evidence_status != "verified":
                record = _unresolved_record(ingredient.canonical_name, "unverified")
            else:
                record = _record_from_item(ingredient.canonical_name, item)
            if record.evidence_status != "verified":
                unresolved_count += 1
            enriched.append(KnowledgeEnrichedIngredient(normalized=ingredient, knowledge=record))
        formulas.append(
            KnowledgeEnrichedFormula(
                name=formula.name,
                raw_text=formula.raw_text,
                ingredients=enriched,
            )
        )

    return IngredientKnowledgeResponse(
        provenance=normalized.provenance,
        formulas=formulas,
        unresolved_count=unresolved_count,
        paid_fallback_count=len(still_missing) if researcher else 0,
    )


def _record_from_item(
    canonical_name: str,
    item: IngredientAnalysisItem,
) -> IngredientKnowledgeRecord:
    functions = [function for function in item.functions if function != "unknown"]
    properties = list(dict.fromkeys(
        PROPERTY_BY_FUNCTION[function]
        for function in functions
        if function in PROPERTY_BY_FUNCTION
    ))
    cautions = list(dict.fromkeys(
        caution
        for flag in item.flags
        for caution in CAUTION_BY_FLAG.get(flag, [])
    ))
    return IngredientKnowledgeRecord(
        canonical_name=canonical_name,
        functions=functions,
        cosmetic_properties=properties,
        caution_tags=cautions,
        evidence_status="verified",
        source=_source(item),
        source_references=item.source_urls,
        last_verified_at=item.last_verified_at,
    )


def _unresolved_record(
    canonical_name: str | None,
    evidence_status: str,
) -> IngredientKnowledgeRecord:
    return IngredientKnowledgeRecord(
        canonical_name=canonical_name,
        functions=[],
        cosmetic_properties=[],
        caution_tags=[],
        evidence_status=evidence_status,
        source="unresolved",
        source_references=[],
    )


def _source(item: IngredientAnalysisItem) -> str:
    if item.knowledge_source:
        return item.knowledge_source
    if any("europa.eu" in url for url in item.source_urls):
        return "cosing"
    return "trusted_enrichment"
