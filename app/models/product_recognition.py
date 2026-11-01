from typing import Literal

from pydantic import BaseModel, Field, field_validator


ProductCategory = Literal[
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
]

ImageQuality = Literal["good", "usable", "poor"]
RecognitionStatus = Literal["recognized", "uncertain", "unrecognized"]
QualityIssue = Literal[
    "blur",
    "too_dark",
    "glare",
    "too_far",
    "occluded",
    "label_not_front_facing",
    "multiple_products",
    "not_a_product",
]
RetakeReason = Literal[
    "blur",
    "too_dark",
    "glare",
    "too_far",
    "occluded",
    "label_not_front_facing",
    "multiple_products",
    "not_a_product",
    "missing_brand_text",
    "missing_product_name_text",
]


class ProductRecognitionModelOutput(BaseModel):
    """Strict model output before backend confidence and gating decisions."""

    brand: str | None = None
    brand_evidence: str | None = None
    product_name: str | None = None
    product_name_evidence: str | None = None
    variant: str | None = None
    variant_evidence: str | None = None
    category: ProductCategory | None = None
    model_confidence: float = Field(ge=0.0, le=1.0)
    image_quality: ImageQuality
    quality_issues: list[QualityIssue]

    @field_validator(
        "brand",
        "brand_evidence",
        "product_name",
        "product_name_evidence",
        "variant",
        "variant_evidence",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None

    @field_validator("model_confidence", mode="before")
    @classmethod
    def normalize_model_confidence(cls, value: object) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(confidence, 1.0))


class ProductRecognitionResult(BaseModel):
    brand: str | None = None
    product_name: str | None = None
    variant: str | None = None
    category: ProductCategory | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    model_confidence: float = Field(ge=0.0, le=1.0)
    status: RecognitionStatus
    image_quality: ImageQuality
    quality_issues: list[QualityIssue]
    brand_evidence: str | None = None
    product_name_evidence: str | None = None
    variant_evidence: str | None = None
    needs_retake: bool
    retake_reasons: list[RetakeReason]


class ProductRecognitionResponse(BaseModel):
    filename: str | None
    content_type: str
    size: int = Field(ge=1)
    product: ProductRecognitionResult
