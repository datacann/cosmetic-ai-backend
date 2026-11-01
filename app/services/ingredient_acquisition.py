import base64
import os
import re
import threading
import time
import unicodedata
from difflib import SequenceMatcher
from datetime import datetime, timezone

from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from app.models.ingredient_acquisition import (
    IngredientAcquisitionResponse,
    IngredientFormula,
    IngredientSource,
    OnlineIngredientModelOutput,
    PackageIngredientModelOutput,
)
from app.services.openai_usage import record_openai_usage


load_dotenv()

DEFAULT_MODEL = "gpt-5.4-mini"

# OCR may preserve a printed line-break hyphen inside an otherwise known INCI
# name. Keep this allow-list deliberately narrow: arbitrary unreadable fragments
# must remain uncertain instead of being guessed.
_STRONGLY_JUSTIFIED_LINE_BREAK_REPAIRS = {
    "ethylhexylglyce-rin": "Ethylhexylglycerin",
}

# Deterministic equivalents seen on multilingual INCI declarations. This is
# intentionally reviewable and is used only for duplicate detection; returned
# ingredient spelling still comes from the selected package block.
_DUPLICATE_COMPARISON_ALIASES = {
    "aqua": "aqua",
    "water": "aqua",
    "phenoxyethanol": "phenoxyethanol",
    "fenoxietanol": "phenoxyethanol",
    "benzoic acid": "benzoic acid",
    "acido benzoico": "benzoic acid",
    "acid benzoic": "benzoic acid",
    "dehydroacetic acid": "dehydroacetic acid",
    "acido dehidroacetico": "dehydroacetic acid",
    "acid dehidroacetic": "dehydroacetic acid",
    "parfum": "parfum",
    "parfume": "parfum",
    "perfume": "parfum",
    "polysorbate 20": "polysorbate 20",
    "polisorbato 20": "polysorbate 20",
    "polisorbato": "polysorbate 20",
    "cocamidopropyl betaine": "cocamidopropyl betaine",
    "cocamidopropil betaina": "cocamidopropyl betaine",
    "cocamidopropyl betaina": "cocamidopropyl betaine",
    "peg 7 glyceryl cocoate": "peg-7 glyceryl cocoate",
    "peg 7 cocoato de glicerilo": "peg-7 glyceryl cocoate",
    "gliceril peg 7 cocoat": "peg-7 glyceryl cocoate",
    "tetrasodium edta": "tetrasodium edta",
    "edta tetrasodico": "tetrasodium edta",
    "edta tetrasodic": "tetrasodium edta",
    "citric acid": "citric acid",
    "acido citrico": "citric acid",
    "acid citric": "citric acid",
}

_NEGATIVE_LOOKUP_CACHE: dict[tuple[str, str, str, str], float] = {}
_LOOKUP_LOCKS = tuple(threading.Lock() for _ in range(32))


class IngredientAcquisitionError(Exception):
    """Base exception for safe API-layer error mapping."""


class IngredientAcquisitionConfigurationError(IngredientAcquisitionError):
    pass


class IngredientAcquisitionUnavailableError(IngredientAcquisitionError):
    pass


def _get_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise IngredientAcquisitionConfigurationError(
            "OPENAI_API_KEY ortam değişkeni tanımlı değil."
        )
    return OpenAI(
        api_key=api_key,
        timeout=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "45")),
        max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "0")),
    )


def acquire_ingredients(
    *,
    brand: str,
    product_name: str,
    variant: str | None,
    market: str,
    images: list[tuple[bytes, str]],
) -> IngredientAcquisitionResponse:
    if images:
        return _extract_from_package(images=images, market=market)
    return _lookup_online(
        brand=brand,
        product_name=product_name,
        variant=variant,
        market=market,
    )


def _extract_from_package(
    *,
    images: list[tuple[bytes, str]],
    market: str,
) -> IngredientAcquisitionResponse:
    content: list[dict[str, str]] = [
        {
            "type": "input_text",
            "text": (
                "Transcribe the complete cosmetic ingredient declaration across these "
                "package photos. The images may overlap. Preserve ingredient order, merge "
                "overlap once, and never infer hidden or unreadable text. If the package "
                "contains multiple products or separately named formulas, return each one "
                "as a separate formulas item. Never merge their ingredient declarations. "
                "Do not normalize, translate, explain, or evaluate ingredients."
            ),
        }
    ]
    for image_bytes, content_type in images:
        encoded = base64.b64encode(image_bytes).decode("utf-8")
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:{content_type};base64,{encoded}",
                # Ingredient declarations are small, dense text. Keep OCR quality
                # deterministic instead of allowing the API to choose low detail.
                "detail": "high",
            }
        )

    model = os.getenv("OPENAI_INGREDIENT_EXTRACTION_MODEL", DEFAULT_MODEL)
    try:
        response = _get_client().responses.parse(
            model=model,
            text_format=PackageIngredientModelOutput,
            instructions=(
                "You are a conservative OCR extractor for cosmetic ingredient labels. "
                "Use only characters visibly present on the package. raw_text must preserve "
                "the declarations; each formula's ingredients must be split from that same "
                "formula text in the same order. Use the visibly printed formula heading as "
                "name. Unknown fragments belong in uncertain_segments, never as guesses."
            ),
            input=[{"role": "user", "content": content}],
        )
        record_openai_usage(
            operation="ingredient_extraction",
            model=model,
            response=response,
            image_count=len(images),
        )
    except (APIConnectionError, APITimeoutError) as exc:
        raise IngredientAcquisitionUnavailableError(
            "OpenAI servisine şu anda ulaşılamıyor."
        ) from exc
    except APIStatusError as exc:
        _raise_status_error(exc)

    output = response.output_parsed
    if output is None:
        raise IngredientAcquisitionUnavailableError(
            "OpenAI yanıtı doğrulanmış içerik verisi içermiyor."
        )

    formulas: list[IngredientFormula] = []
    removed_label_codes: list[str] = []
    removed_uncertain: list[str] = []
    rejected_non_declarations = 0
    duplicate_sections_removed = False
    for formula in output.formulas:
        if not _looks_like_ingredient_declaration(formula.raw_text, formula.ingredients):
            rejected_non_declarations += 1
            continue
        cleaned, formula_codes = _clean_extracted_ingredients(formula.ingredients)
        uncertain_names = {
            segment.strip().rstrip(".,;:").casefold()
            for segment in formula.uncertain_segments
        }
        formula_removed_uncertain = [
            ingredient for ingredient in cleaned if ingredient.casefold() in uncertain_names
        ]
        cleaned = [
            ingredient for ingredient in cleaned if ingredient.casefold() not in uncertain_names
        ]
        cleaned, section_removed = _deduplicate_repeated_sections(cleaned)
        duplicate_sections_removed = duplicate_sections_removed or section_removed
        removed_label_codes.extend(formula_codes)
        removed_uncertain.extend(formula_removed_uncertain)
        if cleaned:
            formulas.append(
                IngredientFormula(
                    name=formula.name,
                    raw_text=", ".join(cleaned) if section_removed else formula.raw_text,
                    ingredients=cleaned,
                    status=formula.status,
                    uncertain_segments=[*formula.uncertain_segments, *formula_codes],
                )
            )

    formulas, duplicate_formulas_removed = _deduplicate_formulas(formulas)
    duplicate_sections_removed = (
        duplicate_sections_removed or duplicate_formulas_removed
    )

    usable = output.status in {"complete", "partial"} and bool(formulas)
    uncertain_segments = [*output.uncertain_segments, *removed_label_codes]
    warnings = []
    if output.status == "unreadable":
        warnings.append("ingredient_label_unreadable")
    elif output.status == "partial":
        warnings.append("ingredient_text_incomplete")
    if removed_label_codes:
        warnings.append("non_ingredient_label_code_removed")
    if removed_uncertain:
        warnings.append("uncertain_ingredients_removed")
    if len(formulas) > 1:
        warnings.append("multiple_formulas_detected")
    if rejected_non_declarations:
        warnings.append("non_ingredient_text_rejected")
    if duplicate_sections_removed:
        warnings.append("duplicate_formula_sections_removed")

    legacy_ingredients = formulas[0].ingredients if len(formulas) == 1 else []

    return IngredientAcquisitionResponse(
        status=output.status,
        source=IngredientSource(
            type="package",
            match_quality="exact",
            market=market,
        ),
        raw_text=output.raw_text,
        ingredients=legacy_ingredients if usable else [],
        formulas=formulas if usable else [],
        uncertain_segments=uncertain_segments,
        image_quality=output.image_quality,
        needs_package_photo=output.status != "complete" or not usable,
        warnings=warnings,
    )


def _lookup_online(
    *,
    brand: str,
    product_name: str,
    variant: str | None,
    market: str,
) -> IngredientAcquisitionResponse:
    cache_key = tuple(
        value.strip().casefold()
        for value in (brand, product_name, variant or "", market.upper())
    )
    if _negative_lookup_cached(cache_key):
        return _online_not_found(market=market)

    # Serialize only identical cache stripes. The second request rechecks the
    # negative cache after waiting, so one miss cannot trigger duplicate paid
    # research while the first request is still in flight.
    lock = _LOOKUP_LOCKS[hash(cache_key) % len(_LOOKUP_LOCKS)]
    with lock:
        if _negative_lookup_cached(cache_key):
            return _online_not_found(market=market)
        result = _lookup_online_uncached(
            brand=brand,
            product_name=product_name,
            variant=variant,
            market=market,
        )
        if result.status == "not_found":
            ttl = max(
                0.0,
                float(os.getenv("INGREDIENT_LOOKUP_NEGATIVE_CACHE_TTL_SECONDS", "900")),
            )
            if ttl:
                _NEGATIVE_LOOKUP_CACHE[cache_key] = time.monotonic() + ttl
                if len(_NEGATIVE_LOOKUP_CACHE) > 512:
                    _NEGATIVE_LOOKUP_CACHE.pop(next(iter(_NEGATIVE_LOOKUP_CACHE)))
        return result


def _negative_lookup_cached(cache_key: tuple[str, str, str, str]) -> bool:
    expires_at = _NEGATIVE_LOOKUP_CACHE.get(cache_key)
    if expires_at is None:
        return False
    if expires_at <= time.monotonic():
        _NEGATIVE_LOOKUP_CACHE.pop(cache_key, None)
        return False
    return True


def _lookup_online_uncached(
    *,
    brand: str,
    product_name: str,
    variant: str | None,
    market: str,
) -> IngredientAcquisitionResponse:
    identity = f"brand={brand}; product={product_name}; variant={variant or 'none'}; market={market}"
    model = os.getenv("OPENAI_INGREDIENT_LOOKUP_MODEL", DEFAULT_MODEL)
    try:
        response = _get_client().responses.parse(
            model=model,
            text_format=OnlineIngredientModelOutput,
            tools=[{"type": "web_search"}],
            max_tool_calls=int(os.getenv("OPENAI_MAX_WEB_SEARCH_CALLS", "1")),
            include=["web_search_call.action.sources"],
            instructions=(
                "Find the cosmetic ingredient list for the exact supplied product identity. "
                "Prefer the manufacturer's official page for the requested market. A source "
                "is exact only when brand, product name, and variant all match. Never combine "
                "lists from different variants or markets. Copy only a source-published list, "
                "preserve order, and do not normalize or invent ingredients. If exact identity "
                "or a published ingredient list cannot be verified, return status=not_found, "
                "match_quality=none, source_type=none, null source fields, and an empty list."
            ),
            input=f"Locate ingredients for: {identity}",
        )
        record_openai_usage(operation="ingredient_lookup", model=model, response=response)
    except (APIConnectionError, APITimeoutError) as exc:
        raise IngredientAcquisitionUnavailableError(
            "OpenAI web aramasına şu anda ulaşılamıyor."
        ) from exc
    except APIStatusError as exc:
        _raise_status_error(exc)

    output = response.output_parsed
    if output is None:
        raise IngredientAcquisitionUnavailableError(
            "OpenAI yanıtı doğrulanmış çevrimiçi içerik verisi içermiyor."
        )

    cited_urls = _citation_urls(response)
    source_is_cited = bool(output.source_url and output.source_url in cited_urls)
    accepted = (
        output.match_quality == "exact"
        and output.source_type in {"official_online", "trusted_online"}
        and source_is_cited
        and output.status in {"complete", "partial"}
        and bool(output.ingredients)
    )
    if not accepted:
        return _online_not_found(market=market)

    warnings = []
    if output.status == "partial":
        warnings.append("ingredient_text_incomplete")
    if output.source_type == "trusted_online":
        warnings.append("source_is_not_official")

    return IngredientAcquisitionResponse(
        status=output.status,
        source=IngredientSource(
            type=output.source_type,
            url=output.source_url,
            title=output.source_title,
            retrieved_at=datetime.now(timezone.utc),
            match_quality="exact",
            market=market,
        ),
        raw_text=output.raw_text,
        ingredients=output.ingredients,
        formulas=[
            IngredientFormula(
                name=product_name,
                raw_text=output.raw_text,
                ingredients=output.ingredients,
                status=output.status,
                uncertain_segments=output.uncertain_segments,
            )
        ],
        uncertain_segments=output.uncertain_segments,
        needs_package_photo=False,
        warnings=warnings,
    )


def _citation_urls(response: object) -> set[str]:
    urls: set[str] = set()
    for item in getattr(response, "output", []):
        for content in getattr(item, "content", []):
            for annotation in getattr(content, "annotations", []):
                if getattr(annotation, "type", None) == "url_citation":
                    url = getattr(annotation, "url", None)
                    if url:
                        urls.add(url)
    return urls


def _clean_extracted_ingredients(
    ingredients: list[str],
) -> tuple[list[str], list[str]]:
    cleaned: list[str] = []
    removed_label_codes: list[str] = []
    for ingredient in ingredients:
        candidate = ingredient.strip()
        if re.fullmatch(r"\[[A-Z]{1,5}-?\d+\]", candidate, flags=re.IGNORECASE):
            removed_label_codes.append(candidate)
            continue
        candidate = candidate.rstrip(".,;: ")
        repair_key = re.sub(r"\s+", "", candidate).casefold()
        candidate = _STRONGLY_JUSTIFIED_LINE_BREAK_REPAIRS.get(
            repair_key,
            candidate,
        )
        if candidate:
            cleaned.append(candidate)
    return cleaned, removed_label_codes


def _deduplicate_repeated_sections(
    ingredients: list[str],
) -> tuple[list[str], bool]:
    """Collapse confidently repeated language blocks inside one OCR formula."""
    if len(ingredients) < 6:
        return ingredients, False

    keys = [_comparison_key(item) for item in ingredients]
    candidates: list[tuple[tuple[int, float, int], list[list[str]]]] = []
    for boundary_key in dict.fromkeys(keys):
        positions = [index for index, key in enumerate(keys) if key == boundary_key]
        if len(positions) < 2:
            continue
        blocks: list[list[str]] = []
        start = 0
        for position in positions:
            block = ingredients[start : position + 1]
            if block:
                blocks.append(block)
            start = position + 1
        if start < len(ingredients):
            blocks.append(ingredients[start:])
        substantial = [block for block in blocks if len(block) >= 3]
        if len(substantial) < 2:
            continue
        similar_pairs = [
            _sequence_similarity(left, right)
            for index, left in enumerate(substantial)
            for right in substantial[index + 1 :]
        ]
        confident_pairs = [score for score in similar_pairs if score >= 0.80]
        if len(confident_pairs) < len(substantial) - 1:
            continue
        coverage = sum(len(block) for block in substantial) / len(ingredients)
        if coverage < 0.85:
            continue
        candidates.append(
            (
                (len(confident_pairs), sum(confident_pairs), len(substantial)),
                substantial,
            )
        )

    if not candidates:
        return ingredients, False
    _, blocks = max(candidates, key=lambda item: item[0])
    selected = max(blocks, key=_block_quality)
    return selected, True


def _deduplicate_formulas(
    formulas: list[IngredientFormula],
) -> tuple[list[IngredientFormula], bool]:
    kept: list[IngredientFormula] = []
    removed = False
    for formula in formulas:
        duplicate_index = next(
            (
                index
                for index, existing in enumerate(kept)
                if _sequence_similarity(existing.ingredients, formula.ingredients) >= 0.80
            ),
            None,
        )
        if duplicate_index is None:
            kept.append(formula)
            continue
        removed = True
        existing = kept[duplicate_index]
        if _formula_quality(formula) > _formula_quality(existing):
            kept[duplicate_index] = formula
    return kept, removed


def _sequence_similarity(left: list[str], right: list[str]) -> float:
    left_keys = [_comparison_key(item) for item in left]
    right_keys = [_comparison_key(item) for item in right]
    if not left_keys or not right_keys:
        return 0.0
    length_ratio = min(len(left_keys), len(right_keys)) / max(len(left_keys), len(right_keys))
    if length_ratio < 0.75:
        return 0.0
    overlap = len(set(left_keys) & set(right_keys)) / max(
        len(set(left_keys)), len(set(right_keys))
    )
    order = SequenceMatcher(a=left_keys, b=right_keys, autojunk=False).ratio()
    if overlap < 0.80 or order < 0.75:
        return 0.0
    return min(overlap, order, length_ratio)


def _comparison_key(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value.casefold())
    ascii_value = "".join(char for char in folded if not unicodedata.combining(char))
    normalized = re.sub(r"[^a-z0-9]+", " ", ascii_value).strip()
    return _DUPLICATE_COMPARISON_ALIASES.get(normalized, normalized)


def _block_quality(block: list[str]) -> tuple[int, int]:
    canonical_spellings = sum(
        _comparison_key(item) == re.sub(r"[^a-z0-9]+", " ", item.casefold()).strip()
        for item in block
    )
    return canonical_spellings, len(block)


def _formula_quality(formula: IngredientFormula) -> tuple[int, int, int]:
    return (
        1 if formula.status == "complete" else 0,
        _block_quality(formula.ingredients)[0],
        len(formula.raw_text or ""),
    )


def _looks_like_ingredient_declaration(
    raw_text: str | None,
    ingredients: list[str],
) -> bool:
    return bool(raw_text and "," in raw_text and len(ingredients) >= 2)


def _online_not_found(*, market: str) -> IngredientAcquisitionResponse:
    return IngredientAcquisitionResponse(
        status="not_found",
        source=IngredientSource(
            type="none",
            match_quality="none",
            market=market,
        ),
        ingredients=[],
        formulas=[],
        uncertain_segments=[],
        needs_package_photo=True,
        warnings=["exact_online_ingredient_source_not_verified"],
    )


def _raise_status_error(exc: APIStatusError) -> None:
    if exc.status_code in {400, 401, 403, 404}:
        raise IngredientAcquisitionConfigurationError(
            "OpenAI kimlik bilgileri veya model/araç ayarı geçersiz."
        ) from exc
    raise IngredientAcquisitionUnavailableError(
        f"OpenAI servisi isteği tamamlayamadı (status={exc.status_code})."
    ) from exc
