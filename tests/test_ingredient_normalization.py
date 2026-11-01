import unittest
from unittest.mock import MagicMock

from app.models.ingredient_analysis import IngredientAnalysisItem
from app.models.ingredient_normalization import (
    NormalizeIngredientsRequest,
    RawIngredientFormula,
)
from app.services.ingredient_normalization import normalize_ingredients


def request(*ingredients: str, provenance: str = "package_image") -> NormalizeIngredientsRequest:
    return NormalizeIngredientsRequest(
        provenance=provenance,
        formulas=[RawIngredientFormula(name="Main", ingredients=list(ingredients))],
    )


class IngredientNormalizationTests(unittest.TestCase):
    def test_parfume_alias_normalizes_to_parfum_without_external_lookup(self):
        resolver = MagicMock()

        result = normalize_ingredients(
            request("Parfume"),
            resolver=resolver,
        )

        ingredient = result.formulas[0].ingredients[0]
        self.assertEqual(ingredient.raw_name, "Parfume")
        self.assertEqual(ingredient.canonical_name, "Parfum")
        self.assertEqual(ingredient.match_status, "alias")
        resolver.assert_not_called()

    def test_aqua_aliases_normalize_to_water_without_external_lookup(self):
        resolver = MagicMock()

        result = normalize_ingredients(
            request("Aqua", "Water (Aqua)", "Aqua/Water/Eau"),
            resolver=resolver,
        )

        self.assertEqual(
            [item.canonical_name for item in result.formulas[0].ingredients],
            ["Water", "Water", "Water"],
        )
        resolver.assert_not_called()

    def test_case_whitespace_and_trailing_punctuation_are_deterministic(self):
        result = normalize_ingredients(request(" NIACINAMIDE ", "niacinamide", "Niacinamide,"))

        self.assertEqual(
            [item.canonical_name for item in result.formulas[0].ingredients],
            ["Niacinamide", "Niacinamide", "Niacinamide"],
        )
        self.assertEqual(result.formulas[0].ingredients[0].raw_name, " NIACINAMIDE ")

    def test_explicit_concentration_is_preserved_but_never_inferred(self):
        result = normalize_ingredients(request("Niacinamide 10%", "Panthenol"))
        niacinamide, panthenol = result.formulas[0].ingredients

        self.assertEqual(niacinamide.canonical_name, "Niacinamide")
        self.assertEqual(niacinamide.concentration, 10)
        self.assertEqual(niacinamide.concentration_unit, "%")
        self.assertIsNone(panthenol.concentration)
        self.assertIsNone(panthenol.concentration_unit)

    def test_unknown_ingredient_remains_unmatched(self):
        result = normalize_ingredients(request("Unobtainium Flower Extract"))
        item = result.formulas[0].ingredients[0]

        self.assertEqual(item.raw_name, "Unobtainium Flower Extract")
        self.assertIsNone(item.canonical_name)
        self.assertEqual(item.match_status, "unmatched")
        self.assertEqual(result.unmatched_count, 1)

    def test_original_order_is_preserved(self):
        result = normalize_ingredients(request("Aqua", "Glycerin", "Niacinamide", "Panthenol"))

        self.assertEqual(
            [(item.position, item.canonical_name) for item in result.formulas[0].ingredients],
            [(1, "Water"), (2, "Glycerin"), (3, "Niacinamide"), (4, "Panthenol")],
        )

    def test_multiple_formulas_are_normalized_independently(self):
        normalization_request = NormalizeIngredientsRequest(
            provenance="package_image",
            formulas=[
                RawIngredientFormula(name="Cream", ingredients=["Aqua", "Glycerin"]),
                RawIngredientFormula(name="Balm", ingredients=["Panthenol", "Aqua"]),
            ],
        )

        result = normalize_ingredients(normalization_request)

        self.assertEqual([formula.name for formula in result.formulas], ["Cream", "Balm"])
        self.assertEqual(
            [item.canonical_name for item in result.formulas[0].ingredients],
            ["Water", "Glycerin"],
        )
        self.assertEqual(
            [item.canonical_name for item in result.formulas[1].ingredients],
            ["Panthenol", "Water"],
        )

    def test_package_provenance_is_preserved(self):
        result = normalize_ingredients(request("Aqua", provenance="package_image"))

        self.assertEqual(result.provenance, "package_image")

    def test_verified_database_and_cosing_matches_are_used(self):
        def resolver(*, ingredients):
            self.assertEqual(ingredients, ["Ceramide NP", "Allantoin"])
            return {
                "ceramide-np": IngredientAnalysisItem(
                    input_name="Ceramide NP", canonical_name="Ceramide NP",
                    functions=["skin_conditioning"], flags=[], note="DB",
                    evidence_status="verified", source_urls=["https://catalog.example/ceramide"],
                ),
                "allantoin": IngredientAnalysisItem(
                    input_name="Allantoin", canonical_name="Allantoin",
                    functions=["soothing"], flags=[], note="CosIng",
                    evidence_status="verified", source_urls=["https://ec.europa.eu/cosing/allantoin"],
                ),
            }

        result = normalize_ingredients(request("Ceramide NP", "Allantoin"), resolver=resolver)

        self.assertEqual(
            [item.match_status for item in result.formulas[0].ingredients],
            ["database", "cosing"],
        )

    def test_unverified_cache_value_is_not_accepted(self):
        result = normalize_ingredients(
            request("Mystery Extract"),
            resolver=lambda **_: {
                "mystery-extract": IngredientAnalysisItem(
                    input_name="Mystery Extract", canonical_name="Guessed Name",
                    functions=[], flags=[], note="Unverified",
                    evidence_status="unverified", source_urls=[],
                )
            },
        )

        item = result.formulas[0].ingredients[0]
        self.assertIsNone(item.canonical_name)
        self.assertEqual(item.match_status, "unmatched")


if __name__ == "__main__":
    unittest.main()
