import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.compatibility_analysis import (
    AnalyzeCompatibilityRequest,
    CompatibilityAnalysisModelOutput,
    CompatibilityDimensionModelOutput,
)
from app.services.compatibility_analysis import analyze_compatibility


def dimension(assessment="neutral", evidence=None):
    return CompatibilityDimensionModelOutput(
        assessment=assessment,
        explanation="Test açıklaması.",
        evidence_ingredients=evidence or [],
    )


def request_payload(*, evidence_status="verified", avoid=None):
    return AnalyzeCompatibilityRequest.model_validate(
        {
            "product": {"brand": "Test", "product_name": "Cream", "category": "Moisturizer"},
            "ingredients": [
                {
                    "input_name": "Glycerin",
                    "canonical_name": "Glycerin",
                    "functions": ["humectant"],
                    "flags": [],
                    "evidence_status": evidence_status,
                }
            ],
            "skin_profile": {
                "skin_type": "Kuru",
                "sensitivity": None,
                "appearance": [],
                "goals": ["Kuruluk / nem"],
                "avoid_ingredients": avoid or [],
            },
            "routine": [],
        }
    )


class CompatibilityAnalysisServiceTests(unittest.TestCase):
    @patch("app.services.compatibility_analysis._get_client")
    def test_validates_evidence_and_forces_missing_context_to_unknown(self, get_client):
        output = CompatibilityAnalysisModelOutput(
            skin_type=dimension("supportive", ["Glycerin", "Invented"]),
            sensitivity=dimension("caution", ["Glycerin"]),
            goals=dimension("supportive", ["Invented"]),
            appearance=dimension(),
            routine=dimension(),
        )
        parse = MagicMock(return_value=SimpleNamespace(output_parsed=output))
        get_client.return_value.responses.parse = parse

        result = analyze_compatibility(request_payload())

        self.assertEqual(result.compatibility.skin_type.evidence_ingredients, ["Glycerin"])
        self.assertEqual(result.compatibility.sensitivity.assessment, "unknown")
        self.assertFalse(result.compatibility.sensitivity.available)
        self.assertEqual(result.compatibility.goals.assessment, "unknown")
        self.assertEqual(result.compatibility.goals.evidence_ingredients, [])
        self.assertNotIn("Invented", str(result.model_dump()))

    @patch("app.services.compatibility_analysis._get_client")
    def test_avoid_match_is_deterministic(self, get_client):
        output = CompatibilityAnalysisModelOutput(
            skin_type=dimension(),
            sensitivity=dimension(),
            goals=dimension(),
            appearance=dimension(),
            routine=dimension(),
        )
        get_client.return_value.responses.parse.return_value = SimpleNamespace(output_parsed=output)

        result = analyze_compatibility(request_payload(avoid=["glycerin"]))

        self.assertEqual(result.avoid_matches, ["Glycerin"])
        self.assertIn("user_avoid_preference_match", result.warnings)

    @patch("app.services.compatibility_analysis._get_client")
    def test_unverified_ingredients_do_not_reach_model(self, get_client):
        result = analyze_compatibility(request_payload(evidence_status="unverified"))

        get_client.assert_not_called()
        self.assertIn("no_verified_ingredient_knowledge", result.warnings)
        self.assertEqual(result.compatibility.skin_type.assessment, "unknown")


class CompatibilityAnalysisRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_rejects_empty_ingredient_list(self):
        response = self.client.post(
            "/v1/analyze-compatibility",
            json={
                "product": {"product_name": "Test"},
                "ingredients": [],
                "skin_profile": {},
            },
        )

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
