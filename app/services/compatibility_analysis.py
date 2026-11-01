import json
import os

from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from app.models.compatibility_analysis import (
    AnalyzeCompatibilityRequest,
    AnalyzeCompatibilityResponse,
    CompatibilityAnalysisModelOutput,
    CompatibilityBreakdownResponse,
    CompatibilityDimensionModelOutput,
    CompatibilityDimensionResult,
)
from app.services.openai_usage import record_openai_usage


load_dotenv()

DEFAULT_MODEL = "gpt-5.4-mini"
DISCLAIMER = (
    "Bu değerlendirme kozmetik uyum bilgisidir; tıbbi tanı, tedavi, etkinlik veya güvenlik garantisi değildir."
)


class CompatibilityAnalysisError(Exception):
    """Base exception for safe API-layer error mapping."""


class CompatibilityAnalysisConfigurationError(CompatibilityAnalysisError):
    pass


class CompatibilityAnalysisUnavailableError(CompatibilityAnalysisError):
    pass


def _get_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise CompatibilityAnalysisConfigurationError(
            "OPENAI_API_KEY ortam değişkeni tanımlı değil."
        )
    return OpenAI(
        api_key=api_key,
        timeout=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "45")),
        max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "0")),
    )


def analyze_compatibility(
    request: AnalyzeCompatibilityRequest,
) -> AnalyzeCompatibilityResponse:
    verified = [
        ingredient
        for ingredient in request.ingredients
        if ingredient.evidence_status == "verified"
    ]
    warnings = []
    if len(verified) != len(request.ingredients):
        warnings.append("unverified_ingredients_excluded")

    avoid_matches = _avoid_matches(request)
    if avoid_matches:
        warnings.append("user_avoid_preference_match")

    if not verified:
        return _unknown_response(
            request=request,
            avoid_matches=avoid_matches,
            warnings=[*warnings, "no_verified_ingredient_knowledge"],
        )

    payload = {
        "product": request.product.model_dump(),
        "verified_ingredients": [ingredient.model_dump() for ingredient in verified],
        "skin_profile": request.skin_profile.model_dump(),
        "routine": [product.model_dump() for product in request.routine],
    }
    model = os.getenv("OPENAI_COMPATIBILITY_MODEL", DEFAULT_MODEL)
    try:
        response = _get_client().responses.parse(
            model=model,
            reasoning={
                "effort": os.getenv("OPENAI_COMPATIBILITY_REASONING_EFFORT", "none")
            },
            text_format=CompatibilityAnalysisModelOutput,
            instructions=(
                "Assess cosmetic suitability signals from only the supplied verified ingredient "
                "knowledge and user context. Return five independent dimensions. Use supportive "
                "only for a relevant positive signal, caution only for a context-dependent concern, "
                "neutral when neither is supported, and unknown when user context or evidence is "
                "missing. evidence_ingredients must copy exact input_name values from the supplied "
                "verified ingredients. Never invent an ingredient, concentration, diagnosis, "
                "treatment, safety claim, efficacy guarantee, score, verdict, or frequency. Write "
                "brief explanations in Turkish. Missing profile "
                "information must not be treated negatively. Avoid-preference matching is handled "
                "by backend and must not be inferred here."
            ),
            input=json.dumps(payload, ensure_ascii=False),
        )
        record_openai_usage(operation="compatibility_analysis", model=model, response=response)
    except (APIConnectionError, APITimeoutError) as exc:
        raise CompatibilityAnalysisUnavailableError(
            "OpenAI uyumluluk servisine şu anda ulaşılamıyor."
        ) from exc
    except APIStatusError as exc:
        _raise_status_error(exc)

    output = response.output_parsed
    if output is None:
        raise CompatibilityAnalysisUnavailableError(
            "OpenAI yanıtı doğrulanmış uyumluluk değerlendirmesi içermiyor."
        )

    allowed_names = {ingredient.input_name.casefold(): ingredient.input_name for ingredient in verified}
    availability = {
        "skin_type": bool(request.skin_profile.skin_type),
        "sensitivity": bool(request.skin_profile.sensitivity),
        "goals": bool(request.skin_profile.goals),
        "appearance": bool(request.skin_profile.appearance),
        "routine": bool(request.routine),
    }
    dimensions = {}
    for name in availability:
        dimensions[name] = _validate_dimension(
            getattr(output, name),
            available=availability[name],
            allowed_names=allowed_names,
        )

    strengths = [
        dimension.explanation
        for dimension in dimensions.values()
        if dimension.assessment == "supportive"
    ]
    cautions = [
        dimension.explanation
        for dimension in dimensions.values()
        if dimension.assessment == "caution"
    ]
    if avoid_matches:
        cautions.append(
            "Tercihen kaçınmak istediğin içeriklerden biri veya birkaçı bu formülde bulunuyor."
        )

    return AnalyzeCompatibilityResponse(
        product=request.product,
        compatibility=CompatibilityBreakdownResponse(**dimensions),
        strengths=strengths,
        cautions=cautions,
        avoid_matches=avoid_matches,
        usage_note=None,
        warnings=warnings,
        disclaimer=DISCLAIMER,
    )


def _validate_dimension(
    dimension: CompatibilityDimensionModelOutput,
    *,
    available: bool,
    allowed_names: dict[str, str],
) -> CompatibilityDimensionResult:
    if not available:
        return CompatibilityDimensionResult(
            assessment="unknown",
            explanation="Bu başlık için kullanıcı bilgisi bulunmuyor.",
            evidence_ingredients=[],
            available=False,
        )

    evidence = []
    for name in dimension.evidence_ingredients:
        canonical_input = allowed_names.get(name.casefold())
        if canonical_input and canonical_input not in evidence:
            evidence.append(canonical_input)

    assessment = dimension.assessment
    explanation = dimension.explanation
    if assessment in {"supportive", "caution"} and not evidence:
        assessment = "unknown"
        explanation = "Bu değerlendirme girişteki doğrulanmış içeriklerle desteklenemedi."

    return CompatibilityDimensionResult(
        assessment=assessment,
        explanation=explanation,
        evidence_ingredients=evidence,
        available=True,
    )


def _avoid_matches(request: AnalyzeCompatibilityRequest) -> list[str]:
    preferences = {
        value.strip().casefold()
        for value in request.skin_profile.avoid_ingredients
        if value.strip()
    }
    matches = []
    for ingredient in request.ingredients:
        names = {ingredient.input_name.casefold()}
        if ingredient.canonical_name:
            names.add(ingredient.canonical_name.casefold())
        if names & preferences and ingredient.input_name not in matches:
            matches.append(ingredient.input_name)
    return matches


def _unknown_response(
    *,
    request: AnalyzeCompatibilityRequest,
    avoid_matches: list[str],
    warnings: list[str],
) -> AnalyzeCompatibilityResponse:
    dimensions = {}
    availability = {
        "skin_type": bool(request.skin_profile.skin_type),
        "sensitivity": bool(request.skin_profile.sensitivity),
        "goals": bool(request.skin_profile.goals),
        "appearance": bool(request.skin_profile.appearance),
        "routine": bool(request.routine),
    }
    for name, available in availability.items():
        dimensions[name] = CompatibilityDimensionResult(
            assessment="unknown",
            explanation="Değerlendirme için doğrulanmış içerik bilgisi bulunmuyor.",
            evidence_ingredients=[],
            available=available,
        )
    return AnalyzeCompatibilityResponse(
        product=request.product,
        compatibility=CompatibilityBreakdownResponse(**dimensions),
        strengths=[],
        cautions=[],
        avoid_matches=avoid_matches,
        warnings=warnings,
        disclaimer=DISCLAIMER,
    )


def _raise_status_error(exc: APIStatusError) -> None:
    if exc.status_code in {400, 401, 403, 404}:
        raise CompatibilityAnalysisConfigurationError(
            "OpenAI kimlik bilgileri veya uyumluluk modeli ayarı geçersiz."
        ) from exc
    raise CompatibilityAnalysisUnavailableError(
        f"OpenAI uyumluluk isteğini tamamlayamadı (status={exc.status_code})."
    ) from exc
