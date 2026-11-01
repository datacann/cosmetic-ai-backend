from collections.abc import Callable
import re
import unicodedata

from app.models.compatibility_analysis import (
    AnalyzeCompatibilityRequest,
    CompatibilityIngredient,
    CompatibilityProduct,
    CompatibilityRoutineProduct,
    CompatibilitySkinProfile,
)
from app.models.compatibility_scoring import ScoreCompatibilityRequest
from app.models.full_analysis import (
    FormulaAnalysisResult,
    FullProductAnalysisResponse,
)
from app.models.ingredient_analysis import AnalyzeIngredientsRequest
from app.models.ingredient_analysis import AnalyzeIngredientsResponse
from app.models.ingredient_analysis import IngredientAnalysisItem
from app.models.product_recognition import ProductRecognitionResult
from app.models.ingredient_acquisition import IngredientAcquisitionResponse
from app.services.compatibility_analysis import analyze_compatibility
from app.services.compatibility_scoring import score_compatibility
from app.services.ingredient_acquisition import acquire_ingredients
from app.services.ingredient_analysis import analyze_ingredients
from app.services.product_recognition import recognize_product


def run_full_analysis(
    *,
    product_image: tuple[bytes, str] | None,
    ingredient_images: list[tuple[bytes, str]],
    skin_profile: CompatibilitySkinProfile,
    routine: list[CompatibilityRoutineProduct],
    market: str,
    ingredient_cache_lookup: Callable[..., IngredientAcquisitionResponse | None] | None = None,
    ingredient_analysis_cache_lookup: Callable[..., AnalyzeIngredientsResponse | None] | None = None,
    ingredient_item_cache_lookup: Callable[..., dict[str, IngredientAnalysisItem]] | None = None,
    progress_callback: Callable[[str], None] | None = None,
    recognized_product: ProductRecognitionResult | None = None,
    acquired_ingredients: IngredientAcquisitionResponse | None = None,
) -> FullProductAnalysisResponse:
    report = progress_callback or (lambda _stage: None)
    if recognized_product is None:
        if product_image is None:
            raise ValueError("Ürün görseli veya tanınmış ürün bilgisi gereklidir.")
        report("recognizing_product")
        product = recognize_product(
            image_bytes=product_image[0],
            content_type=product_image[1],
        )
    else:
        product = recognized_product
    if product.status != "recognized" or not product.brand or not product.product_name:
        return FullProductAnalysisResponse(
            status="needs_input",
            product=product,
            formulas=[],
            warnings=["product_recognition_requires_user_input"],
        )

    report("acquiring_ingredients")
    acquisition = acquired_ingredients
    # A fresh package photo is stronger evidence than a cached formula and can
    # reveal a reformulation. Cache is only used without package evidence.
    if acquisition is None and ingredient_cache_lookup is not None and not ingredient_images:
        acquisition = ingredient_cache_lookup(
            brand=product.brand,
            product_name=product.product_name,
            variant=product.variant,
            market=market,
        )
    if acquisition is None:
        acquisition = acquire_ingredients(
            brand=product.brand,
            product_name=product.product_name,
            variant=product.variant,
            market=market,
            images=ingredient_images,
        )
    if acquisition.status != "complete" or not acquisition.formulas:
        return FullProductAnalysisResponse(
            status="needs_input",
            product=product,
            ingredient_acquisition=acquisition,
            formulas=[
                FormulaAnalysisResult(
                    formula_name=formula.name,
                    status="blocked",
                    ingredients=formula.ingredients,
                    blockers=["complete_ingredient_list_required"],
                )
                for formula in acquisition.formulas
            ],
            warnings=[
                *acquisition.warnings,
                "ingredient_acquisition_requires_user_input",
            ],
        )

    formula_results = []
    warnings = list(acquisition.warnings)
    for formula in acquisition.formulas:
        report("analyzing_ingredients")
        knowledge = None
        if ingredient_analysis_cache_lookup is not None:
            knowledge = ingredient_analysis_cache_lookup(
                brand=product.brand,
                product_name=product.product_name,
                variant=product.variant,
                market=market,
                formula_name=formula.name,
                ingredients=formula.ingredients,
            )
        if knowledge is None:
            if ingredient_item_cache_lookup is None:
                knowledge = analyze_ingredients(
                    AnalyzeIngredientsRequest(
                        formula_name=formula.name,
                        ingredients=formula.ingredients,
                        market=market,
                    )
                )
            else:
                knowledge = _analyze_with_item_cache(
                    formula_name=formula.name,
                    ingredients=formula.ingredients,
                    market=market,
                    cache_lookup=ingredient_item_cache_lookup,
                )
        else:
            warnings.append("ingredient_analysis_loaded_from_cache")
        warnings.extend(knowledge.warnings)
        compatibility_product = CompatibilityProduct(
            brand=product.brand,
            product_name=product.product_name,
            category=product.category,
            formula_name=formula.name,
        )
        report("matching_profile")
        compatibility = analyze_compatibility(
            AnalyzeCompatibilityRequest(
                product=compatibility_product,
                ingredients=[
                    CompatibilityIngredient(
                        input_name=item.input_name,
                        canonical_name=item.canonical_name,
                        functions=item.functions,
                        flags=item.flags,
                        evidence_status=item.evidence_status,
                    )
                    for item in knowledge.items
                ],
                skin_profile=skin_profile,
                routine=routine,
            )
        )
        report("scoring")
        scoring = score_compatibility(
            ScoreCompatibilityRequest(
                product=compatibility_product,
                compatibility=compatibility.compatibility,
                avoid_matches=compatibility.avoid_matches,
            )
        )
        formula_status = "complete" if knowledge.status == "complete" else "partial"
        if formula_status == "partial":
            warnings.append("formula_contains_unverified_ingredients")
        formula_results.append(
            FormulaAnalysisResult(
                formula_name=formula.name,
                status=formula_status,
                ingredients=formula.ingredients,
                ingredient_analysis=knowledge,
                compatibility=compatibility,
                scoring=scoring,
                blockers=[],
            )
        )

    overall_status = (
        "complete"
        if all(result.status == "complete" for result in formula_results)
        else "partial"
    )
    return FullProductAnalysisResponse(
        status=overall_status,
        product=product,
        ingredient_acquisition=acquisition,
        formulas=formula_results,
        warnings=list(dict.fromkeys(warnings)),
    )


def _analyze_with_item_cache(
    *,
    formula_name: str | None,
    ingredients: list[str],
    market: str,
    cache_lookup: Callable[..., dict[str, IngredientAnalysisItem]] | None,
) -> AnalyzeIngredientsResponse:
    cached = cache_lookup(ingredients=ingredients) if cache_lookup else {}
    missing = [name for name in ingredients if _normalize_ingredient_name(name) not in cached]
    researched = None
    if missing:
        researched = analyze_ingredients(
            AnalyzeIngredientsRequest(
                formula_name=formula_name,
                ingredients=missing,
                market=market,
            )
        )
    researched_by_name = {
        _normalize_ingredient_name(item.input_name): item
        for item in (researched.items if researched else [])
    }
    items = [
        cached.get(_normalize_ingredient_name(name))
        or researched_by_name.get(_normalize_ingredient_name(name))
        for name in ingredients
    ]
    final_items = [item for item in items if item is not None]
    unknown = [item.input_name for item in final_items if item.evidence_status != "verified"]
    warnings = list(researched.warnings if researched else [])
    if cached:
        warnings.append("ingredient_items_loaded_from_cache")
    return AnalyzeIngredientsResponse(
        formula_name=formula_name,
        status="complete" if not unknown and len(final_items) == len(ingredients) else "partial",
        items=final_items,
        unknown_ingredients=unknown,
        warnings=list(dict.fromkeys(warnings)),
        disclaimer=(researched.disclaimer if researched else (
            "Bu çıktı kozmetik içerik bilgisidir; tıbbi tanı, tedavi veya güvenlik garantisi değildir."
        )),
    )


def _normalize_ingredient_name(value: str) -> str:
    folded = unicodedata.normalize(
        "NFKD", value.casefold().translate(str.maketrans({"ı": "i"}))
    )
    ascii_text = "".join(char for char in folded if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
