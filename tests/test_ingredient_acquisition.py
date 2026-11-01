import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.ingredient_acquisition import (
    OnlineIngredientModelOutput,
    PackageFormulaModelOutput,
    PackageIngredientModelOutput,
)
from app.services.ingredient_acquisition import acquire_ingredients
from app.services import ingredient_acquisition
from app.services.openai_usage import begin_usage_capture, finish_usage_capture


def citation_response(*, parsed, url: str | None = None):
    annotations = []
    if url:
        annotations.append(SimpleNamespace(type="url_citation", url=url))
    return SimpleNamespace(
        output_parsed=parsed,
        output=[
            SimpleNamespace(
                content=[SimpleNamespace(annotations=annotations)]
            )
        ],
    )


class IngredientAcquisitionServiceTests(unittest.TestCase):
    def _package_result(self, get_client, formulas):
        parsed = PackageIngredientModelOutput(
            raw_text="package declaration",
            formulas=formulas,
            status="complete", uncertain_segments=[], image_quality="good",
        )
        get_client.return_value.responses.parse.return_value = SimpleNamespace(
            output_parsed=parsed
        )
        return acquire_ingredients(
            brand="Test", product_name="Product", variant=None, market="TR",
            images=[(b"image", "image/jpeg")],
        )

    @patch("app.services.ingredient_acquisition._get_client")
    def test_same_formula_in_two_languages_is_deduplicated(self, get_client):
        result = self._package_result(get_client, [
            PackageFormulaModelOutput(name="Ingredients", raw_text="Aqua, Phenoxyethanol, Benzoic Acid, Citric Acid", ingredients=["Aqua", "Phenoxyethanol", "Benzoic Acid", "Citric Acid"], status="complete", uncertain_segments=[]),
            PackageFormulaModelOutput(name="Ingredientes", raw_text="Aqua, Fenoxietanol, Acido Benzoico, Acido Citrico", ingredients=["Aqua", "Fenoxietanol", "Acido Benzoico", "Acido Citrico"], status="complete", uncertain_segments=[]),
        ])

        self.assertEqual(len(result.formulas), 1)
        self.assertEqual(result.formulas[0].ingredients[1], "Phenoxyethanol")
        self.assertIn("duplicate_formula_sections_removed", result.warnings)

    @patch("app.services.ingredient_acquisition._get_client")
    def test_exact_duplicate_formula_is_deduplicated(self, get_client):
        formula = PackageFormulaModelOutput(name="Ingredients", raw_text="Aqua, Glycerin, Parfum", ingredients=["Aqua", "Glycerin", "Parfum"], status="complete", uncertain_segments=[])
        result = self._package_result(get_client, [formula, formula])

        self.assertEqual(len(result.formulas), 1)
        self.assertEqual(result.formulas[0].ingredients, ["Aqua", "Glycerin", "Parfum"])

    @patch("app.services.ingredient_acquisition._get_client")
    def test_slightly_noisy_duplicate_is_deduplicated_when_overlap_is_high(self, get_client):
        result = self._package_result(get_client, [
            PackageFormulaModelOutput(name="A", raw_text="Aqua, Phenoxyethanol, Benzoic Acid, Parfum, Citric Acid", ingredients=["Aqua", "Phenoxyethanol", "Benzoic Acid", "Parfum", "Citric Acid"], status="complete", uncertain_segments=[]),
            PackageFormulaModelOutput(name="B", raw_text="Aqua, Fenoxietanol, Acido Benzoico, Citric Acid", ingredients=["Aqua", "Fenoxietanol", "Acido Benzoico", "Citric Acid"], status="partial", uncertain_segments=[]),
        ])

        self.assertEqual(len(result.formulas), 1)
        self.assertEqual(result.formulas[0].name, "A")

    @patch("app.services.ingredient_acquisition._get_client")
    def test_genuinely_different_formulas_are_not_merged(self, get_client):
        result = self._package_result(get_client, [
            PackageFormulaModelOutput(name="Cream", raw_text="Aqua, Cetearyl Alcohol, Potassium Thioglycolate", ingredients=["Aqua", "Cetearyl Alcohol", "Potassium Thioglycolate"], status="complete", uncertain_segments=[]),
            PackageFormulaModelOutput(name="Balm", raw_text="Aqua, Dimethicone, Bisabolol", ingredients=["Aqua", "Dimethicone", "Bisabolol"], status="complete", uncertain_segments=[]),
        ])

        self.assertEqual(len(result.formulas), 2)
        self.assertEqual([formula.name for formula in result.formulas], ["Cream", "Balm"])

    @patch("app.services.ingredient_acquisition._get_client")
    def test_concatenated_duplicates_preserve_selected_order_without_extra_ai_call(self, get_client):
        result = self._package_result(get_client, [
            PackageFormulaModelOutput(
                name="Wipes",
                raw_text="Aqua, Phenoxyethanol, Benzoic Acid, Citric Acid / Aqua, Fenoxietanol, Acido Benzoico, Acido Citrico",
                ingredients=["Aqua", "Phenoxyethanol", "Benzoic Acid", "Citric Acid", "Aqua", "Fenoxietanol", "Acido Benzoico", "Acido Citrico"],
                status="complete", uncertain_segments=[],
            )
        ])

        self.assertEqual(result.formulas[0].ingredients, ["Aqua", "Phenoxyethanol", "Benzoic Acid", "Citric Acid"])
        self.assertEqual(get_client.return_value.responses.parse.call_count, 1)
        self.assertNotIn("tools", get_client.return_value.responses.parse.call_args.kwargs)

    @patch("app.services.ingredient_acquisition._get_client")
    def test_repairs_strongly_justified_line_break_hyphen(self, get_client):
        parsed = PackageIngredientModelOutput(
            raw_text="Aqua, Ethylhexylglyce-\nrin, Bisabolol",
            formulas=[PackageFormulaModelOutput(
                name="Balm",
                raw_text="Aqua, Ethylhexylglyce-\nrin, Bisabolol",
                ingredients=["Aqua", "Ethylhexylglyce-\nrin", "Bisabolol"],
                status="complete",
                uncertain_segments=[],
            )],
            status="complete", uncertain_segments=[], image_quality="good",
        )
        get_client.return_value.responses.parse.return_value = SimpleNamespace(
            output_parsed=parsed
        )

        result = acquire_ingredients(
            brand="Test", product_name="Balm", variant=None, market="TR",
            images=[(b"image", "image/jpeg")],
        )

        self.assertEqual(
            result.ingredients,
            ["Aqua", "Ethylhexylglycerin", "Bisabolol"],
        )

    @patch("app.services.ingredient_acquisition._get_client")
    def test_package_images_take_priority(self, get_client):
        parsed = PackageIngredientModelOutput(
            raw_text="Aqua, Glycerin",
            formulas=[PackageFormulaModelOutput(name=None, raw_text="Aqua, Glycerin", ingredients=["Aqua", "Glycerin"], status="complete", uncertain_segments=[])],
            status="complete",
            uncertain_segments=[],
            image_quality="good",
        )
        parse = MagicMock(return_value=SimpleNamespace(
            output_parsed=parsed,
            output=[],
            usage=SimpleNamespace(input_tokens=3000, output_tokens=120),
        ))
        get_client.return_value.responses.parse = parse

        token = begin_usage_capture()
        try:
            result = acquire_ingredients(
                brand="Test",
                product_name="Cream",
                variant=None,
                market="TR",
                images=[(b"image-1", "image/jpeg"), (b"image-2", "image/jpeg")],
            )
        finally:
            usage = finish_usage_capture(token)

        self.assertEqual(result.source.type, "package")
        self.assertEqual(result.ingredients, ["Aqua", "Glycerin"])
        self.assertNotIn("tools", parse.call_args.kwargs)
        self.assertNotIn("max_tool_calls", parse.call_args.kwargs)
        self.assertEqual(parse.call_count, 1)
        image_parts = [
            part
            for part in parse.call_args.kwargs["input"][0]["content"]
            if part["type"] == "input_image"
        ]
        self.assertEqual(len(image_parts), 2)
        self.assertTrue(all(part["detail"] == "high" for part in image_parts))
        self.assertEqual(usage["calls"][0]["stage"], "ingredient_extraction")
        self.assertEqual(usage["calls"][0]["model"], "gpt-5.4-mini")
        self.assertEqual(usage["calls"][0]["image_count"], 2)
        self.assertEqual(usage["calls"][0]["input_tokens"], 3000)
        self.assertEqual(usage["calls"][0]["output_tokens"], 120)
        self.assertEqual(usage["calls"][0]["web_search_calls"], 0)
        self.assertGreater(usage["calls"][0]["estimated_usd"], 0)

    @patch("app.services.ingredient_acquisition._get_client")
    def test_package_removes_formula_code_and_separator_punctuation(self, get_client):
        parsed = PackageIngredientModelOutput(
            raw_text="BHT, Parfum, CI 77891. [PR-0002799]",
            formulas=[PackageFormulaModelOutput(name="Lip Care", raw_text="BHT, Parfum, CI 77891. [PR-0002799]", ingredients=["BHT", "Parfum", "CI 77891.", "[PR-0002799]"], status="complete", uncertain_segments=[])],
            status="complete",
            uncertain_segments=[],
            image_quality="usable",
        )
        get_client.return_value.responses.parse.return_value = SimpleNamespace(
            output_parsed=parsed
        )

        result = acquire_ingredients(
            brand="Test",
            product_name="Lip Care",
            variant=None,
            market="TR",
            images=[(b"image", "image/jpeg")],
        )

        self.assertEqual(result.ingredients, ["BHT", "Parfum", "CI 77891"])
        self.assertIn("[PR-0002799]", result.uncertain_segments)
        self.assertIn("non_ingredient_label_code_removed", result.warnings)

    @patch("app.services.ingredient_acquisition._get_client")
    def test_partial_package_excludes_uncertain_ingredients_and_requests_photo(self, get_client):
        parsed = PackageIngredientModelOutput(
            raw_text="Aqua, Glycerin, unclear word",
            formulas=[PackageFormulaModelOutput(name="Wipes", raw_text="Aqua, Glycerin, unclear word", ingredients=["Aqua", "Glycerin", "Hydroxy Citronellal"], status="partial", uncertain_segments=["Hydroxy Citronellal"])],
            status="partial",
            uncertain_segments=[],
            image_quality="usable",
        )
        get_client.return_value.responses.parse.return_value = SimpleNamespace(
            output_parsed=parsed
        )

        result = acquire_ingredients(
            brand="Test",
            product_name="Wipes",
            variant=None,
            market="TR",
            images=[(b"image", "image/jpeg")],
        )

        self.assertEqual(result.ingredients, ["Aqua", "Glycerin"])
        self.assertTrue(result.needs_package_photo)
        self.assertIn("uncertain_ingredients_removed", result.warnings)

    @patch("app.services.ingredient_acquisition._get_client")
    def test_multiple_package_formulas_are_not_merged(self, get_client):
        parsed = PackageIngredientModelOutput(
            raw_text="Cream: Aqua. Balm: Glycerin.",
            formulas=[
                PackageFormulaModelOutput(name="Cream", raw_text="Aqua, Cetearyl Alcohol", ingredients=["Aqua", "Cetearyl Alcohol"], status="complete", uncertain_segments=[]),
                PackageFormulaModelOutput(name="Balm", raw_text="Glycerin, Aqua", ingredients=["Glycerin", "Aqua"], status="complete", uncertain_segments=[]),
            ],
            status="complete",
            uncertain_segments=[],
            image_quality="good",
        )
        get_client.return_value.responses.parse.return_value = SimpleNamespace(output_parsed=parsed)

        result = acquire_ingredients(brand="Test", product_name="Kit", variant=None, market="TR", images=[(b"image", "image/jpeg")])

        self.assertEqual(result.ingredients, [])
        self.assertEqual([formula.name for formula in result.formulas], ["Cream", "Balm"])
        self.assertEqual(result.formulas[0].ingredients, ["Aqua", "Cetearyl Alcohol"])
        self.assertEqual(result.formulas[1].ingredients, ["Glycerin", "Aqua"])
        self.assertIn("multiple_formulas_detected", result.warnings)

    @patch("app.services.ingredient_acquisition._get_client")
    def test_rejects_front_label_words_as_ingredients(self, get_client):
        parsed = PackageIngredientModelOutput(
            raw_text="NOURISHING INTENSIVE CARE CREAM",
            formulas=[
                PackageFormulaModelOutput(
                    name="NEW FORMULA",
                    raw_text="NOURISHING INTENSIVE CARE CREAM",
                    ingredients=["NOURISHING", "INTENSIVE", "CARE", "CREAM"],
                    status="partial",
                    uncertain_segments=[],
                )
            ],
            status="partial",
            uncertain_segments=[],
            image_quality="good",
        )
        get_client.return_value.responses.parse.return_value = SimpleNamespace(output_parsed=parsed)

        result = acquire_ingredients(brand="Test", product_name="Cream", variant=None, market="TR", images=[(b"image", "image/jpeg")])

        self.assertEqual(result.ingredients, [])
        self.assertEqual(result.formulas, [])
        self.assertTrue(result.needs_package_photo)
        self.assertIn("non_ingredient_text_rejected", result.warnings)

    @patch("app.services.ingredient_acquisition._get_client")
    def test_accepts_exact_online_result_only_when_url_is_cited(self, get_client):
        url = "https://brand.example/products/cream"
        parsed = OnlineIngredientModelOutput(
            matched_brand="Test",
            matched_product_name="Cream",
            matched_variant=None,
            match_quality="exact",
            source_type="official_online",
            source_url=url,
            source_title="Test Cream",
            raw_text="Aqua, Glycerin",
            ingredients=["Aqua", "Glycerin"],
            status="complete",
            uncertain_segments=[],
        )
        parse = MagicMock(return_value=citation_response(parsed=parsed, url=url))
        get_client.return_value.responses.parse = parse

        result = acquire_ingredients(
            brand="Test",
            product_name="Cream",
            variant=None,
            market="TR",
            images=[],
        )

        self.assertEqual(result.source.type, "official_online")
        self.assertFalse(result.needs_package_photo)
        self.assertEqual(parse.call_args.kwargs["tools"], [{"type": "web_search"}])

    @patch("app.services.ingredient_acquisition._get_client")
    def test_rejects_uncited_online_result(self, get_client):
        parsed = OnlineIngredientModelOutput(
            matched_brand="Test",
            matched_product_name="Cream",
            matched_variant=None,
            match_quality="exact",
            source_type="official_online",
            source_url="https://uncited.example/cream",
            source_title="Test Cream",
            raw_text="Aqua, Glycerin",
            ingredients=["Aqua", "Glycerin"],
            status="complete",
            uncertain_segments=[],
        )
        get_client.return_value.responses.parse.return_value = citation_response(
            parsed=parsed,
            url="https://different.example/cream",
        )

        result = acquire_ingredients(
            brand="Test",
            product_name="Cream",
            variant=None,
            market="TR",
            images=[],
        )

        self.assertEqual(result.status, "not_found")
        self.assertEqual(result.ingredients, [])
        self.assertTrue(result.needs_package_photo)

    @patch("app.services.ingredient_acquisition._get_client")
    def test_repeated_online_miss_uses_negative_cache(self, get_client):
        ingredient_acquisition._NEGATIVE_LOOKUP_CACHE.clear()
        parsed = OnlineIngredientModelOutput(
            matched_brand=None,
            matched_product_name=None,
            matched_variant=None,
            match_quality="none",
            source_type="none",
            source_url=None,
            source_title=None,
            raw_text=None,
            ingredients=[],
            status="not_found",
            uncertain_segments=[],
        )
        parse = MagicMock(return_value=citation_response(parsed=parsed))
        get_client.return_value.responses.parse = parse

        first = acquire_ingredients(
            brand="Cache Test Brand",
            product_name="Missing Barcode Product",
            variant=None,
            market="TR",
            images=[],
        )
        second = acquire_ingredients(
            brand="Cache Test Brand",
            product_name="Missing Barcode Product",
            variant=None,
            market="TR",
            images=[],
        )

        self.assertEqual(first.status, "not_found")
        self.assertEqual(second.status, "not_found")
        self.assertEqual(parse.call_count, 1)


class IngredientAcquisitionRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_rejects_more_than_three_images(self):
        files = [
            ("images", (f"label-{index}.jpg", b"image", "image/jpeg"))
            for index in range(4)
        ]
        response = self.client.post(
            "/v1/acquire-ingredients",
            data={"brand": "Test", "product_name": "Cream", "market": "TR"},
            files=files,
        )

        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
