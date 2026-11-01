import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from app.models.compatibility_assessment import (
    AnalyzeContextCompatibilityRequest,
    CompatibilityAssessmentModelOutput,
    CompatibilitySignalModelOutput,
    FormulaCompatibilityModelOutput,
    GoalCompatibilityModelOutput,
    GoalsCompatibilityModelOutput,
    RoutineCompatibilityModelOutput,
)
from app.models.ingredient_knowledge import (
    IngredientKnowledgeRecord,
    IngredientKnowledgeResponse,
    KnowledgeEnrichedFormula,
    KnowledgeEnrichedIngredient,
)
from app.models.ingredient_normalization import NormalizedIngredient
from app.models.user_context import BuildUserContextRequest
from app.services.compatibility_assessment import (
    CompatibilityAssessmentUnavailableError,
    analyze_context_compatibility,
)
from app.services.openai_usage import begin_usage_capture, finish_usage_capture
from app.services.user_context import build_user_context


def knowledge_ingredient(
    name, *, functions=None, properties=None, cautions=None, verified=True,
):
    return KnowledgeEnrichedIngredient(
        normalized=NormalizedIngredient(
            position=1, raw_name=name,
            canonical_name=name if verified else None,
            match_status="cosing" if verified else "unmatched",
        ),
        knowledge=IngredientKnowledgeRecord(
            canonical_name=name if verified else None,
            functions=functions or [], cosmetic_properties=properties or [],
            caution_tags=cautions or [],
            evidence_status="verified" if verified else "unresolved",
            source="cosing" if verified else "unresolved",
            source_references=[],
        ),
    )


def signal(status="neutral", evidence=None, matched=None, unassessed=None, severity="none"):
    return CompatibilitySignalModelOutput(
        status=status, conflict_severity=severity,
        reasons=["Kozmetik uyum açıklaması."],
        evidence_ingredients=evidence or [], matched_context=matched or [],
        unassessed_context=unassessed or [],
    )


def goal_signal(goal, status="neutral", evidence=None, severity="none"):
    return GoalCompatibilityModelOutput(
        goal=goal, **signal(status, evidence, [goal], [], severity).model_dump()
    )


def model_output(**overrides):
    values = {
        "skin_type": signal(), "sensitivity": signal(),
        "goals": GoalsCompatibilityModelOutput(assessments=[]),
        "current_appearance": signal(),
        "routine": RoutineCompatibilityModelOutput(
            **signal().model_dump(), potential_conflicts=[], recommended_time="both"
        ),
    }
    values.update(overrides)
    return CompatibilityAssessmentModelOutput(
        formulas=[FormulaCompatibilityModelOutput(formula_name="Main", **values)],
        overall_cautions=[],
    )


def compatibility_request(*, ingredients=None, profile=None, routine=None):
    ingredients = ingredients or [knowledge_ingredient(
        "Glycerin", functions=["humectant"], properties=["supports_hydration"]
    )]
    knowledge = IngredientKnowledgeResponse(
        provenance="package_image",
        formulas=[KnowledgeEnrichedFormula(name="Main", ingredients=ingredients)],
        unresolved_count=sum(item.knowledge.evidence_status != "verified" for item in ingredients),
        paid_fallback_count=0,
    )
    context = build_user_context(BuildUserContextRequest(
        profile=profile or {}, routine=routine or [],
    ))
    return AnalyzeContextCompatibilityRequest(
        product={"brand": "Test", "product_name": "Cream", "category": "Moisturizer"},
        ingredient_knowledge=knowledge, user_context=context,
    )


def mocked_response(output):
    return SimpleNamespace(
        output_parsed=output, output=[],
        usage=SimpleNamespace(input_tokens=800, output_tokens=250),
    )


class CompatibilityAssessmentTests(unittest.TestCase):
    @patch("app.services.compatibility_assessment._get_client")
    def test_dry_skin_humectant_and_hydration_goal_keep_positive_signals(self, client):
        output = model_output(
            skin_type=signal("match", ["Glycerin"]),
            goals=GoalsCompatibilityModelOutput(assessments=[
                goal_signal("hydration", "strong_match", ["Glycerin"])
            ]),
        )
        client.return_value.responses.parse.return_value = mocked_response(output)
        result = analyze_context_compatibility(compatibility_request(
            profile={"skin_type": "Kuru", "goals": ["Kuruluk / nem"]}
        ))
        formula = result.formulas[0]
        self.assertEqual(formula.skin_type.status, "match")
        self.assertEqual(formula.goals.assessments[0].status, "strong_match")
        self.assertEqual(formula.goals.assessments[0].evidence_ingredients, ["Glycerin"])

    @patch("app.services.compatibility_assessment._get_client")
    def test_sensitive_fragrance_caution_and_exact_avoid_conflict(self, client):
        ingredient = knowledge_ingredient(
            "Parfum", functions=["fragrance"], properties=["provides_fragrance"],
            cautions=["fragrance_related"],
        )
        client.return_value.responses.parse.return_value = mocked_response(model_output(
            sensitivity=signal("caution", ["Parfum"])
        ))
        result = analyze_context_compatibility(compatibility_request(
            ingredients=[ingredient],
            profile={"sensitivity": "Biraz hassas", "avoid_ingredients": ["Parfume"]},
        ))
        formula = result.formulas[0]
        self.assertEqual(formula.sensitivity.status, "caution")
        self.assertEqual(formula.avoid_preferences.status, "conflict")
        self.assertEqual(formula.avoid_preferences.conflict_severity, "strong")
        self.assertEqual(formula.avoid_preferences.matched_avoid_ingredients, ["Parfum"])

    @patch("app.services.compatibility_assessment._get_client")
    def test_unknown_skin_and_empty_routine_are_not_negative(self, client):
        client.return_value.responses.parse.return_value = mocked_response(model_output(
            skin_type=signal("conflict", ["Glycerin"]),
            routine=RoutineCompatibilityModelOutput(
                **signal("conflict", ["Glycerin"]).model_dump(),
                potential_conflicts=["invented"], recommended_time="evening",
            ),
        ))
        result = analyze_context_compatibility(compatibility_request())
        formula = result.formulas[0]
        self.assertEqual(formula.skin_type.status, "unknown")
        self.assertEqual(formula.routine.status, "unknown")
        self.assertEqual(formula.routine.recommended_time, "unknown")
        self.assertEqual(formula.routine.potential_conflicts, [])

    @patch("app.services.compatibility_assessment._get_client")
    def test_multiple_goals_are_preserved_for_independent_reasoning(self, client):
        client.return_value.responses.parse.return_value = mocked_response(model_output(
            goals=GoalsCompatibilityModelOutput(assessments=[
                goal_signal("hydration", "match", ["Glycerin"]),
                goal_signal("uneven_tone", "unknown"),
            ])
        ))
        result = analyze_context_compatibility(compatibility_request(profile={
            "goals": ["Kuruluk / nem", "Leke / ton eşitsizliği"]
        }))
        goals = result.formulas[0].goals.assessments
        self.assertEqual([item.goal for item in goals], ["hydration", "uneven_tone"])
        self.assertEqual([item.status for item in goals], ["match", "unknown"])
        self.assertEqual(result.formulas[0].goal_coverage, 0.5)

    @patch("app.services.compatibility_assessment._get_client")
    def test_duplicate_exfoliating_active_in_routine_is_deterministic_conflict(self, client):
        active = knowledge_ingredient(
            "Glycolic Acid", functions=["exfoliant"],
            properties=["supports_a_smoother_looking_surface"],
            cautions=["exfoliating_active"],
        )
        client.return_value.responses.parse.return_value = mocked_response(model_output())
        result = analyze_context_compatibility(compatibility_request(
            ingredients=[active], routine=[{
                "name": "Toner", "routine_time": "evening",
                "ingredients": [{"raw_name": "Glycolic Acid", "canonical_name": "Glycolic Acid"}],
            }],
        ))
        routine = result.formulas[0].routine
        self.assertEqual(routine.status, "conflict")
        self.assertEqual(routine.conflict_severity, "strong")
        self.assertEqual(routine.potential_conflicts, ["duplicate_exfoliating_active:Glycolic Acid"])

    @patch("app.services.compatibility_assessment._get_client")
    def test_unresolved_is_not_sent_and_lowers_deterministic_confidence(self, client):
        unresolved = knowledge_ingredient("Unreadable", verified=False)
        client.return_value.responses.parse.return_value = mocked_response(model_output())
        request = compatibility_request(
            ingredients=[knowledge_ingredient("Glycerin"), unresolved],
            profile={"skin_type": "Kuru"},
        )
        result = analyze_context_compatibility(request)
        sent = client.return_value.responses.parse.call_args.kwargs["input"]
        self.assertNotIn("Unreadable", sent)
        self.assertIn("incomplete_ingredient_knowledge", result.warnings)
        self.assertLess(result.formulas[0].assessment_confidence, 1.0)

    @patch("app.services.compatibility_assessment._get_client")
    def test_one_structured_call_no_web_search_no_score_and_usage_recorded(self, client):
        client.return_value.responses.parse.return_value = mocked_response(model_output())
        token = begin_usage_capture()
        try:
            result = analyze_context_compatibility(compatibility_request(
                profile={"skin_type": "Kuru"}
            ))
        finally:
            usage = finish_usage_capture(token)
        parse = client.return_value.responses.parse
        self.assertEqual(parse.call_count, 1)
        self.assertNotIn("tools", parse.call_args.kwargs)
        self.assertEqual(usage["calls"][0]["stage"], "compatibility_analysis")
        self.assertEqual(usage["calls"][0]["web_search_calls"], 0)
        serialized = result.model_dump()
        self.assertNotIn("score", str(serialized).casefold())

    @patch("app.services.compatibility_assessment._get_client")
    def test_invalid_or_missing_model_output_fails_safely(self, client):
        client.return_value.responses.parse.return_value = mocked_response(None)
        with self.assertRaises(CompatibilityAssessmentUnavailableError):
            analyze_context_compatibility(compatibility_request())

    def test_medical_claims_fail_schema_validation(self):
        with self.assertRaises(ValidationError):
            CompatibilitySignalModelOutput(
                status="match", reasons=["This treats acne."],
                evidence_ingredients=["Niacinamide"],
            )

    def test_conflict_severity_is_schema_validated(self):
        with self.assertRaises(ValidationError):
            CompatibilitySignalModelOutput(
                status="caution", conflict_severity="extreme",
                reasons=["Test"], evidence_ingredients=["Niacinamide"],
            )
        with self.assertRaises(ValidationError):
            CompatibilitySignalModelOutput(
                status="unknown", conflict_severity="mild",
                reasons=["Test"], evidence_ingredients=[],
            )


if __name__ == "__main__":
    unittest.main()
