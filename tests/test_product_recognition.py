import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.product_recognition import (
    ProductRecognitionModelOutput,
    ProductRecognitionResult,
)
from app.services.product_recognition import (
    ProductRecognitionUnavailableError,
    _finalize_result,
    recognize_product,
)


def recognized_product() -> ProductRecognitionResult:
    return ProductRecognitionResult(
        brand="CeraVe",
        product_name="Foaming Cleanser",
        variant=None,
        category="Cleanser",
        confidence=0.91,
        model_confidence=0.91,
        status="recognized",
        image_quality="good",
        quality_issues=[],
        brand_evidence="CeraVe",
        product_name_evidence="Foaming Cleanser",
        variant_evidence=None,
        needs_retake=False,
        retake_reasons=[],
    )


class ProductRecognitionServiceTests(unittest.TestCase):
    @patch("app.services.product_recognition._get_client")
    def test_uses_vision_input_and_pydantic_structured_output(self, get_client):
        model_output = ProductRecognitionModelOutput(
            brand="CeraVe",
            brand_evidence="CeraVe",
            product_name="Foaming Cleanser",
            product_name_evidence="Foaming Cleanser",
            variant=None,
            variant_evidence=None,
            category="Cleanser",
            model_confidence=0.91,
            image_quality="good",
            quality_issues=[],
        )
        parse = MagicMock(return_value=SimpleNamespace(output_parsed=model_output))
        get_client.return_value.responses.parse = parse

        result = recognize_product(b"image-bytes", "image/jpeg")

        self.assertEqual(result.status, "recognized")
        kwargs = parse.call_args.kwargs
        self.assertIs(kwargs["text_format"], ProductRecognitionModelOutput)
        image_input = kwargs["input"][0]["content"][1]
        self.assertEqual(image_input["type"], "input_image")
        self.assertTrue(image_input["image_url"].startswith("data:image/jpeg;base64,"))


class ProductRecognitionRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.api.analyze.recognize_product_service")
    def test_returns_validated_product(self, recognize):
        recognize.return_value = recognized_product()

        response = self.client.post(
            "/v1/recognize-product",
            files={"image": ("product.jpg", b"image-bytes", "image/jpeg")},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["product"]["status"], "recognized")
        self.assertFalse(response.json()["product"]["needs_retake"])
        self.assertEqual(response.json()["size"], len(b"image-bytes"))

    def test_rejects_unsupported_content_type(self):
        response = self.client.post(
            "/v1/recognize-product",
            files={"image": ("product.gif", b"image-bytes", "image/gif")},
        )

        self.assertEqual(response.status_code, 400)

    @patch("app.api.analyze.recognize_product_service")
    def test_maps_openai_unavailable_error_to_503(self, recognize):
        recognize.side_effect = ProductRecognitionUnavailableError(
            "OpenAI servisine şu anda ulaşılamıyor."
        )

        response = self.client.post(
            "/v1/recognize-product",
            files={"image": ("product.jpg", b"image-bytes", "image/jpeg")},
        )

        self.assertEqual(response.status_code, 503)
        self.assertNotIn("api_key", response.text.lower())


class ProductRecognitionDecisionTests(unittest.TestCase):
    def test_dove_hand_wash_is_not_mapped_to_hand_cream(self):
        output = ProductRecognitionModelOutput(
            brand="Dove", brand_evidence="Dove",
            product_name="Caring Hand Wash", product_name_evidence="caring hand wash",
            variant=None, variant_evidence=None, category="Hand Cream",
            model_confidence=0.95, image_quality="good", quality_issues=[],
        )

        result = _finalize_result(output)

        self.assertEqual(result.category, "Hand Wash")
        self.assertEqual(result.status, "recognized")

    def test_deodorant_does_not_fall_back_to_after_shave(self):
        output = ProductRecognitionModelOutput(
            brand="Watsons", brand_evidence="Watsons",
            product_name="Invisible Power Men Deodorant",
            product_name_evidence="Invisible Power Men Deodorant",
            variant="24 Saat Etkili Antiperspirant",
            variant_evidence="24 Saat Etkili Antiperspirant",
            category="After-shave/Post-hair-removal", model_confidence=0.91,
            image_quality="good", quality_issues=[],
        )

        result = _finalize_result(output)

        self.assertIsNone(result.category)
        self.assertEqual(result.status, "recognized")

    def test_count_only_product_name_rejects_weak_identity(self):
        output = ProductRecognitionModelOutput(
            brand="Scottex", brand_evidence="Scottex",
            product_name="Adet/Pcs 100", product_name_evidence="Adet/Pcs 100",
            variant="wet wipes", variant_evidence="wet wipes",
            category="Other Skin Care", model_confidence=0.69,
            image_quality="good", quality_issues=[],
        )

        result = _finalize_result(output)

        self.assertEqual(result.status, "unrecognized")
        self.assertIsNone(result.brand)
        self.assertIsNone(result.product_name)
        self.assertIsNone(result.category)

    def test_poor_image_clears_identity_and_requires_retake(self):
        output = ProductRecognitionModelOutput(
            brand="Bepanthol",
            brand_evidence="Bepanthol",
            product_name="Bepanthol Derma",
            product_name_evidence="Bepanthol Derma",
            variant=None,
            variant_evidence=None,
            category="Moisturizer",
            model_confidence=0.84,
            image_quality="poor",
            quality_issues=["too_dark", "too_far"],
        )

        result = _finalize_result(output)

        self.assertEqual(result.status, "unrecognized")
        self.assertIsNone(result.brand)
        self.assertIsNone(result.product_name)
        self.assertLessEqual(result.confidence, 0.39)
        self.assertTrue(result.needs_retake)

    def test_minor_angle_can_continue_when_evidence_is_strong(self):
        output = ProductRecognitionModelOutput(
            brand="Neutrogena",
            brand_evidence="Neutrogena",
            product_name="Besleyici Yoğun Bakım Kremi",
            product_name_evidence="BESLEYİCİ YOĞUN BAKIM KREMİ",
            variant=None,
            variant_evidence=None,
            category="Moisturizer",
            model_confidence=0.95,
            image_quality="usable",
            quality_issues=["label_not_front_facing"],
        )

        result = _finalize_result(output)

        self.assertEqual(result.status, "recognized")
        self.assertEqual(result.confidence, 0.90)
        self.assertFalse(result.needs_retake)
        self.assertEqual(result.retake_reasons, [])

    def test_dark_usable_image_still_cannot_auto_continue(self):
        output = ProductRecognitionModelOutput(
            brand="Neutrogena",
            brand_evidence="Neutrogena",
            product_name="Besleyici Yoğun Bakım Kremi",
            product_name_evidence="BESLEYİCİ YOĞUN BAKIM KREMİ",
            variant=None,
            variant_evidence=None,
            category="Moisturizer",
            model_confidence=0.97,
            image_quality="usable",
            quality_issues=["too_dark"],
        )

        result = _finalize_result(output)

        self.assertEqual(result.status, "uncertain")
        self.assertEqual(result.confidence, 0.69)
        self.assertTrue(result.needs_retake)

    def test_minor_occlusion_can_continue_when_all_identity_is_visible(self):
        output = ProductRecognitionModelOutput(
            brand="Ombia",
            brand_evidence="OMBIA",
            product_name="Handcreme",
            product_name_evidence="HANDCREME",
            variant="Kamille",
            variant_evidence="KAMILLE",
            category="Hand Cream",
            model_confidence=0.98,
            image_quality="usable",
            quality_issues=["occluded"],
        )

        result = _finalize_result(output)

        self.assertEqual(result.status, "recognized")
        self.assertEqual(result.confidence, 0.90)
        self.assertFalse(result.needs_retake)

    def test_minor_glare_and_occlusion_can_continue_with_strong_evidence(self):
        output = ProductRecognitionModelOutput(
            brand="Ombia",
            brand_evidence="OMBIA",
            product_name="Handcreme",
            product_name_evidence="HANDCREME",
            variant="Kamille",
            variant_evidence="KAMILLE",
            category="Hand Cream",
            model_confidence=0.98,
            image_quality="usable",
            quality_issues=["glare", "occluded"],
        )

        result = _finalize_result(output)

        self.assertEqual(result.status, "recognized")
        self.assertFalse(result.needs_retake)

    def test_occlusion_with_lower_confidence_still_requires_retake(self):
        output = ProductRecognitionModelOutput(
            brand="Ombia",
            brand_evidence="OMBIA",
            product_name="Handcreme",
            product_name_evidence="HANDCREME",
            variant=None,
            variant_evidence=None,
            category="Hand Cream",
            model_confidence=0.90,
            image_quality="usable",
            quality_issues=["occluded"],
        )

        result = _finalize_result(output)

        self.assertEqual(result.status, "uncertain")
        self.assertTrue(result.needs_retake)

    def test_missing_evidence_removes_claimed_identity(self):
        output = ProductRecognitionModelOutput(
            brand="Bepanthol",
            brand_evidence=None,
            product_name="Bepanthol Derma",
            product_name_evidence=None,
            variant=None,
            variant_evidence=None,
            category="Moisturizer",
            model_confidence=0.90,
            image_quality="good",
            quality_issues=[],
        )

        result = _finalize_result(output)

        self.assertEqual(result.status, "unrecognized")
        self.assertIsNone(result.brand)
        self.assertIsNone(result.product_name)
        self.assertIn("missing_brand_text", result.retake_reasons)

    def test_duplicate_variant_is_removed(self):
        output = ProductRecognitionModelOutput(
            brand="Neutrogena",
            brand_evidence="Neutrogena",
            product_name="Besleyici Çok Amaçlı Yoğun Bakım Kremi",
            product_name_evidence="Besleyici Çok Amaçlı Yoğun Bakım Kremi",
            variant="Besleyici",
            variant_evidence="Besleyici",
            category="Moisturizer",
            model_confidence=0.98,
            image_quality="good",
            quality_issues=[],
        )

        result = _finalize_result(output)

        self.assertIsNone(result.variant)
        self.assertIsNone(result.variant_evidence)


if __name__ == "__main__":
    unittest.main()
