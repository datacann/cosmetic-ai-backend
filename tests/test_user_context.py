import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.models.user_context import BuildUserContextRequest
from app.services.openai_usage import begin_usage_capture, finish_usage_capture
from app.services.user_context import build_user_context


class UserContextTests(unittest.TestCase):
    def build(self, profile=None, routine=None):
        return build_user_context(BuildUserContextRequest(
            profile=profile or {}, routine=routine or [],
        ))

    def test_turkish_profile_values_and_multi_select_goals_are_canonical(self):
        result = self.build(profile={
            "skin_type": "Kuru", "sensitivity": "Biraz hassas",
            "goals": ["Kuruluk / nem", "Leke / ton eşitsizliği"],
        })
        self.assertEqual(result.skin.skin_type, "dry")
        self.assertEqual(result.skin.sensitivity, "somewhat_sensitive")
        self.assertEqual(result.skin.goals, ["hydration", "uneven_tone"])

    def test_appearance_concrete_values_override_none_and_unknown(self):
        result = self.build(profile={
            "appearance": ["Hiçbiri", "Emin değilim", "Kızarıklık görünümü"]
        })
        self.assertEqual(result.skin.appearance, ["redness"])

    def test_appearance_none_wins_when_no_concrete_concern_exists(self):
        result = self.build(profile={"appearance": ["Emin değilim", "Hiçbiri"]})
        self.assertEqual(result.skin.appearance, ["none"])

    def test_missing_fields_remain_unknown(self):
        result = self.build()
        self.assertEqual(result.skin.skin_type, "unknown")
        self.assertEqual(result.skin.sensitivity, "unknown")
        self.assertEqual(result.skin.appearance, ["unknown"])
        self.assertIn("skin.skin_type", result.unknown_fields)
        self.assertIn("routine", result.unknown_fields)

    def test_avoid_aliases_normalize_and_unknown_is_not_guessed(self):
        result = self.build(profile={
            "avoid_ingredients": ["Aqua", "Parfume", "Mystery Extract"]
        })
        self.assertEqual(
            [(item.raw_name, item.canonical_name) for item in result.preferences.avoid_ingredients],
            [("Aqua", "Water"), ("Parfume", "Parfum"), ("Mystery Extract", None)],
        )

    def test_morning_evening_and_both_are_logical_views(self):
        result = self.build(routine=[
            {"name": "AM", "routine_time": "morning"},
            {"name": "PM", "routine_time": "evening"},
            {"name": "Shared", "routine_time": "both"},
        ])
        self.assertEqual([item.name for item in result.routine.morning], ["AM", "Shared"])
        self.assertEqual([item.name for item in result.routine.evening], ["PM", "Shared"])

    def test_routine_prefers_existing_canonical_identity_and_normalizes_alias(self):
        result = self.build(routine=[{
            "name": "Cream", "routine_time": "morning",
            "ingredients": [
                {"raw_name": "Vitamin B3", "canonical_name": "Niacinamide"},
                "Aqua",
            ],
        }])
        ingredients = result.routine.morning[0].ingredients
        self.assertEqual([item.canonical_name for item in ingredients], ["Niacinamide", "Water"])

    def test_empty_routine_is_safe_and_has_no_compatibility_fields_or_api_cost(self):
        token = begin_usage_capture()
        try:
            result = self.build()
        finally:
            usage = finish_usage_capture(token)
        self.assertEqual(result.routine.morning, [])
        self.assertEqual(result.routine.evening, [])
        self.assertEqual(usage["calls"], [])
        serialized = result.model_dump()
        for forbidden in ("score", "suitable", "conflicts", "warnings", "recommendations"):
            self.assertNotIn(forbidden, serialized)

    def test_manual_endpoint_returns_typed_context(self):
        response = TestClient(app).post("/v1/build-user-context", json={
            "profile": {"skin_type": "Karma"}, "routine": [],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["skin"]["skin_type"], "combination")


if __name__ == "__main__":
    unittest.main()
