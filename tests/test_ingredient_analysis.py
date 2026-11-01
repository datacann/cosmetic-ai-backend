import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.ingredient_analysis import (
    AnalyzeIngredientsRequest,
    IngredientAnalysisModelOutput,
    IngredientKnowledgeModelOutput,
)
from app.services.ingredient_analysis import analyze_ingredients


def response_with_citations(*, parsed, urls: list[str]):
    annotations = [
        SimpleNamespace(type="url_citation", url=url) for url in urls
    ]
    return SimpleNamespace(
        output_parsed=parsed,
        output=[SimpleNamespace(content=[SimpleNamespace(annotations=annotations)])],
    )


def response_with_search_sources(*, parsed, urls: list[str]):
    sources = [SimpleNamespace(url=url) for url in urls]
    return SimpleNamespace(
        output_parsed=parsed,
        output=[SimpleNamespace(action=SimpleNamespace(sources=sources), content=[])],
    )


class IngredientAnalysisServiceTests(unittest.TestCase):
    @patch("app.services.ingredient_analysis._get_client")
    def test_preserves_input_order_and_accepts_only_cited_claims(self, get_client):
        cited_url = "https://example.org/glycerin"
        parsed = IngredientAnalysisModelOutput(
            items=[
                IngredientKnowledgeModelOutput(
                    input_name="Glycerin",
                    canonical_name="Glycerin",
                    functions=["humectant"],
                    flags=[],
                    note="Helps retain moisture in cosmetic formulations.",
                    source_urls=[cited_url],
                ),
                IngredientKnowledgeModelOutput(
                    input_name="Mystery",
                    canonical_name="Invented",
                    functions=["emollient"],
                    flags=[],
                    note="Unsupported claim.",
                    source_urls=["https://uncited.example/item"],
                ),
            ]
        )
        parse = MagicMock(
            return_value=response_with_citations(parsed=parsed, urls=[cited_url])
        )
        get_client.return_value.responses.parse = parse

        result = analyze_ingredients(
            AnalyzeIngredientsRequest(ingredients=["Glycerin", "Mystery"])
        )

        self.assertEqual([item.input_name for item in result.items], ["Glycerin", "Mystery"])
        self.assertEqual(result.items[0].evidence_status, "verified")
        self.assertEqual(result.items[1].functions, ["unknown"])
        self.assertEqual(result.unknown_ingredients, ["Mystery"])
        self.assertEqual(result.status, "partial")
        self.assertEqual(parse.call_args.kwargs["tools"], [{"type": "web_search"}])

    @patch("app.services.ingredient_analysis._get_client")
    def test_missing_model_item_is_returned_as_unknown(self, get_client):
        parsed = IngredientAnalysisModelOutput(items=[])
        get_client.return_value.responses.parse.return_value = response_with_citations(
            parsed=parsed,
            urls=[],
        )

        result = analyze_ingredients(
            AnalyzeIngredientsRequest(ingredients=["Unknown INCI"])
        )

        self.assertEqual(result.items[0].evidence_status, "unverified")
        self.assertEqual(result.unknown_ingredients, ["Unknown INCI"])

    @patch("app.services.ingredient_analysis._get_client")
    def test_accepts_web_search_action_source_and_ignores_query_string(self, get_client):
        source_url = "https://example.org/ingredient/glycerin"
        parsed = IngredientAnalysisModelOutput(
            items=[
                IngredientKnowledgeModelOutput(
                    input_name="Glycerin",
                    canonical_name="Glycerin",
                    functions=["humectant"],
                    flags=[],
                    note="Cosmetic function.",
                    source_urls=[source_url + "?utm_source=test"],
                )
            ]
        )
        get_client.return_value.responses.parse.return_value = response_with_search_sources(
            parsed=parsed,
            urls=[source_url],
        )

        result = analyze_ingredients(
            AnalyzeIngredientsRequest(ingredients=["Glycerin"])
        )

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.items[0].evidence_status, "verified")


class IngredientAnalysisRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_rejects_empty_ingredient_list(self):
        response = self.client.post(
            "/v1/analyze-ingredients",
            json={"ingredients": []},
        )

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
