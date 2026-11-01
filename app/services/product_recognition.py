import base64
import os
import re

from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from app.models.product_recognition import (
    ProductRecognitionModelOutput,
    ProductRecognitionResult,
)
from app.services.openai_usage import record_openai_usage


load_dotenv()


ALLOWED_CATEGORIES = (
    "Cleanser",
    "Moisturizer",
    "Serum",
    "Toner",
    "Essence",
    "Exfoliant",
    "Face Mask",
    "Sunscreen",
    "Eye Care",
    "Acne/Blemish Care",
    "Body Lotion/Cream",
    "Body Cleanser",
    "Hand Wash",
    "Depilatory",
    "After-shave/Post-hair-removal",
    "Hand Cream",
    "Other Skin Care",
)

DEFAULT_MODEL = "gpt-5.4-mini"

_HAND_WASH_TERMS = ("hand wash", "handwash", "sıvı sabun", "el sabunu")
_BODY_CLEANSER_TERMS = ("body wash", "shower gel", "duş jeli")
_UNSUPPORTED_DEODORANT_TERMS = ("deodorant", "antiperspirant", "anti-perspirant")
_COUNT_ONLY_PRODUCT_NAME = re.compile(
    r"^(?=.*\d)(?:(?:adet|pcs?|pieces?)|\d+|[\s/,:.\-])+$",
    flags=re.IGNORECASE,
)


class ProductRecognitionError(Exception):
    """Base exception for safe API-layer error mapping."""


class ProductRecognitionConfigurationError(ProductRecognitionError):
    pass


class ProductRecognitionUnavailableError(ProductRecognitionError):
    pass


def _get_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ProductRecognitionConfigurationError(
            "OPENAI_API_KEY ortam değişkeni tanımlı değil."
        )
    return OpenAI(
        api_key=api_key,
        timeout=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "45")),
        max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "0")),
    )


def recognize_product(
    image_bytes: bytes,
    content_type: str,
) -> ProductRecognitionResult:

    encoded_image = base64.b64encode(image_bytes).decode("utf-8")

    image_data_url = (
        f"data:{content_type};base64,{encoded_image}"
    )

    model = os.getenv("OPENAI_PRODUCT_RECOGNITION_MODEL", DEFAULT_MODEL)
    try:
        response = _get_client().responses.parse(
            model=model,
            text_format=ProductRecognitionModelOutput,
            instructions=(
                "Inspect one cosmetic or personal-care package image. First assess image "
                "quality, then transcribe identity text. Ignore text on screens, furniture, "
                "hands, and all background objects. Never identify a brand from package color, "
                "shape, memory, or resemblance. Every non-null identity field requires its "
                "evidence field to contain an exact, visibly readable transcription from the "
                "package. If text is faint, partial, obstructed, or uncertain, set both the "
                "identity field and its evidence to null. Never complete partial words. Use "
                "image_quality=poor when the main label cannot be reliably read. Category must "
                "be null unless package text and product form clearly support it. "
                "model_confidence is conservative and is not a calibrated probability."
            ),
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Identify the single cosmetic/skincare product in this image. "
                                "Do not extract ingredients or evaluate the product. Supported "
                                f"categories are: {', '.join(ALLOWED_CATEGORIES)}."
                            ),
                        },
                        {
                            "type": "input_image",
                            "image_url": image_data_url,
                        },
                    ],
                }
            ],
        )
        record_openai_usage(
            operation="product_recognition",
            model=model,
            response=response,
            image_count=1,
        )
    except (APIConnectionError, APITimeoutError) as exc:
        raise ProductRecognitionUnavailableError(
            "OpenAI servisine şu anda ulaşılamıyor."
        ) from exc
    except APIStatusError as exc:
        if exc.status_code in {400, 401, 403, 404}:
            raise ProductRecognitionConfigurationError(
                "OpenAI kimlik bilgileri veya model ayarı geçersiz."
            ) from exc
        raise ProductRecognitionUnavailableError(
            f"OpenAI servisi isteği tamamlayamadı (status={exc.status_code})."
        ) from exc

    result = response.output_parsed
    if result is None:
        raise ProductRecognitionUnavailableError(
            "OpenAI yanıtı doğrulanmış ürün verisi içermiyor."
        )

    return _finalize_result(result)


def _finalize_result(
    output: ProductRecognitionModelOutput,
) -> ProductRecognitionResult:
    brand = output.brand if output.brand_evidence else None
    product_name = output.product_name if output.product_name_evidence else None
    variant = output.variant if output.variant_evidence else None
    category = output.category

    evidence_text = " ".join(
        value
        for value in (
            output.brand_evidence,
            output.product_name_evidence,
            output.variant_evidence,
        )
        if value
    ).casefold()

    # Package quantities and counts are metadata, not product identities. If the
    # model used one as the product name, reject the whole weak identity rather
    # than retaining a possibly background-derived brand guess.
    if product_name and _COUNT_ONLY_PRODUCT_NAME.fullmatch(product_name.strip()):
        brand = None
        product_name = None
        variant = None
        category = None

    # Correct only categories supported by explicit label wording. Deodorant is
    # deliberately unsupported for now and must not be forced into after-shave.
    if any(term in evidence_text for term in _UNSUPPORTED_DEODORANT_TERMS):
        category = None
    elif any(term in evidence_text for term in _HAND_WASH_TERMS):
        category = "Hand Wash"
    elif any(term in evidence_text for term in _BODY_CLEANSER_TERMS):
        category = "Body Cleanser"

    if output.image_quality == "poor":
        brand = None
        product_name = None
        variant = None
        category = None

    if variant and product_name and variant.casefold() in product_name.casefold():
        variant = None

    evidence_score = 0.0
    if brand:
        evidence_score += 0.35
    if product_name:
        evidence_score += 0.45
    if category:
        evidence_score += 0.20

    hard_quality_issues = {
        "blur",
        "too_dark",
        "too_far",
        "multiple_products",
        "not_a_product",
    }
    strong_identity_despite_minor_issues = (
        output.image_quality == "usable"
        and bool(output.quality_issues)
        and not set(output.quality_issues).intersection(hard_quality_issues)
        and brand
        and product_name
        and category
        and output.model_confidence >= 0.95
    )
    quality_cap = {
        "good": 1.0,
        "usable": 0.90 if strong_identity_despite_minor_issues else 0.69,
        "poor": 0.39,
    }[output.image_quality]
    confidence = round(
        min(output.model_confidence, evidence_score, quality_cap),
        2,
    )

    if (
        (
            output.image_quality == "good"
            or strong_identity_despite_minor_issues
        )
        and brand
        and product_name
        and confidence >= 0.70
    ):
        status = "recognized"
    elif output.image_quality == "poor" or not (brand or product_name):
        status = "unrecognized"
    else:
        status = "uncertain"

    if status == "unrecognized":
        brand = None
        product_name = None
        variant = None
        category = None

    retake_reasons = []
    if status != "recognized":
        retake_reasons = list(dict.fromkeys(output.quality_issues))
        if not brand:
            retake_reasons.append("missing_brand_text")
        if not product_name:
            retake_reasons.append("missing_product_name_text")

    return ProductRecognitionResult(
        brand=brand,
        product_name=product_name,
        variant=variant,
        category=category,
        confidence=confidence,
        model_confidence=output.model_confidence,
        status=status,
        image_quality=output.image_quality,
        quality_issues=output.quality_issues,
        brand_evidence=output.brand_evidence if brand else None,
        product_name_evidence=(
            output.product_name_evidence if product_name else None
        ),
        variant_evidence=output.variant_evidence if variant else None,
        needs_retake=status != "recognized",
        retake_reasons=retake_reasons,
    )
