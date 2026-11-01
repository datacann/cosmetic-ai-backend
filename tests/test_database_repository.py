import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database.base import Base
from app.database.models import Analysis, AnalysisFormulaResult, Ingredient, Product, ProductFormula
from app.database.repository import CosmeticRepository, product_key
from app.models.compatibility_analysis import CompatibilitySkinProfile
from app.models.full_analysis import FormulaAnalysisResult, FullProductAnalysisResponse
from app.models.ingredient_acquisition import IngredientAcquisitionResponse, IngredientFormula, IngredientSource
from app.models.product_recognition import ProductRecognitionResult
from app.models.ingredient_analysis import AnalyzeIngredientsResponse, IngredientAnalysisItem


def analysis_response(ingredients: list[str]) -> FullProductAnalysisResponse:
    formula = IngredientFormula(
        name="Ana Krem", raw_text=", ".join(ingredients), ingredients=ingredients,
        status="complete", uncertain_segments=[],
    )
    return FullProductAnalysisResponse(
        status="partial",
        product=ProductRecognitionResult(
            brand="Test Marka", product_name="Nem Kremi", variant="Hassas",
            category="Moisturizer", confidence=0.98, model_confidence=0.98,
            status="recognized", image_quality="good", quality_issues=[],
            brand_evidence="Test Marka", product_name_evidence="Nem Kremi",
            variant_evidence="Hassas", needs_retake=False, retake_reasons=[],
        ),
        ingredient_acquisition=IngredientAcquisitionResponse(
            status="complete",
            source=IngredientSource(type="package", match_quality="exact", market="TR"),
            raw_text=formula.raw_text, ingredients=ingredients, formulas=[formula],
            uncertain_segments=[], image_quality="good", needs_package_photo=False,
            warnings=[],
        ),
        formulas=[FormulaAnalysisResult(
            formula_name="Ana Krem", status="partial", ingredients=ingredients, blockers=[]
        )],
        warnings=["formula_contains_unverified_ingredients"],
    )


def verified_analysis_response(ingredients: list[str]) -> FullProductAnalysisResponse:
    response = analysis_response(ingredients)
    response.status = "complete"
    response.formulas[0].status = "complete"
    response.formulas[0].ingredient_analysis = AnalyzeIngredientsResponse(
        formula_name="Ana Krem",
        status="complete",
        items=[
            IngredientAnalysisItem(
                input_name=name,
                canonical_name=name,
                functions=["humectant"],
                flags=[],
                note="Test",
                evidence_status="verified",
                source_urls=["https://example.org/ingredient"],
            )
            for name in ingredients
        ],
        unknown_ingredients=[],
        warnings=[],
        disclaimer="Test",
    )
    response.warnings = []
    return response


class DatabaseRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.repository = CosmeticRepository(self.session)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def save(self, ingredients: list[str]):
        return self.repository.save_full_analysis(
            response=analysis_response(ingredients),
            skin_profile=CompatibilitySkinProfile(
                skin_type="Kuru", avoid_ingredients=["Parfum"]
            ),
            routine=[], market="TR",
        )

    def test_persists_catalog_analysis_and_reads_cache(self):
        analysis_id = self.save(["Aqua", "Glycerin", "Parfum"])
        self.assertIsNotNone(analysis_id)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(Product)), 1)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(ProductFormula)), 1)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(Ingredient)), 3)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(Analysis)), 1)

        cached = self.repository.find_cached_acquisition(
            brand="Test Marka", product_name="Nem Kremi", variant="Hassas", market="TR"
        )
        self.assertIsNotNone(cached)
        self.assertEqual(cached.source.type, "database")
        self.assertEqual(cached.formulas[0].ingredients, ["Aqua", "Glycerin", "Parfum"])

    def test_decimal_score_and_unit_interval_confidence_persist_exactly(self):
        analysis_id = self.save(["Aqua", "Glycerin"])
        formula_id = self.session.scalar(select(ProductFormula.id))
        result = AnalysisFormulaResult(
            analysis_id=analysis_id,
            formula_id=formula_id,
            status="complete",
            score=74,
            raw_score=74.2308,
            verdict="Uygun",
            coverage=0.83,
            dimensions={},
        )
        self.session.add(result)
        self.session.commit()
        self.session.expire_all()

        stored = self.session.get(AnalysisFormulaResult, result.id)
        self.assertAlmostEqual(stored.raw_score, 74.2308, places=4)
        self.assertAlmostEqual(stored.coverage, 0.83, places=2)

    def test_changed_formula_creates_new_version_without_duplicating_product(self):
        self.save(["Aqua", "Glycerin"])
        self.save(["Aqua", "Glycerin", "Panthenol"])
        versions = list(self.session.scalars(
            select(ProductFormula).order_by(ProductFormula.formula_version)
        ))
        self.assertEqual(self.session.scalar(select(func.count()).select_from(Product)), 1)
        self.assertEqual([formula.formula_version for formula in versions], [1, 2])

    def test_product_key_is_case_and_turkish_character_insensitive(self):
        first = product_key(
            brand="Ürün Marka", product_name="Yoğun Bakım Kremi", variant=None, market="tr"
        )
        second = product_key(
            brand="urun marka", product_name="YOGUN BAKIM KREMI", variant="", market="TR"
        )
        self.assertEqual(first, second)

    def test_reads_verified_ingredient_knowledge_for_exact_formula_only(self):
        self.repository.save_full_analysis(
            response=verified_analysis_response(["Aqua", "Glycerin"]),
            skin_profile=CompatibilitySkinProfile(skin_type="Kuru"),
            routine=[],
            market="TR",
        )

        cached = self.repository.find_cached_ingredient_analysis(
            brand="Test Marka",
            product_name="Nem Kremi",
            variant="Hassas",
            market="TR",
            formula_name="Ana Krem",
            ingredients=["Aqua", "Glycerin"],
        )
        changed = self.repository.find_cached_ingredient_analysis(
            brand="Test Marka",
            product_name="Nem Kremi",
            variant="Hassas",
            market="TR",
            formula_name="Ana Krem",
            ingredients=["Aqua", "Glycerin", "Panthenol"],
        )

        self.assertIsNotNone(cached)
        self.assertEqual(cached.status, "complete")
        self.assertEqual(cached.items[1].canonical_name, "Glycerin")
        self.assertIsNone(changed)

    def test_reuses_verified_ingredient_knowledge_across_products(self):
        self.repository.save_full_analysis(
            response=verified_analysis_response(["Aqua", "Glycerin"]),
            skin_profile=CompatibilitySkinProfile(skin_type="Kuru"),
            routine=[],
            market="TR",
        )

        cached = self.repository.find_cached_ingredient_items(
            ingredients=["Glycerin", "Panthenol"]
        )

        self.assertEqual(list(cached), ["glycerin"])
        self.assertEqual(cached["glycerin"].functions, ["humectant"])

    def test_barcode_is_normalized_and_resolves_catalog_product(self):
        self.repository.save_full_analysis(
            response=verified_analysis_response(["Aqua", "Glycerin"]),
            skin_profile=CompatibilitySkinProfile(skin_type="Kuru"),
            routine=[],
            market="TR",
            barcode="8690 1234-56789",
        )

        product = self.repository.find_product_by_barcode("8690123456789")

        self.assertIsNotNone(product)
        self.assertEqual(product.brand, "Test Marka")
        self.assertEqual(product.product_name, "Nem Kremi")
        self.assertEqual(product.brand_evidence, "barcode_database_match")

    def test_catalog_search_filters_and_paginates_products(self):
        self.save(["Aqua", "Glycerin"])
        second = Product(
            brand="Başka Marka", product_name="Temizleme Jeli", variant=None,
            category="Cleanser", market="TR", barcode=None,
            normalized_key=product_key(
                brand="Başka Marka", product_name="Temizleme Jeli", variant=None, market="TR"
            ),
        )
        self.session.add(second)
        self.session.commit()

        products, total, categories = self.repository.list_catalog_products(
            query="test marka", category="Moisturizer", limit=20, offset=0
        )

        self.assertEqual(total, 1)
        self.assertEqual(products[0].product_name, "Nem Kremi")
        self.assertIn("Moisturizer", categories)
        self.assertIn("Cleanser", categories)

    def test_catalog_detail_loads_formula_ingredients(self):
        self.save(["Aqua", "Glycerin"])
        stored = self.session.scalar(select(Product))

        detail = self.repository.get_catalog_product(stored.id)

        self.assertIsNotNone(detail)
        self.assertEqual(len(detail.formulas), 1)
        self.assertEqual(
            [link.raw_name for link in detail.formulas[0].ingredient_links],
            ["Aqua", "Glycerin"],
        )


if __name__ == "__main__":
    unittest.main()
