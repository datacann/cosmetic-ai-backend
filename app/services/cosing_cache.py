from __future__ import annotations

import csv
import json
import uuid
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Ingredient
from app.database.repository import normalize_text
from app.models.ingredient_analysis import IngredientAnalysisItem


COSING_SOURCE_URL = (
    "https://single-market-economy.ec.europa.eu/sectors/cosmetics/"
    "cosmetic-ingredient-database_en"
)

FUNCTION_MAP = {
    "humectant": "humectant",
    "emollient": "emollient",
    "skin conditioning": "skin_conditioning",
    "skin-conditioning agent": "skin_conditioning",
    "solvent": "solvent",
    "surfactant": "surfactant",
    "emulsifying": "emulsifier",
    "preservative": "preservative",
    "antioxidant": "antioxidant",
    "perfuming": "fragrance",
    "colorant": "colorant",
    "uv filter": "uv_filter",
    "buffering": "ph_adjuster",
    "chelating": "chelating_agent",
    "soothing": "soothing",
    "hair conditioning": "other",
    "oral care": "other",
    "viscosity controlling": "other",
    "skin protecting": "skin_conditioning",
    "cleansing": "surfactant",
    "film forming": "other",
}

CONFIG_URL = "https://ec.europa.eu/growth/tools-databases/cosing/assets/env-json-config.json"


def import_cosing_csv(session: Session, path: str | Path) -> int:
    """Import an official CosIng CSV/export into the reusable ingredient cache."""
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        count = import_cosing_rows(session, reader)
    session.commit()
    return count


def import_cosing_rows(session: Session, rows: Iterable[dict[str, str]]) -> int:
    changed = 0
    for row in rows:
        name = _field(row, "inci name", "inci_name", "inci", "name")
        if not name:
            continue
        functions = _functions(_field(row, "function", "functions", "cosmetic function"))
        if not functions:
            functions = ["other"]
        normalized = normalize_text(name)
        ingredient = session.scalar(
            select(Ingredient).where(Ingredient.normalized_name == normalized)
        )
        if ingredient is None:
            ingredient = Ingredient(inci_name=name, normalized_name=normalized)
            session.add(ingredient)
        ingredient.inci_name = name
        ingredient.functions = functions
        ingredient.flags = _flags(functions)
        ingredient.evidence_status = "verified"
        ingredient.knowledge_source = "cosing"
        ingredient.source_urls = [COSING_SOURCE_URL]
        ingredient.knowledge_note = (
            "CosIng resmi ingredient veri setinden içe aktarıldı; CosIng kaydı tek başına "
            "ürünün güvenli veya onaylı olduğu anlamına gelmez."
        )
        changed += 1
    session.flush()
    return changed


def lookup_cosing_ingredients(names: list[str]) -> dict[str, IngredientAnalysisItem]:
    """Resolve exact active INCI entries via the API used by the official CosIng UI."""
    if not names:
        return {}
    workers = min(6, len(names))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        items = executor.map(_lookup_one, names)
    return {
        normalize_text(item.input_name): item
        for item in items
        if item is not None
    }


def resolve_ingredient_knowledge(repository, *, ingredients: list[str]):
    cached = repository.find_cached_ingredient_items(ingredients=ingredients)
    missing = [name for name in ingredients if normalize_text(name) not in cached]
    cosing = lookup_cosing_ingredients(missing)
    if cosing:
        repository.save_cached_ingredient_items(items=list(cosing.values()), source="cosing")
    return {**cached, **cosing}


def _lookup_one(input_name: str) -> IngredientAnalysisItem | None:
    try:
        config = _config()
        query = {"bool": {"must": [
            {"text": {"query": input_name, "fields": ["inciName"], "defaultOperator": "AND"}},
            {"term": {"itemType": "ingredient"}},
            {"term": {"status": "Active"}},
        ]}}
        boundary = "----CosmeticAI" + uuid.uuid4().hex
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="query"; filename="blob"\r\n'
            "Content-Type: application/json\r\n\r\n"
            + json.dumps(query)
            + f"\r\n--{boundary}--\r\n"
        ).encode()
        params = urlencode({
            "apiKey": config["euSearchApiKey"], "text": input_name,
            "pageSize": 10, "pageNumber": 1,
        })
        request = Request(
            f"{config['euSearchApiUrl']}?{params}", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urlopen(request, timeout=12) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError):
        return None

    expected = normalize_text(input_name)
    for result in payload.get("results") or []:
        metadata = result.get("metadata") or {}
        inci_values = metadata.get("inciName") or []
        inci_name = inci_values[0] if inci_values else None
        if not inci_name or normalize_text(inci_name) != expected:
            continue
        functions = _functions(",".join(metadata.get("functionName") or [])) or ["other"]
        substance_ids = metadata.get("substanceId") or []
        source_url = (
            f"https://ec.europa.eu/growth/tools-databases/cosing/details/{substance_ids[0]}"
            if substance_ids else COSING_SOURCE_URL
        )
        return IngredientAnalysisItem(
            input_name=input_name,
            canonical_name=inci_name.title(),
            functions=functions,
            flags=_flags(functions),
            note=(
                "CosIng resmi ingredient kaydındaki kozmetik fonksiyonları. Kayıt, tek başına "
                "ingredient'in güvenli veya kullanım için onaylı olduğu anlamına gelmez."
            ),
            evidence_status="verified",
            source_urls=[source_url],
            knowledge_source="cosing",
        )
    return None


@lru_cache(maxsize=1)
def _config() -> dict[str, str]:
    with urlopen(CONFIG_URL, timeout=12) as response:
        return json.load(response)


def _field(row: dict[str, str], *names: str) -> str:
    folded = {key.strip().casefold(): (value or "").strip() for key, value in row.items() if key}
    return next((folded[name] for name in names if folded.get(name)), "")


def _functions(raw: str) -> list[str]:
    lowered = raw.casefold()
    values = [mapped for label, mapped in FUNCTION_MAP.items() if label in lowered]
    return list(dict.fromkeys(values))


def _flags(functions: list[str]) -> list[str]:
    return [name for name in ("fragrance", "colorant", "uv_filter", "preservative") if name in functions]
