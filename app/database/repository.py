from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.database.models import (
    Analysis,
    AnalysisFormulaResult,
    FormulaIngredient,
    Ingredient,
    Product,
    ProductFormula,
)
from app.models.compatibility_analysis import (
    CompatibilityRoutineProduct,
    CompatibilitySkinProfile,
)
from app.models.full_analysis import FullProductAnalysisResponse
from app.models.product_recognition import ProductRecognitionResult
from app.models.analysis_orchestration import EndToEndAnalysisResponse
from app.models.ingredient_analysis import AnalyzeIngredientsResponse, IngredientAnalysisItem
from app.models.ingredient_acquisition import (
    IngredientAcquisitionResponse,
    IngredientFormula,
    IngredientSource,
)


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    turkish_folded = value.casefold().translate(str.maketrans({"ı": "i"}))
    folded = unicodedata.normalize("NFKD", turkish_folded)
    ascii_text = "".join(char for char in folded if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")


def product_key(*, brand: str, product_name: str, variant: str | None, market: str) -> str:
    return "|".join(
        [normalize_text(brand), normalize_text(product_name), normalize_text(variant), market.upper()]
    )


def normalize_barcode(value: str | None) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"[\s-]+", "", value)
    if not normalized.isdigit() or not 8 <= len(normalized) <= 14:
        return None
    return normalized


class CosmeticRepository:
    def __init__(self, session: Session):
        self.session = session

    def find_cached_acquisition(
        self,
        *,
        brand: str,
        product_name: str,
        variant: str | None,
        market: str,
    ) -> IngredientAcquisitionResponse | None:
        key = product_key(
            brand=brand,
            product_name=product_name,
            variant=variant,
            market=market,
        )
        product = self.session.scalar(
            select(Product)
            .where(Product.normalized_key == key)
            .options(
                selectinload(Product.formulas)
                .selectinload(ProductFormula.ingredient_links)
                .selectinload(FormulaIngredient.ingredient)
            )
        )
        if product is None or not product.formulas:
            return None

        latest_by_name: dict[str, ProductFormula] = {}
        for formula in product.formulas:
            current = latest_by_name.get(formula.formula_name)
            if current is None or formula.formula_version > current.formula_version:
                latest_by_name[formula.formula_name] = formula
        latest = list(latest_by_name.values())
        if not latest or any(formula.status != "complete" for formula in latest):
            return None

        formulas = [
            IngredientFormula(
                name=None if formula.formula_name == "Ana formül" else formula.formula_name,
                raw_text=formula.raw_text,
                ingredients=[link.raw_name for link in formula.ingredient_links],
                status="complete",
                uncertain_segments=[],
            )
            for formula in sorted(latest, key=lambda item: item.formula_name)
        ]
        flattened = formulas[0].ingredients if len(formulas) == 1 else []
        return IngredientAcquisitionResponse(
            status="complete",
            source=IngredientSource(
                type="database",
                match_quality="exact",
                market=market.upper(),
                retrieved_at=datetime.now(timezone.utc),
            ),
            raw_text=formulas[0].raw_text if len(formulas) == 1 else None,
            ingredients=flattened,
            formulas=formulas,
            uncertain_segments=[],
            image_quality=None,
            needs_package_photo=False,
            warnings=["ingredients_loaded_from_cache"],
        )

    def save_product_acquisition(
        self,
        *,
        product: ProductRecognitionResult,
        acquisition: IngredientAcquisitionResponse,
        market: str,
    ) -> None:
        """Cache a complete, source-backed formula without creating an analysis row."""
        if (
            acquisition.status != "complete"
            or not acquisition.formulas
            or not product.brand
            or not product.product_name
        ):
            return
        key = product_key(
            brand=product.brand,
            product_name=product.product_name,
            variant=product.variant,
            market=market,
        )
        stored = self.session.scalar(select(Product).where(Product.normalized_key == key))
        if stored is None:
            stored = Product(
                brand=product.brand,
                product_name=product.product_name,
                variant=product.variant,
                category=product.category,
                market=market.upper(),
                normalized_key=key,
            )
            self.session.add(stored)
            self.session.flush()
        for formula in acquisition.formulas:
            self._get_or_create_formula(
                product=stored,
                formula_name=formula.name or "Ana formül",
                ingredients=formula.ingredients,
                status=formula.status,
                source_type=acquisition.source.type,
                source_url=acquisition.source.url,
                source_accessed_at=acquisition.source.retrieved_at,
                raw_text=formula.raw_text,
                analysis_result=None,
            )
        self.session.commit()

    def save_orchestrated_analysis(
        self,
        *,
        response: EndToEndAnalysisResponse,
        market: str,
        user_id: str | None,
    ) -> str | None:
        """Persist the canonical pipeline result using the existing analysis tables."""
        if response.outcome != "success" or response.product is None or response.user_context is None:
            return None
        key = product_key(
            brand=response.product.brand or "",
            product_name=response.product.product_name or "",
            variant=response.product.variant,
            market=market,
        )
        product = self.session.scalar(
            select(Product)
            .where(Product.normalized_key == key)
            .options(selectinload(Product.formulas))
        )
        if product is None:
            return None
        analysis = Analysis(
            user_id=user_id,
            product_id=product.id,
            skin_profile_snapshot=response.user_context.model_dump(mode="json"),
            routine_snapshot={
                "morning": [item.model_dump(mode="json") for item in response.user_context.routine.morning],
                "evening": [item.model_dump(mode="json") for item in response.user_context.routine.evening],
            },
            status="complete",
        )
        self.session.add(analysis)
        self.session.flush()
        compatibility_by_name = {
            item.formula_name or "Ana formül": item
            for item in (response.compatibility.formulas if response.compatibility else [])
        }
        for scored in response.personal_suitability:
            name = scored.formula_name or "Ana formül"
            candidates = [item for item in product.formulas if item.formula_name == name]
            if not candidates:
                continue
            formula = max(candidates, key=lambda item: item.formula_version)
            compatibility = compatibility_by_name.get(name)
            analysis.results.append(AnalysisFormulaResult(
                formula_id=formula.id,
                status="complete",
                score=scored.result.score,
                raw_score=scored.result.base_score,
                verdict=scored.result.verdict,
                coverage=scored.result.analysis_confidence,
                dimensions={
                    "compatibility": compatibility.model_dump(mode="json") if compatibility else None,
                    "scoring": scored.result.model_dump(mode="json"),
                },
                strengths=response.summary.strengths if response.summary else [],
                cautions=response.summary.cautions if response.summary else [],
                avoid_matches=(compatibility.avoid_preferences.matched_avoid_ingredients if compatibility else []),
                warnings=response.compatibility.warnings if response.compatibility else [],
                scoring_version="2.0",
            ))
        self.session.commit()
        return analysis.id

    def find_product_by_barcode(self, barcode: str) -> ProductRecognitionResult | None:
        normalized = normalize_barcode(barcode)
        if normalized is None:
            return None
        product = self.session.scalar(select(Product).where(Product.barcode == normalized))
        if product is None:
            return None
        return ProductRecognitionResult(
            brand=product.brand,
            product_name=product.product_name,
            variant=product.variant,
            category=product.category,
            confidence=1.0,
            model_confidence=1.0,
            status="recognized",
            image_quality="good",
            quality_issues=[],
            brand_evidence="barcode_database_match",
            product_name_evidence="barcode_database_match",
            variant_evidence="barcode_database_match" if product.variant else None,
            needs_retake=False,
            retake_reasons=[],
        )

    def find_product_by_id(self, product_id: str) -> ProductRecognitionResult | None:
        product = self.session.get(Product, product_id)
        if product is None:
            return None
        return ProductRecognitionResult(
            brand=product.brand,
            product_name=product.product_name,
            variant=product.variant,
            category=product.category,
            confidence=1.0,
            model_confidence=1.0,
            status="recognized",
            image_quality="good",
            quality_issues=[],
            brand_evidence="catalog_database_match",
            product_name_evidence="catalog_database_match",
            variant_evidence="catalog_database_match" if product.variant else None,
            needs_retake=False,
            retake_reasons=[],
        )

    def list_catalog_products(
        self, *, query: str | None, category: str | None, limit: int, offset: int
    ) -> tuple[list[Product], int, list[str]]:
        conditions = []
        normalized_query = normalize_text(query)
        if normalized_query:
            conditions.append(Product.normalized_key.contains(normalized_query))
        if category:
            conditions.append(func.lower(Product.category) == category.strip().casefold())
        base = select(Product).where(*conditions)
        total = self.session.scalar(
            select(func.count()).select_from(Product).where(*conditions)
        ) or 0
        products = list(
            self.session.scalars(
                base.options(selectinload(Product.formulas))
                .order_by(Product.brand, Product.product_name)
                .limit(limit)
                .offset(offset)
            )
        )
        categories = list(
            self.session.scalars(
                select(Product.category)
                .where(Product.category.is_not(None))
                .distinct()
                .order_by(Product.category)
            )
        )
        return products, int(total), categories

    def get_catalog_product(self, product_id: str) -> Product | None:
        return self.session.scalar(
            select(Product)
            .where(Product.id == product_id)
            .options(
                selectinload(Product.formulas)
                .selectinload(ProductFormula.ingredient_links)
                .selectinload(FormulaIngredient.ingredient)
            )
        )

    def find_cached_ingredient_analysis(
        self,
        *,
        brand: str,
        product_name: str,
        variant: str | None,
        market: str,
        formula_name: str | None,
        ingredients: list[str],
    ) -> AnalyzeIngredientsResponse | None:
        """Return verified catalog knowledge only for the exact stored formula version."""
        key = product_key(
            brand=brand,
            product_name=product_name,
            variant=variant,
            market=market,
        )
        stored_name = formula_name or "Ana formül"
        product = self.session.scalar(
            select(Product)
            .where(Product.normalized_key == key)
            .options(
                selectinload(Product.formulas)
                .selectinload(ProductFormula.ingredient_links)
                .selectinload(FormulaIngredient.ingredient)
            )
        )
        if product is None:
            return None

        candidates = [
            formula
            for formula in product.formulas
            if formula.formula_name == stored_name and formula.status == "complete"
        ]
        if not candidates:
            return None
        formula = max(candidates, key=lambda item: item.formula_version)
        links = formula.ingredient_links
        if [normalize_text(link.raw_name) for link in links] != [
            normalize_text(item) for item in ingredients
        ]:
            return None
        if any(
            link.verification_status != "verified"
            or not link.evidence_source_urls
            or not link.ingredient.functions
            or "unknown" in link.ingredient.functions
            for link in links
        ):
            return None

        return AnalyzeIngredientsResponse(
            formula_name=formula_name,
            status="complete",
            items=[
                IngredientAnalysisItem(
                    input_name=link.raw_name,
                    canonical_name=link.ingredient.inci_name,
                    functions=link.ingredient.functions,
                    flags=link.ingredient.flags,
                    note="Doğrulanmış içerik bilgisi ürün kataloğundan yüklendi.",
                    evidence_status="verified",
                    source_urls=link.evidence_source_urls,
                )
                for link in links
            ],
            unknown_ingredients=[],
            warnings=["ingredient_analysis_loaded_from_cache"],
            disclaimer=(
                "Bu çıktı kozmetik içerik bilgisidir; tıbbi tanı, tedavi veya "
                "güvenlik garantisi değildir."
            ),
        )

    def find_cached_ingredient_items(
        self, *, ingredients: list[str]
    ) -> dict[str, IngredientAnalysisItem]:
        """Return reusable, source-backed knowledge independently of any product formula."""
        normalized = {normalize_text(name): name for name in ingredients}
        if not normalized:
            return {}
        rows = self.session.scalars(
            select(Ingredient).where(Ingredient.normalized_name.in_(normalized))
        )
        result: dict[str, IngredientAnalysisItem] = {}
        for ingredient in rows:
            if (
                ingredient.evidence_status != "verified"
                or not ingredient.source_urls
                or not ingredient.functions
                or "unknown" in ingredient.functions
            ):
                continue
            input_name = normalized[ingredient.normalized_name]
            result[ingredient.normalized_name] = IngredientAnalysisItem(
                input_name=input_name,
                canonical_name=ingredient.inci_name,
                functions=ingredient.functions,
                flags=ingredient.flags,
                note=ingredient.knowledge_note
                or "Doğrulanmış içerik bilgisi ingredient önbelleğinden yüklendi.",
                evidence_status="verified",
                source_urls=ingredient.source_urls,
                knowledge_source=(
                    "cosing"
                    if ingredient.knowledge_source == "cosing"
                    else "trusted_enrichment"
                    if ingredient.knowledge_source == "researched"
                    else "postgresql"
                ),
                last_verified_at=ingredient.updated_at,
            )
        return result

    def save_cached_ingredient_items(
        self, *, items: list[IngredientAnalysisItem], source: str
    ) -> None:
        for item in items:
            normalized = normalize_text(item.input_name)
            ingredient = self.session.scalar(
                select(Ingredient).where(Ingredient.normalized_name == normalized)
            )
            if ingredient is None:
                ingredient = Ingredient(
                    inci_name=item.canonical_name or item.input_name,
                    normalized_name=normalized,
                )
                self.session.add(ingredient)
            self._store_ingredient_knowledge(ingredient, item)
            ingredient.knowledge_source = source
        self.session.flush()

    def save_full_analysis(
        self,
        *,
        response: FullProductAnalysisResponse,
        skin_profile: CompatibilitySkinProfile,
        routine: Sequence[CompatibilityRoutineProduct],
        market: str,
        user_id: str | None = None,
        barcode: str | None = None,
    ) -> str | None:
        if not response.product.brand or not response.product.product_name:
            return None

        product = self._get_or_create_product(response=response, market=market, barcode=barcode)
        formulas_by_name: dict[str, ProductFormula] = {}
        acquisition = response.ingredient_acquisition
        if acquisition and acquisition.status == "complete":
            results_by_name = {
                (result.formula_name or "Ana formül"): result for result in response.formulas
            }
            for formula_data in acquisition.formulas:
                formula_name = formula_data.name or "Ana formül"
                formula = self._get_or_create_formula(
                    product=product,
                    formula_name=formula_name,
                    ingredients=formula_data.ingredients,
                    status=formula_data.status,
                    source_type=acquisition.source.type,
                    source_url=acquisition.source.url,
                    source_accessed_at=acquisition.source.retrieved_at,
                    raw_text=formula_data.raw_text,
                    analysis_result=results_by_name.get(formula_name),
                )
                formulas_by_name[formula_name] = formula

        if not formulas_by_name:
            self.session.commit()
            return None

        analysis = Analysis(
            user_id=user_id,
            product_id=product.id,
            skin_profile_snapshot=skin_profile.model_dump(mode="json"),
            routine_snapshot=[item.model_dump(mode="json") for item in routine],
            status=response.status,
        )
        self.session.add(analysis)
        self.session.flush()

        for result in response.formulas:
            formula = formulas_by_name.get(result.formula_name or "Ana formül")
            if formula is None:
                continue
            scoring = result.scoring
            compatibility = result.compatibility
            analysis.results.append(
                AnalysisFormulaResult(
                    formula_id=formula.id,
                    status=result.status,
                    score=scoring.score if scoring else None,
                    raw_score=scoring.raw_score if scoring else None,
                    verdict=scoring.verdict if scoring else None,
                    coverage=(scoring.coverage / 100.0) if scoring else None,
                    dimensions=(
                        compatibility.compatibility.model_dump(mode="json")
                        if compatibility
                        else None
                    ),
                    strengths=compatibility.strengths if compatibility else [],
                    cautions=compatibility.cautions if compatibility else [],
                    avoid_matches=compatibility.avoid_matches if compatibility else [],
                    warnings=(compatibility.warnings if compatibility else []),
                    scoring_version="1.0",
                )
            )
        self.session.commit()
        return analysis.id

    def _get_or_create_product(
        self, *, response: FullProductAnalysisResponse, market: str, barcode: str | None = None
    ) -> Product:
        recognized = response.product
        key = product_key(
            brand=recognized.brand or "",
            product_name=recognized.product_name or "",
            variant=recognized.variant,
            market=market,
        )
        product = self.session.scalar(select(Product).where(Product.normalized_key == key))
        if product is None:
            product = Product(
                brand=recognized.brand or "",
                product_name=recognized.product_name or "",
                variant=recognized.variant,
                category=recognized.category,
                market=market.upper(),
                normalized_key=key,
                barcode=normalize_barcode(barcode),
            )
            self.session.add(product)
            self.session.flush()
        elif barcode:
            normalized_barcode = normalize_barcode(barcode)
            if normalized_barcode:
                owner = self.session.scalar(select(Product).where(Product.barcode == normalized_barcode))
                if owner is None or owner.id == product.id:
                    product.barcode = normalized_barcode
        return product

    def _get_or_create_formula(
        self,
        *,
        product: Product,
        formula_name: str,
        ingredients: list[str],
        status: str,
        source_type: str,
        source_url: str | None,
        source_accessed_at: datetime | None,
        raw_text: str | None,
        analysis_result,
    ) -> ProductFormula:
        existing = list(
            self.session.scalars(
                select(ProductFormula)
                .where(
                    ProductFormula.product_id == product.id,
                    ProductFormula.formula_name == formula_name,
                )
                .options(
                    selectinload(ProductFormula.ingredient_links).selectinload(
                        FormulaIngredient.ingredient
                    )
                )
                .order_by(ProductFormula.formula_version.desc())
            )
        )
        normalized_new = [normalize_text(item) for item in ingredients]
        if existing:
            latest = existing[0]
            normalized_existing = [normalize_text(link.raw_name) for link in latest.ingredient_links]
            if normalized_existing == normalized_new:
                self._refresh_formula_knowledge(latest, analysis_result)
                return latest
            next_version = latest.formula_version + 1
        else:
            next_version = 1

        formula = ProductFormula(
            product_id=product.id,
            formula_name=formula_name,
            formula_version=next_version,
            status=status,
            source_type=source_type,
            source_url=source_url,
            source_accessed_at=source_accessed_at,
            raw_text=raw_text,
        )
        self.session.add(formula)
        self.session.flush()

        knowledge_by_input = {}
        if analysis_result and analysis_result.ingredient_analysis:
            knowledge_by_input = {
                normalize_text(item.input_name): item
                for item in analysis_result.ingredient_analysis.items
            }
        for position, raw_name in enumerate(ingredients, start=1):
            normalized_name = normalize_text(raw_name)
            knowledge = knowledge_by_input.get(normalized_name)
            ingredient = self.session.scalar(
                select(Ingredient).where(Ingredient.normalized_name == normalized_name)
            )
            if ingredient is None:
                ingredient = Ingredient(
                    inci_name=(knowledge.canonical_name if knowledge and knowledge.canonical_name else raw_name),
                    normalized_name=normalized_name,
                    functions=knowledge.functions if knowledge else [],
                    flags=knowledge.flags if knowledge else [],
                )
                self.session.add(ingredient)
                self.session.flush()
            elif knowledge and knowledge.evidence_status == "verified":
                ingredient.inci_name = knowledge.canonical_name or ingredient.inci_name
                ingredient.functions = knowledge.functions
                ingredient.flags = knowledge.flags

            self._store_ingredient_knowledge(ingredient, knowledge)

            formula.ingredient_links.append(
                FormulaIngredient(
                    ingredient_id=ingredient.id,
                    position=position,
                    raw_name=raw_name,
                    verification_status=(knowledge.evidence_status if knowledge else "unverified"),
                    evidence_source_urls=(knowledge.source_urls if knowledge else []),
                )
            )
        return formula

    def _refresh_formula_knowledge(self, formula: ProductFormula, analysis_result) -> None:
        if not analysis_result or not analysis_result.ingredient_analysis:
            return
        knowledge_by_input = {
            normalize_text(item.input_name): item
            for item in analysis_result.ingredient_analysis.items
        }
        for link in formula.ingredient_links:
            knowledge = knowledge_by_input.get(normalize_text(link.raw_name))
            if knowledge is None:
                continue
            link.verification_status = knowledge.evidence_status
            link.evidence_source_urls = knowledge.source_urls
            self._store_ingredient_knowledge(link.ingredient, knowledge)

    @staticmethod
    def _store_ingredient_knowledge(ingredient: Ingredient, knowledge) -> None:
        if knowledge is None or knowledge.evidence_status != "verified":
            return
        ingredient.inci_name = knowledge.canonical_name or ingredient.inci_name
        ingredient.functions = knowledge.functions
        ingredient.flags = knowledge.flags
        ingredient.evidence_status = "verified"
        ingredient.source_urls = knowledge.source_urls
        ingredient.knowledge_note = knowledge.note
        ingredient.knowledge_source = (
            "cosing"
            if any("ec.europa.eu" in url or "europa.eu" in url for url in knowledge.source_urls)
            else "researched"
        )
