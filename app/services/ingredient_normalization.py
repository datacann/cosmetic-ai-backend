from __future__ import annotations

import re
from collections.abc import Callable

from app.database.repository import normalize_text
from app.models.ingredient_analysis import IngredientAnalysisItem
from app.models.ingredient_normalization import (
    NormalizeIngredientsRequest,
    NormalizeIngredientsResponse,
    NormalizedIngredient,
    NormalizedIngredientFormula,
)


IngredientResolver = Callable[..., dict[str, IngredientAnalysisItem]]
CONCENTRATION_PATTERN = re.compile(
    r"^(?P<name>.+?)(?:\s+|\s*[\[(])(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>%)[\])]?$"
)

# Deliberately small and reviewable. Broader exact INCI matching comes from
# PostgreSQL/CosIng; aliases here are only deterministic equivalences.
CANONICAL_ALIASES = {
    "Water": ["Water", "Aqua", "Water (Aqua)", "Aqua/Water/Eau"],
    "Glycerin": ["Glycerin", "Glycerine"],
    "Niacinamide": ["Niacinamide", "Nicotinamide"],
    "Panthenol": ["Panthenol", "D-Panthenol"],
    "Parfum": ["Parfum", "Parfume", "Parfüm"],
}


def normalize_ingredients(
    request: NormalizeIngredientsRequest,
    *,
    resolver: IngredientResolver | None = None,
) -> NormalizeIngredientsResponse:
    """Normalize ordered ingredient data without OpenAI or web search."""
    aliases = _aliases()
    parsed_formulas: list[list[tuple[str, str, float | None, str | None]]] = []
    unresolved: list[str] = []

    for formula in request.formulas:
        parsed: list[tuple[str, str, float | None, str | None]] = []
        for raw_name in formula.ingredients:
            candidate, concentration, unit = _parse_name(raw_name)
            parsed.append((raw_name, candidate, concentration, unit))
            if normalize_text(candidate) not in aliases:
                unresolved.append(candidate)
        parsed_formulas.append(parsed)

    resolved = resolver(ingredients=list(dict.fromkeys(unresolved))) if resolver and unresolved else {}
    normalized_formulas: list[NormalizedIngredientFormula] = []
    unmatched_count = 0

    for formula, parsed in zip(request.formulas, parsed_formulas, strict=True):
        normalized_items: list[NormalizedIngredient] = []
        for position, (raw_name, candidate, concentration, unit) in enumerate(parsed, start=1):
            key = normalize_text(candidate)
            canonical_name = aliases.get(key)
            match_status = "alias" if canonical_name else "unmatched"
            if canonical_name is None:
                knowledge = resolved.get(key)
                if knowledge and knowledge.evidence_status == "verified" and knowledge.canonical_name:
                    canonical_name = knowledge.canonical_name
                    match_status = _knowledge_match_status(knowledge)
            if canonical_name is None:
                unmatched_count += 1
            normalized_items.append(
                NormalizedIngredient(
                    position=position,
                    raw_name=raw_name,
                    canonical_name=canonical_name,
                    concentration=concentration,
                    concentration_unit=unit,
                    match_status=match_status,
                )
            )
        normalized_formulas.append(
            NormalizedIngredientFormula(
                name=formula.name,
                raw_text=formula.raw_text,
                ingredients=normalized_items,
            )
        )

    return NormalizeIngredientsResponse(
        provenance=request.provenance,
        formulas=normalized_formulas,
        unmatched_count=unmatched_count,
    )


def _parse_name(raw_name: str) -> tuple[str, float | None, str | None]:
    cleaned = re.sub(r"\s+", " ", raw_name.strip()).rstrip(".,;:").strip()
    match = CONCENTRATION_PATTERN.fullmatch(cleaned)
    if not match:
        return cleaned, None, None
    name = match.group("name").strip().rstrip(".,;:").strip()
    concentration = float(match.group("value").replace(",", "."))
    return name, concentration, match.group("unit")


def _knowledge_match_status(item: IngredientAnalysisItem) -> str:
    if any("europa.eu" in url for url in item.source_urls):
        return "cosing"
    return "database"


def _aliases() -> dict[str, str]:
    return {
        normalize_text(value): canonical_name
        for canonical_name, values in CANONICAL_ALIASES.items()
        for value in values
    }
