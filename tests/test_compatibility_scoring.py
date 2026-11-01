import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.models.compatibility_scoring import ScoreCompatibilityRequest
from app.services.compatibility_scoring import (
    ScoringInsufficientDataError,
    score_compatibility,
)


def dimension(assessment: str, available: bool = True):
    return {
        "assessment": assessment,
        "explanation": "Test",
        "evidence_ingredients": [] if assessment in {"neutral", "unknown"} else ["Test"],
        "available": available,
    }


def scoring_request(*, overrides=None, avoid_matches=None):
    dimensions = {
        "skin_type": dimension("caution"),
        "sensitivity": dimension("caution"),
        "goals": dimension("supportive"),
        "appearance": dimension("caution"),
        "routine": dimension("unknown", available=False),
    }
    dimensions.update(overrides or {})
    return ScoreCompatibilityRequest.model_validate(
        {
            "compatibility": dimensions,
            "avoid_matches": avoid_matches or [],
        }
    )


class CompatibilityScoringServiceTests(unittest.TestCase):
    def test_example_score_reweights_missing_routine(self):
        result = score_compatibility(scoring_request())

        self.assertEqual(result.raw_score, 53)
        self.assertEqual(result.score, 53)
        self.assertEqual(result.coverage, 90)
        self.assertEqual(result.verdict, "Dikkatli Değerlendir")
        self.assertIn("available_dimensions_reweighted", result.applied_rules)
        self.assertFalse(result.breakdown.routine.included)

    def test_unknown_dimension_is_not_a_penalty(self):
        result = score_compatibility(
            scoring_request(
                overrides={
                    "skin_type": dimension("supportive"),
                    "sensitivity": dimension("unknown", available=False),
                    "goals": dimension("supportive"),
                    "appearance": dimension("unknown", available=False),
                }
            )
        )

        self.assertEqual(result.score, 100)
        self.assertEqual(result.coverage, 40)

    def test_avoid_match_applies_penalty_and_cap(self):
        supportive = {name: dimension("supportive") for name in ["skin_type", "sensitivity", "goals", "appearance", "routine"]}
        result = score_compatibility(
            scoring_request(overrides=supportive, avoid_matches=["Parfum"])
        )

        self.assertEqual(result.raw_score, 100)
        self.assertEqual(result.score, 69)
        self.assertEqual(result.verdict, "Dikkatli Değerlendir")
        self.assertIn("avoid_preference_penalty_10", result.applied_rules)
        self.assertIn("avoid_preference_score_cap_69", result.applied_rules)

    def test_sensitivity_caution_caps_high_score(self):
        result = score_compatibility(
            scoring_request(
                overrides={
                    "skin_type": dimension("supportive"),
                    "sensitivity": dimension("caution"),
                    "goals": dimension("supportive"),
                    "appearance": dimension("supportive"),
                    "routine": dimension("supportive"),
                }
            )
        )

        self.assertEqual(result.raw_score, 82)
        self.assertEqual(result.score, 69)
        self.assertIn("sensitivity_caution_score_cap_69", result.applied_rules)

    def test_rejects_when_every_dimension_is_unknown(self):
        unknown = {name: dimension("unknown", available=False) for name in ["skin_type", "sensitivity", "goals", "appearance", "routine"]}
        with self.assertRaises(ScoringInsufficientDataError):
            score_compatibility(scoring_request(overrides=unknown))


class CompatibilityScoringRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_maps_insufficient_data_to_400(self):
        unknown = {name: dimension("unknown", available=False) for name in ["skin_type", "sensitivity", "goals", "appearance", "routine"]}
        response = self.client.post(
            "/v1/score-compatibility",
            json={"compatibility": unknown},
        )

        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
