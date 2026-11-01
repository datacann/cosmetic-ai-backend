import os
from urllib.parse import urlsplit

from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from app.models.ingredient_analysis import (
    AnalyzeIngredientsRequest,
    AnalyzeIngredientsResponse,
    IngredientAnalysisItem,
    IngredientAnalysisModelOutput,
)
from app.services.openai_usage import record_openai_usage


load_dotenv()

DEFAULT_MODEL = "gpt-5.4-mini"
DISCLAIMER = (
    "Bu çıktı kozmetik içerik bilgisidir; tıbbi tanı, tedavi veya güvenlik garantisi değildir."
)


class IngredientAnalysisError(Exception):
    """Base exception for safe API-layer error mapping."""


class IngredientAnalysisConfigurationError(IngredientAnalysisError):
    pass


class IngredientAnalysisUnavailableError(IngredientAnalysisError):
    pass


def _get_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise IngredientAnalysisConfigurationError(
            "OPENAI_API_KEY ortam değişkeni tanımlı değil."
        )
    return OpenAI(
        api_key=api_key,
        timeout=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "45")),
        max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "0")),
    )


def analyze_ingredients(request: AnalyzeIngredientsRequest) -> AnalyzeIngredientsResponse:
    numbered = "\n".join(
        f"{index + 1}. {ingredient}"
        for index, ingredient in enumerate(request.ingredients)
    )
    model = os.getenv("OPENAI_INGREDIENT_ANALYSIS_MODEL", DEFAULT_MODEL)
    try:
        response = _get_client().responses.parse(
            model=model,
            text_format=IngredientAnalysisModelOutput,
            tools=[{"type": "web_search"}],
            max_tool_calls=int(os.getenv("OPENAI_MAX_WEB_SEARCH_CALLS", "1")),
            include=["web_search_call.action.sources"],
            instructions=(
                "Build conservative cosmetic ingredient knowledge using authoritative primary "
                "sources such as regulator ingredient databases and expert safety assessments. "
                "Return exactly one item for every supplied input, in the same order, and copy "
                "input_name exactly. Do not infer concentration, diagnose conditions, recommend "
                "treatment, claim complete safety, or calculate a score. canonical_name may only "
                "standardize a verified synonym. Use only the allowed function and flag values. "
                "Write each short note in clear Turkish while preserving INCI names. "
                "Every factual classification needs at least one source URL that appears in the "
                "web-search citations. If it cannot be verified, use canonical_name=null, "
                "functions=[unknown], flags=[], a short uncertainty note, and no source URLs."
            ),
            input=(
                f"Market context: {request.market}\n"
                f"Formula: {request.formula_name or 'unnamed'}\n"
                f"Ingredients:\n{numbered}"
            ),
        )
        record_openai_usage(
            operation="ingredient_knowledge_research",
            model=model,
            response=response,
        )
    except (APIConnectionError, APITimeoutError) as exc:
        raise IngredientAnalysisUnavailableError(
            "OpenAI içerik bilgi servisine şu anda ulaşılamıyor."
        ) from exc
    except APIStatusError as exc:
        _raise_status_error(exc)

    output = response.output_parsed
    if output is None:
        raise IngredientAnalysisUnavailableError(
            "OpenAI yanıtı doğrulanmış içerik analizi içermiyor."
        )

    cited_urls = _citation_urls(response)
    output_by_name = {item.input_name.casefold(): item for item in output.items}
    items: list[IngredientAnalysisItem] = []
    unknown: list[str] = []

    for input_name in request.ingredients:
        candidate = output_by_name.get(input_name.casefold())
        verified_urls = (
            [url for url in candidate.source_urls if _is_cited_url(url, cited_urls)]
            if candidate
            else []
        )
        verified = bool(candidate and verified_urls and candidate.functions != ["unknown"])
        if not verified:
            unknown.append(input_name)
            items.append(
                IngredientAnalysisItem(
                    input_name=input_name,
                    canonical_name=None,
                    functions=["unknown"],
                    flags=[],
                    note="Bu içerik için doğrulanmış bilgi bulunamadı.",
                    evidence_status="unverified",
                    source_urls=[],
                    knowledge_source="research",
                )
            )
            continue

        items.append(
            IngredientAnalysisItem(
                input_name=input_name,
                canonical_name=candidate.canonical_name,
                functions=candidate.functions,
                flags=candidate.flags,
                note=candidate.note,
                evidence_status="verified",
                source_urls=verified_urls,
                knowledge_source="research",
            )
        )

    warnings = []
    if unknown:
        warnings.append("some_ingredients_unverified")

    return AnalyzeIngredientsResponse(
        formula_name=request.formula_name,
        status="partial" if unknown else "complete",
        items=items,
        unknown_ingredients=unknown,
        warnings=warnings,
        disclaimer=DISCLAIMER,
    )


def _citation_urls(response: object) -> set[str]:
    urls: set[str] = set()
    for item in getattr(response, "output", None) or []:
        action = getattr(item, "action", None)
        for source in (getattr(action, "sources", None) or []) if action else []:
            url = getattr(source, "url", None)
            if url:
                urls.add(url)
        for content in getattr(item, "content", None) or []:
            for annotation in getattr(content, "annotations", None) or []:
                if getattr(annotation, "type", None) == "url_citation":
                    url = getattr(annotation, "url", None)
                    if url:
                        urls.add(url)
    return urls


def _is_cited_url(candidate: str, cited_urls: set[str]) -> bool:
    candidate_key = _source_key(candidate)
    return bool(candidate_key) and any(
        candidate_key == _source_key(cited_url) for cited_url in cited_urls
    )


def _source_key(url: str) -> tuple[str, str] | None:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return parsed.hostname.casefold(), parsed.path.rstrip("/") or "/"


def _raise_status_error(exc: APIStatusError) -> None:
    if exc.status_code in {400, 401, 403, 404}:
        raise IngredientAnalysisConfigurationError(
            "OpenAI kimlik bilgileri veya içerik analiz modeli/araç ayarı geçersiz."
        ) from exc
    raise IngredientAnalysisUnavailableError(
        f"OpenAI içerik analizi isteğini tamamlayamadı (status={exc.status_code})."
    ) from exc
