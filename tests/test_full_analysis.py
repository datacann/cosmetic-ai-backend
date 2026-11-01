import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.compatibility_analysis import (
    AnalyzeCompatibilityResponse,
    CompatibilityBreakdownResponse,
    CompatibilityDimensionResult,
    CompatibilityProduct,
    CompatibilitySkinProfile,
)
from app.models.compatibility_scoring import (
    DimensionScore,
    ScoreBreakdown,
    ScoreCompatibilityResponse,
)
from app.models.ingredient_acquisition import (
    IngredientAcquisitionResponse,
    IngredientFormula,
    IngredientSource,
)
from app.models.ingredient_analysis import (
    AnalyzeIngredientsResponse,
    IngredientAnalysisItem,
)
from app.models.product_recognition import ProductRecognitionResult
from app.services.full_analysis import run_full_analysis
from app.services.analysis_progress import claim_progress, update_progress


def recognized_product(status="recognized"):
    return ProductRecognitionResult(
        brand="Test" if status == "recognized" else None,
        product_name="Cream" if status == "recognized" else None,
        category="Moisturizer" if status == "recognized" else None,
        confidence=0.98 if status == "recognized" else 0.0,
        model_confidence=0.98 if status == "recognized" else 0.2,
        status=status,
        image_quality="good" if status == "recognized" else "poor",
        quality_issues=[] if status == "recognized" else ["blur"],
        needs_retake=status != "recognized",
        retake_reasons=[] if status == "recognized" else ["blur", "missing_brand_text", "missing_product_name_text"],
    )


def compatibility_dimension():
    return CompatibilityDimensionResult(
        assessment="neutral",
        explanation="Test",
        evidence_ingredients=[],
        available=True,
    )


class FullAnalysisServiceTests(unittest.TestCase):
    @patch("app.services.full_analysis.score_compatibility")
    @patch("app.services.full_analysis.analyze_compatibility")
    @patch("app.services.full_analysis.analyze_ingredients")
    @patch("app.services.full_analysis.acquire_ingredients")
    @patch("app.services.full_analysis.recognize_product")
    def test_runs_complete_pipeline(self, recognize, acquire, analyze_knowledge, analyze_fit, score):
        recognize.return_value = recognized_product()
        acquire.return_value = IngredientAcquisitionResponse(
            status="complete",
            source=IngredientSource(type="package", match_quality="exact", market="TR"),
            raw_text="Aqua, Glycerin",
            ingredients=["Aqua", "Glycerin"],
            formulas=[IngredientFormula(name="Cream", raw_text="Aqua, Glycerin", ingredients=["Aqua", "Glycerin"], status="complete", uncertain_segments=[])],
            uncertain_segments=[],
            image_quality="good",
            needs_package_photo=False,
            warnings=[],
        )
        analyze_knowledge.return_value = AnalyzeIngredientsResponse(
            formula_name="Cream",
            status="complete",
            items=[IngredientAnalysisItem(input_name="Glycerin", canonical_name="Glycerin", functions=["humectant"], flags=[], note="Test", evidence_status="verified", source_urls=["https://example.org"])],
            unknown_ingredients=[],
            warnings=[],
            disclaimer="Test",
        )
        product = CompatibilityProduct(brand="Test", product_name="Cream", category="Moisturizer", formula_name="Cream")
        breakdown = CompatibilityBreakdownResponse(**{name: compatibility_dimension() for name in ["skin_type", "sensitivity", "goals", "appearance", "routine"]})
        analyze_fit.return_value = AnalyzeCompatibilityResponse(product=product, compatibility=breakdown, strengths=[], cautions=[], avoid_matches=[], warnings=[], disclaimer="Test")
        dimension_score = DimensionScore(assessment="neutral", value=75, configured_weight=20, normalized_weight=0.2, contribution=15, included=True)
        score.return_value = ScoreCompatibilityResponse(product=product, score=75, raw_score=75, verdict="Uygun", coverage=100, breakdown=ScoreBreakdown(**{name: dimension_score for name in ["skin_type", "sensitivity", "goals", "appearance", "routine"]}), applied_rules=[], score_meaning="Test")

        stages = []
        result = run_full_analysis(product_image=(b"front", "image/jpeg"), ingredient_images=[(b"back", "image/jpeg")], skin_profile=CompatibilitySkinProfile(skin_type="Kuru"), routine=[], market="TR", progress_callback=stages.append)

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.formulas[0].scoring.score, 75)
        analyze_knowledge.assert_called_once()
        analyze_fit.assert_called_once()
        score.assert_called_once()
        self.assertEqual(
            stages,
            ["recognizing_product", "acquiring_ingredients", "analyzing_ingredients", "matching_profile", "scoring"],
        )

    @patch("app.services.full_analysis.acquire_ingredients")
    @patch("app.services.full_analysis.recognize_product")
    def test_stops_when_recognition_requires_input(self, recognize, acquire):
        recognize.return_value = recognized_product(status="unrecognized")

        result = run_full_analysis(product_image=(b"front", "image/jpeg"), ingredient_images=[], skin_profile=CompatibilitySkinProfile(), routine=[], market="TR")

        self.assertEqual(result.status, "needs_input")
        acquire.assert_not_called()

    @patch("app.services.full_analysis.score_compatibility")
    @patch("app.services.full_analysis.analyze_compatibility")
    @patch("app.services.full_analysis.analyze_ingredients")
    @patch("app.services.full_analysis.acquire_ingredients")
    @patch("app.services.full_analysis.recognize_product")
    def test_verified_knowledge_cache_skips_paid_ingredient_analysis(
        self, recognize, acquire, analyze_knowledge, analyze_fit, score
    ):
        recognize.return_value = recognized_product()
        acquire.return_value = IngredientAcquisitionResponse(
            status="complete",
            source=IngredientSource(type="database", match_quality="exact", market="TR"),
            raw_text="Aqua",
            ingredients=["Aqua"],
            formulas=[IngredientFormula(name="Cream", raw_text="Aqua", ingredients=["Aqua"], status="complete", uncertain_segments=[])],
            uncertain_segments=[],
            needs_package_photo=False,
            warnings=["ingredients_loaded_from_cache"],
        )
        cached_knowledge = AnalyzeIngredientsResponse(
            formula_name="Cream",
            status="complete",
            items=[IngredientAnalysisItem(input_name="Aqua", canonical_name="Water", functions=["solvent"], flags=[], note="Cache", evidence_status="verified", source_urls=["https://example.org"])],
            unknown_ingredients=[],
            warnings=["ingredient_analysis_loaded_from_cache"],
            disclaimer="Test",
        )
        product = CompatibilityProduct(brand="Test", product_name="Cream", category="Moisturizer", formula_name="Cream")
        breakdown = CompatibilityBreakdownResponse(**{name: compatibility_dimension() for name in ["skin_type", "sensitivity", "goals", "appearance", "routine"]})
        analyze_fit.return_value = AnalyzeCompatibilityResponse(product=product, compatibility=breakdown, strengths=[], cautions=[], avoid_matches=[], warnings=[], disclaimer="Test")
        dimension_score = DimensionScore(assessment="neutral", value=75, configured_weight=20, normalized_weight=0.2, contribution=15, included=True)
        score.return_value = ScoreCompatibilityResponse(product=product, score=75, raw_score=75, verdict="Uygun", coverage=100, breakdown=ScoreBreakdown(**{name: dimension_score for name in ["skin_type", "sensitivity", "goals", "appearance", "routine"]}), applied_rules=[], score_meaning="Test")

        result = run_full_analysis(
            product_image=(b"front", "image/jpeg"),
            ingredient_images=[],
            skin_profile=CompatibilitySkinProfile(skin_type="Kuru"),
            routine=[],
            market="TR",
            ingredient_cache_lookup=lambda **_: acquire.return_value,
            ingredient_analysis_cache_lookup=lambda **_: cached_knowledge,
        )

        analyze_knowledge.assert_not_called()
        self.assertIn("ingredient_analysis_loaded_from_cache", result.warnings)

    @patch("app.services.full_analysis.analyze_ingredients")
    def test_item_cache_researches_only_missing_ingredients(self, analyze_knowledge):
        analyze_knowledge.return_value = AnalyzeIngredientsResponse(
            formula_name="Cream",
            status="complete",
            items=[IngredientAnalysisItem(
                input_name="Panthenol", canonical_name="Panthenol", functions=["soothing"],
                flags=[], note="Research", evidence_status="verified",
                source_urls=["https://example.org/panthenol"],
            )],
            unknown_ingredients=[], warnings=[], disclaimer="Test",
        )
        from app.services.full_analysis import _analyze_with_item_cache

        result = _analyze_with_item_cache(
            formula_name="Cream",
            ingredients=["Aqua", "Panthenol"],
            market="TR",
            cache_lookup=lambda **_: {
                "aqua": IngredientAnalysisItem(
                    input_name="Aqua", canonical_name="Water", functions=["solvent"],
                    flags=[], note="CosIng", evidence_status="verified",
                    source_urls=["https://ec.europa.eu/cosing"],
                )
            },
        )

        self.assertEqual([item.input_name for item in result.items], ["Aqua", "Panthenol"])
        self.assertEqual(analyze_knowledge.call_args.args[0].ingredients, ["Panthenol"])
        self.assertIn("ingredient_items_loaded_from_cache", result.warnings)

    @patch("app.services.full_analysis.analyze_ingredients")
    def test_item_cache_skips_web_research_when_all_ingredients_are_local(self, analyze_knowledge):
        from app.services.full_analysis import _analyze_with_item_cache

        local_items = {
            "aqua": IngredientAnalysisItem(
                input_name="Aqua", canonical_name="Water", functions=["solvent"],
                flags=[], note="CosIng", evidence_status="verified",
                source_urls=["https://ec.europa.eu/cosing"],
            ),
            "glycerin": IngredientAnalysisItem(
                input_name="Glycerin", canonical_name="Glycerin", functions=["humectant"],
                flags=[], note="CosIng", evidence_status="verified",
                source_urls=["https://ec.europa.eu/cosing"],
            ),
        }

        result = _analyze_with_item_cache(
            formula_name="Cream",
            ingredients=["Aqua", "Glycerin"],
            market="TR",
            cache_lookup=lambda **_: local_items,
        )

        analyze_knowledge.assert_not_called()
        self.assertEqual([item.input_name for item in result.items], ["Aqua", "Glycerin"])
        self.assertIn("ingredient_items_loaded_from_cache", result.warnings)


class FullAnalysisRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_rejects_invalid_profile_json(self):
        response = self.client.post(
            "/v1/analyze-product",
            data={"skin_profile": "not-json", "routine": "[]"},
            files={"product_image": ("front.jpg", b"image", "image/jpeg")},
        )

        self.assertEqual(response.status_code, 400)

    def test_reads_analysis_progress(self):
        update_progress("test-progress", "matching_profile")

        response = self.client.get("/v1/analysis-progress/test-progress")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["stage"], "matching_profile")

    def test_progress_id_can_only_be_claimed_once(self):
        progress_id = "analysis-duplicate-test"
        self.assertTrue(claim_progress(progress_id))
        self.assertFalse(claim_progress(progress_id))

    def test_rejects_oversized_product_before_ai(self):
        response = self.client.post(
            "/v1/analyze-product",
            data={"skin_profile": "{}", "routine": "[]"},
            files={"product_image": ("front.jpg", b"x" * (10 * 1024 * 1024 + 1), "image/jpeg")},
        )

        self.assertEqual(response.status_code, 413)
        self.assertIn("10 MB", response.json()["detail"])

    def test_rejects_invalid_barcode_before_database_lookup(self):
        response = self.client.get("/v1/products/barcode/not-a-barcode")

        self.assertEqual(response.status_code, 400)
        self.assertIn("8–14", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
