import io
import json
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database.base import Base
from app.database.models import Ingredient
from app.services.cosing_cache import import_cosing_rows
from app.services.open_beauty_facts import lookup_open_beauty_facts


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class ExternalCatalogTests(unittest.TestCase):
    @patch("app.services.open_beauty_facts.urlopen")
    def test_open_beauty_facts_returns_product_and_formula(self, urlopen):
        urlopen.return_value = _Response(json.dumps({
            "status": "success",
            "product": {
                "brands": "Test Brand",
                "product_name": "Face Cream",
                "ingredients_text": "Aqua, Glycerin, Panthenol",
                "categories_tags": ["en:moisturizers"],
            },
        }).encode())

        match = lookup_open_beauty_facts("12345678")

        self.assertIsNotNone(match)
        self.assertEqual(match.product.brand, "Test Brand")
        self.assertEqual(match.acquisition.ingredients, ["Aqua", "Glycerin", "Panthenol"])
        self.assertEqual(match.acquisition.source.title, "Open Beauty Facts")

    @patch("app.services.open_beauty_facts.urlopen")
    def test_unsupported_lip_category_remains_unclassified(self, urlopen):
        urlopen.return_value = _Response(json.dumps({
            "status": "success",
            "product": {
                "brands": "Example",
                "product_name": "Lip Balm",
                "ingredients_text": "Cera Alba, Tocopherol",
                "categories_tags": ["en:lip-balms"],
            },
        }).encode())

        match = lookup_open_beauty_facts("12345678")

        self.assertIsNotNone(match)
        self.assertIsNone(match.product.category)

    def test_cosing_rows_are_upserted_as_verified_cache(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            count = import_cosing_rows(session, [{
                "INCI name": "Glycerin",
                "Function": "Humectant, skin conditioning",
            }])
            session.commit()
            ingredient = session.query(Ingredient).one()

            self.assertEqual(count, 1)
            self.assertEqual(ingredient.knowledge_source, "cosing")
            self.assertEqual(ingredient.evidence_status, "verified")
            self.assertEqual(ingredient.functions, ["humectant", "skin_conditioning"])


if __name__ == "__main__":
    unittest.main()
