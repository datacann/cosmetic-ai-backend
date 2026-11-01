import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.models.suitability_scoring import CalculateSuitabilityScoreRequest
from app.services.openai_usage import begin_usage_capture, finish_usage_capture
from app.services.suitability_scoring import (
    _round_score,
    _verdict,
    calculate_suitability_score,
)


def signal(status, available=True, severity="none", reason="Test"):
    return {
        "status": status,
        "conflict_severity": severity,
        "reasons": [reason],
        "evidence_ingredients": ["Glycerin"] if status not in {"unknown", "neutral"} else [],
        "matched_context": [],
        "unassessed_context": [],
        "available": available,
    }


def scoring_request(
    *, skin="match", sensitivity="match", goals="match", appearance="match",
    avoid="neutral", avoid_matches=None, routine="match", confidence=0.8,
    skin_severity="none", sensitivity_severity="none", goals_severity="none",
    appearance_severity="none", routine_severity="none", reason="Test",
    goal_assessments=None,
):
    routine_value = signal(routine, routine != "unknown", routine_severity, reason)
    routine_value.update({"potential_conflicts": [], "recommended_time": "both" if routine != "unknown" else "unknown"})
    return CalculateSuitabilityScoreRequest.model_validate({
        "compatibility": {
            "formula_name": "Main",
            "skin_type": signal(skin, skin != "unknown", skin_severity, reason),
            "sensitivity": signal(sensitivity, sensitivity != "unknown", sensitivity_severity, reason),
            "goals": {"assessments": goal_assessments or [{
                "goal": "hydration",
                **signal(goals, goals != "unknown", goals_severity, reason),
            }]},
            "current_appearance": signal(appearance, appearance != "unknown", appearance_severity, reason),
            "avoid_preferences": {
                "status": avoid,
                "matched_avoid_ingredients": avoid_matches or [],
            },
            "routine": routine_value,
            "assessment_confidence": confidence,
        }
    })


class SuitabilityScoringTests(unittest.TestCase):
    def test_all_strong_matches_produce_very_suitable_score(self):
        result = calculate_suitability_score(scoring_request(
            skin="strong_match", sensitivity="strong_match", goals="strong_match",
            appearance="strong_match", avoid="unknown", routine="strong_match",
        ))
        self.assertEqual(result.score, 100)
        self.assertEqual(result.verdict, "Çok Uygun")

    def test_mixed_match_and_caution_is_weighted_deterministically(self):
        result = calculate_suitability_score(scoring_request(sensitivity="caution"))
        self.assertEqual(result.dimension_scores.sensitivity, 50)
        self.assertEqual(result.score, 76)
        self.assertEqual(result.verdict, "Uygun")

    def test_mild_moderate_and_strong_severity_adjustments_are_centralized(self):
        result = calculate_suitability_score(scoring_request(
            skin_severity="mild", sensitivity="match",
            sensitivity_severity="moderate", goals_severity="strong",
        ))
        self.assertEqual(
            [(item.code, item.points) for item in result.severity_adjustments],
            [("skin_type_mild_severity", -3),
             ("sensitivity_moderate_severity", -7),
             ("goals_strong_severity", -12)],
        )

    def test_match_with_mild_routine_conflict_is_adjusted_without_status_change(self):
        result = calculate_suitability_score(scoring_request(routine_severity="mild"))
        self.assertEqual(result.dimension_scores.routine, 85)
        self.assertEqual(result.severity_adjustments[0].points, -3)

    def test_moderate_routine_concern_has_deterministic_adjustment(self):
        result = calculate_suitability_score(scoring_request(
            routine="match", routine_severity="moderate"
        ))
        self.assertEqual(
            [(item.code, item.points) for item in result.severity_adjustments],
            [("routine_moderate_severity", -7)],
        )

    def test_existing_conflict_penalty_is_not_double_counted_by_severity(self):
        result = calculate_suitability_score(scoring_request(
            routine="conflict", routine_severity="strong"
        ))
        self.assertEqual(result.severity_adjustments, [])
        self.assertIn("routine_conflict", [item.code for item in result.penalties])

    def test_caution_status_is_not_double_counted_by_severity(self):
        result = calculate_suitability_score(scoring_request(
            sensitivity="caution", sensitivity_severity="moderate"
        ))
        self.assertEqual(result.dimension_scores.sensitivity, 50)
        self.assertEqual(result.severity_adjustments, [])

    def test_unknown_routine_is_excluded_and_does_not_lower_score(self):
        with_unknown = calculate_suitability_score(scoring_request(routine="unknown"))
        self.assertEqual(with_unknown.score, 82)
        self.assertIsNone(with_unknown.dimension_scores.routine)
        self.assertEqual(with_unknown.applied_weights.routine, 0)
        self.assertAlmostEqual(sum(with_unknown.applied_weights.model_dump().values()), 1.0)

    def test_unknown_skin_type_is_excluded_and_weights_renormalize(self):
        result = calculate_suitability_score(scoring_request(skin="unknown"))
        self.assertIsNone(result.dimension_scores.skin_type)
        self.assertEqual(result.applied_weights.skin_type, 0)
        self.assertAlmostEqual(sum(result.applied_weights.model_dump().values()), 1.0)

    def test_unassessed_goal_does_not_change_aggregated_available_goal_status(self):
        request = scoring_request(goal_assessments=[
            {"goal": "hydration", **signal("match")},
            {"goal": "uneven_tone", **signal("unknown", False)},
        ])
        result = calculate_suitability_score(request)
        self.assertEqual(result.dimension_scores.goals, 85)

    def test_two_selected_goals_are_averaged_individually(self):
        result = calculate_suitability_score(scoring_request(goal_assessments=[
            {"goal": "hydration", **signal("strong_match")},
            {"goal": "uneven_tone", **signal("neutral")},
        ]))
        self.assertEqual(result.dimension_scores.goals, 85.0)

    def test_one_assessed_goal_and_one_unknown_excludes_unknown(self):
        result = calculate_suitability_score(scoring_request(goal_assessments=[
            {"goal": "hydration", **signal("strong_match")},
            {"goal": "uneven_tone", **signal("unknown", False)},
        ]))
        self.assertEqual(result.dimension_scores.goals, 100.0)

    def test_different_goal_compatibility_changes_goal_dimension(self):
        stronger = calculate_suitability_score(scoring_request(goal_assessments=[
            {"goal": "hydration", **signal("strong_match")},
            {"goal": "uneven_tone", **signal("match")},
        ]))
        weaker = calculate_suitability_score(scoring_request(goal_assessments=[
            {"goal": "hydration", **signal("match")},
            {"goal": "uneven_tone", **signal("neutral")},
        ]))
        self.assertGreater(stronger.dimension_scores.goals, weaker.dimension_scores.goals)
        self.assertGreater(stronger.score, weaker.score)

    def test_unknown_goal_dimension_is_excluded_not_penalized(self):
        result = calculate_suitability_score(scoring_request(
            goals="unknown", goals_severity="none"
        ))
        self.assertIsNone(result.dimension_scores.goals)
        self.assertEqual(result.applied_weights.goals, 0)
        self.assertNotIn(
            "goals", " ".join(item.code for item in result.severity_adjustments)
        )

    def test_all_individual_goals_unknown_excludes_goal_dimension(self):
        result = calculate_suitability_score(scoring_request(goal_assessments=[
            {"goal": "hydration", **signal("unknown", False)},
            {"goal": "uneven_tone", **signal("unknown", False)},
        ]))
        self.assertIsNone(result.dimension_scores.goals)
        self.assertEqual(result.applied_weights.goals, 0)

    def test_exact_avoid_conflict_adds_penalty_and_cap(self):
        result = calculate_suitability_score(scoring_request(
            avoid="conflict", avoid_matches=["Parfum"]
        ))
        self.assertEqual(result.penalties[0].points, -15)
        self.assertEqual(result.caps[0].max_score, 69)
        self.assertLessEqual(result.score, 69)

    def test_multiple_avoid_conflicts_respect_penalty_cap(self):
        result = calculate_suitability_score(scoring_request(
            avoid="conflict", avoid_matches=["Parfum", "Linalool", "Geraniol", "Limonene"]
        ))
        penalty = next(item for item in result.penalties if item.code == "exact_avoid_preference_conflict")
        self.assertEqual(penalty.points, -25)

    def test_sensitivity_conflict_adds_central_penalty(self):
        result = calculate_suitability_score(scoring_request(sensitivity="conflict"))
        self.assertIn("sensitivity_conflict", [item.code for item in result.penalties])

    def test_routine_conflict_adds_central_penalty(self):
        result = calculate_suitability_score(scoring_request(routine="conflict"))
        self.assertIn("routine_conflict", [item.code for item in result.penalties])

    def test_multiple_serious_conflicts_apply_stricter_cap(self):
        result = calculate_suitability_score(scoring_request(
            sensitivity="conflict", avoid="conflict", avoid_matches=["Parfum"],
            routine="conflict",
        ))
        self.assertIn("three_serious_conflicts_cap", [item.code for item in result.caps])
        self.assertLessEqual(result.score, 49)

    def test_rounding_clamps_below_zero_and_above_one_hundred(self):
        self.assertEqual(_round_score(-20), 0)
        self.assertEqual(_round_score(120), 100)
        self.assertEqual(_round_score(84.5), 85)

    def test_verdict_boundaries(self):
        expected = {
            49: "Düşük Uyum", 50: "Dikkatli Değerlendir",
            69: "Dikkatli Değerlendir", 70: "Uygun", 84: "Uygun",
            85: "Çok Uygun", 100: "Çok Uygun",
        }
        for score, verdict in expected.items():
            with self.subTest(score=score):
                self.assertEqual(_verdict(score), verdict)

    def test_same_input_repeated_is_exactly_deterministic(self):
        request = scoring_request(sensitivity="caution", routine="unknown")
        results = [calculate_suitability_score(request).model_dump() for _ in range(20)]
        self.assertTrue(all(result == results[0] for result in results))

    def test_analysis_confidence_is_preserved_but_does_not_change_score(self):
        low = calculate_suitability_score(scoring_request(confidence=0.1))
        high = calculate_suitability_score(scoring_request(confidence=0.95))
        self.assertEqual(low.score, high.score)
        self.assertEqual(low.analysis_confidence, 0.1)
        self.assertEqual(high.analysis_confidence, 0.95)

    def test_non_package_ingredient_source_reduces_and_caps_score(self):
        request = scoring_request()
        package = calculate_suitability_score(request.model_copy(update={
            "ingredient_source_type": "package_image",
            "ingredient_source_reliability": "high",
        }))
        front_only = calculate_suitability_score(request.model_copy(update={
            "ingredient_source_type": "database",
            "ingredient_source_reliability": "high",
        }))

        self.assertLessEqual(front_only.score, 79)
        self.assertLessEqual(front_only.score, package.score - 10)
        self.assertTrue(any(item.code == "ingredient_list_not_package_verified" for item in front_only.penalties))
        self.assertTrue(any(item.code == "ingredient_list_not_package_verified_cap" for item in front_only.caps))

    def test_free_text_reasons_do_not_affect_score(self):
        first = calculate_suitability_score(scoring_request(reason="Kısa açıklama"))
        second = calculate_suitability_score(scoring_request(reason="Tamamen farklı bir açıklama"))
        self.assertEqual(first.score, second.score)
        self.assertEqual(first.base_score, second.base_score)
        self.assertEqual(first.severity_adjustments, second.severity_adjustments)

    def test_evidence_text_alone_does_not_affect_score(self):
        first = scoring_request()
        second = scoring_request()
        first.compatibility.goals.assessments[0].evidence_ingredients = ["Glycerin"]
        second.compatibility.goals.assessments[0].evidence_ingredients = ["Panthenol"]
        self.assertEqual(
            calculate_suitability_score(first).score,
            calculate_suitability_score(second).score,
        )

    def test_no_openai_or_web_usage(self):
        token = begin_usage_capture()
        try:
            calculate_suitability_score(scoring_request())
        finally:
            usage = finish_usage_capture(token)
        self.assertEqual(usage["calls"], [])
        self.assertEqual(usage["estimated_usd"], 0)

    def test_endpoint_returns_400_when_every_dimension_unknown(self):
        request = scoring_request(
            skin="unknown", sensitivity="unknown", goals="unknown",
            appearance="unknown", avoid="unknown", routine="unknown",
        )
        response = TestClient(app).post("/v1/calculate-score", json=request.model_dump())
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
