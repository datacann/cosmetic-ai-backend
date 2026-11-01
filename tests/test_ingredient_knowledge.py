import unittest
from unittest.mock import MagicMock

from app.database.repository import normalize_text
from app.models.ingredient_analysis import (
    AnalyzeIngredientsResponse,
    IngredientAnalysisItem,
)
from app.models.ingredient_normalization import (
    NormalizeIngredientsResponse,
    NormalizedIngredient,
    NormalizedIngredientFormula,
)
from app.services.ingredient_knowledge import attach_ingredient_knowledge


def normalized_formula(*items, name="Main"):
    return NormalizedIngredientFormula(
        name=name,
        ingredients=[
            NormalizedIngredient(
                position=index,
                raw_name=raw,
                canonical_name=canonical,
                match_status="alias" if canonical else "unmatched",
            )
            for index, (raw, canonical) in enumerate(items, start=1)
        ],
    )


def normalized_response(*formulas):
    return NormalizeIngredientsResponse(
        provenance="package_image",
        formulas=list(formulas),
        unmatched_count=sum(
            item.canonical_name is None for formula in formulas for item in formula.ingredients
        ),
    )


def knowledge_item(
    name,
    *,
    source="postgresql",
    functions=None,
    flags=None,
    urls=None,
    note="Cosmetic knowledge only.",
):
    return IngredientAnalysisItem(
        input_name=name,
        canonical_name=name,
        functions=functions or ["skin_conditioning"],
        flags=flags or [],
        note=note,
        evidence_status="verified",
        source_urls=urls or ["https://catalog.example/ingredient"],
        knowledge_source=source,
    )


class IngredientKnowledgeTests(unittest.TestCase):
    def test_known_postgresql_ingredient_skips_cosing_and_paid_research(self):
        cosing = MagicMock()
        researcher = MagicMock()
        result = attach_ingredient_knowledge(
            normalized_response(normalized_formula(("Niacinamide", "Niacinamide"))),
            cache_resolver=lambda **_: {"niacinamide": knowledge_item("Niacinamide")},
            cosing_resolver=cosing,
            researcher=researcher,
        )

        record = result.formulas[0].ingredients[0].knowledge
        self.assertEqual(record.source, "postgresql")
        self.assertEqual(record.evidence_status, "verified")
        cosing.assert_not_called()
        researcher.assert_not_called()
        self.assertEqual(result.paid_fallback_count, 0)

    def test_known_cosing_ingredient_is_used_without_paid_research(self):
        researcher = MagicMock()
        result = attach_ingredient_knowledge(
            normalized_response(normalized_formula(("Allantoin", "Allantoin"))),
            cache_resolver=lambda **_: {},
            cosing_resolver=lambda **_: {
                "allantoin": knowledge_item(
                    "Allantoin", source="cosing",
                    urls=["https://ec.europa.eu/cosing/allantoin"],
                )
            },
            researcher=researcher,
        )

        self.assertEqual(result.formulas[0].ingredients[0].knowledge.source, "cosing")
        researcher.assert_not_called()

    def test_alias_normalized_water_uses_aqua_cosing_knowledge_but_keeps_identity(self):
        def cosing(*, ingredients):
            self.assertEqual(ingredients, ["Aqua"])
            return {
                "aqua": knowledge_item(
                    "Aqua", source="cosing", functions=["solvent"],
                    urls=["https://ec.europa.eu/cosing/aqua"],
                )
            }

        result = attach_ingredient_knowledge(
            normalized_response(normalized_formula(("Aqua", "Water"))),
            cache_resolver=lambda **_: {},
            cosing_resolver=cosing,
        )

        enriched = result.formulas[0].ingredients[0]
        self.assertEqual(enriched.normalized.canonical_name, "Water")
        self.assertEqual(enriched.knowledge.canonical_name, "Water")
        self.assertEqual(enriched.knowledge.functions, ["solvent"])

    def test_unknown_canonical_ingredient_stays_unverified_without_researcher(self):
        result = attach_ingredient_knowledge(
            normalized_response(normalized_formula(("Known identity", "Known Identity"))),
            cache_resolver=lambda **_: {},
            cosing_resolver=lambda **_: {},
        )

        record = result.formulas[0].ingredients[0].knowledge
        self.assertEqual(record.canonical_name, "Known Identity")
        self.assertEqual(record.evidence_status, "unverified")
        self.assertEqual(record.source, "unresolved")

    def test_null_canonical_identity_is_never_looked_up_or_guessed(self):
        cache = MagicMock(return_value={})
        cosing = MagicMock(return_value={})
        researcher = MagicMock()
        result = attach_ingredient_knowledge(
            normalized_response(normalized_formula(("Unreadable ???", None))),
            cache_resolver=cache,
            cosing_resolver=cosing,
            researcher=researcher,
        )

        record = result.formulas[0].ingredients[0].knowledge
        self.assertIsNone(record.canonical_name)
        self.assertEqual(record.evidence_status, "unresolved")
        cache.assert_not_called()
        cosing.assert_not_called()
        researcher.assert_not_called()

    def test_order_and_separate_formulas_are_preserved(self):
        source = normalized_response(
            normalized_formula(("Aqua", "Water"), ("Glycerin", "Glycerin"), name="Cream"),
            normalized_formula(("Panthenol", "Panthenol"), name="Balm"),
        )
        cached = {
            normalize_text(name): knowledge_item(name)
            for name in ["Water", "Glycerin", "Panthenol"]
        }

        result = attach_ingredient_knowledge(
            source,
            cache_resolver=lambda **_: cached,
            cosing_resolver=lambda **_: {},
        )

        self.assertEqual([formula.name for formula in result.formulas], ["Cream", "Balm"])
        self.assertEqual(
            [item.normalized.position for item in result.formulas[0].ingredients],
            [1, 2],
        )
        self.assertEqual(
            [item.knowledge.canonical_name for item in result.formulas[0].ingredients],
            ["Water", "Glycerin"],
        )

    def test_verified_paid_enrichment_is_cached_and_prevents_second_lookup(self):
        cache = {}
        researcher = MagicMock(return_value=AnalyzeIngredientsResponse(
            status="complete",
            items=[knowledge_item("New INCI", source="research")],
            unknown_ingredients=[], warnings=[], disclaimer="Cosmetic only.",
        ))

        def read_cache(*, ingredients):
            return {normalize_text(name): cache[normalize_text(name)] for name in ingredients if normalize_text(name) in cache}

        def write_cache(*, items, source):
            self.assertEqual(source, "researched")
            for item in items:
                cache[normalize_text(item.input_name)] = item.model_copy(
                    update={"knowledge_source": "trusted_enrichment"}
                )

        source = normalized_response(normalized_formula(("New INCI", "New INCI")))
        first = attach_ingredient_knowledge(
            source, cache_resolver=read_cache, cosing_resolver=lambda **_: {},
            researcher=researcher, cache_writer=write_cache,
        )
        second = attach_ingredient_knowledge(
            source, cache_resolver=read_cache, cosing_resolver=lambda **_: {},
            researcher=researcher, cache_writer=write_cache,
        )

        self.assertEqual(first.paid_fallback_count, 1)
        self.assertEqual(second.paid_fallback_count, 0)
        self.assertEqual(researcher.call_count, 1)
        self.assertEqual(second.formulas[0].ingredients[0].knowledge.source, "trusted_enrichment")

    def test_caution_tags_are_structured_and_free_form_medical_note_is_not_exposed(self):
        result = attach_ingredient_knowledge(
            normalized_response(normalized_formula(("Test Acid", "Test Acid"))),
            cache_resolver=lambda **_: {
                "test-acid": knowledge_item(
                    "Test Acid", functions=["exfoliant"], flags=["exfoliating_acid"],
                    note="Treats acne and cures pigmentation.",
                )
            },
            cosing_resolver=lambda **_: {},
        )

        record = result.formulas[0].ingredients[0].knowledge
        self.assertEqual(record.caution_tags, ["exfoliating_active"])
        self.assertEqual(record.cosmetic_properties, ["supports_a_smoother_looking_surface"])
        serialized = record.model_dump_json().casefold()
        self.assertNotIn("treats acne", serialized)
        self.assertNotIn("cures pigmentation", serialized)
        self.assertFalse(hasattr(record, "skin_type_score"))
        self.assertFalse(hasattr(record, "suitability_score"))


if __name__ == "__main__":
    unittest.main()
