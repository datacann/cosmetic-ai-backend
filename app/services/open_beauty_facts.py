from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.models.ingredient_acquisition import (
    IngredientAcquisitionResponse,
    IngredientFormula,
    IngredientSource,
)
from app.models.product_recognition import ProductRecognitionResult


BASE_URL = "https://world.openbeautyfacts.org/api/v3/product"
FIELDS = (
    "code,product_name,product_name_tr,brands,categories_tags,"
    "ingredients_text,ingredients_text_tr,ingredients"
)


@dataclass(frozen=True)
class OpenBeautyFactsMatch:
    product: ProductRecognitionResult
    acquisition: IngredientAcquisitionResponse | None


def lookup_open_beauty_facts(barcode: str, *, market: str = "TR") -> OpenBeautyFactsMatch | None:
    query = urlencode({"fields": FIELDS, "product_type": "all"})
    url = f"{BASE_URL}/{barcode}.json?{query}"
    request = Request(
        url,
        headers={
            "User-Agent": os.getenv(
                "OPEN_BEAUTY_FACTS_USER_AGENT", "CosmeticAI/0.1 (local-development)"
            ),
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=float(os.getenv("OPEN_BEAUTY_FACTS_TIMEOUT_SECONDS", "8"))) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None

    data = payload.get("product") or {}
    if not data or payload.get("status") in {0, "failure"}:
        return None
    brand = _first_text(data.get("brands"))
    product_name = _first_text(data.get("product_name_tr"), data.get("product_name"))
    if not brand or not product_name:
        return None

    ingredients_text = _first_text(data.get("ingredients_text_tr"), data.get("ingredients_text"))
    ingredients = _structured_ingredients(data.get("ingredients"))
    if not ingredients and ingredients_text:
        ingredients = _split_ingredients(ingredients_text)
    category = _category(data.get("categories_tags"))
    product = ProductRecognitionResult(
        brand=brand,
        product_name=product_name,
        variant=None,
        category=category,
        confidence=0.95,
        model_confidence=0.95,
        status="recognized",
        image_quality="good",
        quality_issues=[],
        brand_evidence="open_beauty_facts_barcode_match",
        product_name_evidence="open_beauty_facts_barcode_match",
        variant_evidence=None,
        needs_retake=False,
        retake_reasons=[],
    )
    acquisition = None
    if ingredients:
        acquisition = IngredientAcquisitionResponse(
            status="complete",
            source=IngredientSource(
                type="trusted_online",
                url=f"https://world.openbeautyfacts.org/product/{barcode}",
                title="Open Beauty Facts",
                retrieved_at=datetime.now(timezone.utc),
                match_quality="exact",
                market=market.upper(),
            ),
            raw_text=ingredients_text,
            ingredients=ingredients,
            formulas=[
                IngredientFormula(
                    name=None,
                    raw_text=ingredients_text,
                    ingredients=ingredients,
                    status="complete",
                    uncertain_segments=[],
                )
            ],
            uncertain_segments=[],
            image_quality=None,
            needs_package_photo=False,
            warnings=["ingredients_loaded_from_open_beauty_facts"],
        )
    return OpenBeautyFactsMatch(product=product, acquisition=acquisition)


def _first_text(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _structured_ingredients(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text = _first_text(item.get("text"), item.get("id"))
        if text:
            result.append(re.sub(r"^[a-z]{2}:", "", text, flags=re.IGNORECASE).strip())
    return list(dict.fromkeys(result))


def _split_ingredients(raw_text: str) -> list[str]:
    parts = re.split(r"[,;]", raw_text)
    return list(dict.fromkeys(part.strip(" ._*\n\t") for part in parts if part.strip(" ._*\n\t")))


def _category(tags: object) -> str | None:
    if not isinstance(tags, list):
        return None
    joined = " ".join(str(tag).casefold() for tag in tags)
    mappings = {
        "moistur": "Moisturizer",
        "cleanser": "Cleanser",
        "sunscreen": "Sunscreen",
        "serum": "Serum",
    }
    return next((label for needle, label in mappings.items() if needle in joined), None)
