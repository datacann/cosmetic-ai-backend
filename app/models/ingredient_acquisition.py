from datetime import datetime
from typing import Literal

from pydantic import BaseModel, field_validator


IngredientStatus = Literal[
    "complete",
    "partial",
    "not_found",
    "unreadable",
]
IngredientSourceType = Literal[
    "package",
    "official_online",
    "trusted_online",
    "database",
    "none",
]
MatchQuality = Literal["exact", "likely", "none"]
ImageQuality = Literal["good", "usable", "poor"]


class PackageFormulaModelOutput(BaseModel):
    name: str | None = None
    raw_text: str | None = None
    ingredients: list[str]
    status: Literal["complete", "partial"]
    uncertain_segments: list[str]

    @field_validator("name", "raw_text", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return value.strip() or None

    @field_validator("ingredients", "uncertain_segments", mode="before")
    @classmethod
    def normalize_lists(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]


class PackageIngredientModelOutput(BaseModel):
    raw_text: str | None = None
    formulas: list[PackageFormulaModelOutput]
    status: Literal["complete", "partial", "unreadable"]
    uncertain_segments: list[str]
    image_quality: ImageQuality

    @field_validator("raw_text", mode="before")
    @classmethod
    def normalize_raw_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return value.strip() or None

    @field_validator("uncertain_segments", mode="before")
    @classmethod
    def normalize_lists(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]


class OnlineIngredientModelOutput(BaseModel):
    matched_brand: str | None = None
    matched_product_name: str | None = None
    matched_variant: str | None = None
    match_quality: MatchQuality
    source_type: IngredientSourceType
    source_url: str | None = None
    source_title: str | None = None
    raw_text: str | None = None
    ingredients: list[str]
    status: Literal["complete", "partial", "not_found"]
    uncertain_segments: list[str]

    @field_validator(
        "matched_brand",
        "matched_product_name",
        "matched_variant",
        "source_url",
        "source_title",
        "raw_text",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return value.strip() or None

    @field_validator("ingredients", "uncertain_segments", mode="before")
    @classmethod
    def normalize_lists(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]


class IngredientSource(BaseModel):
    type: IngredientSourceType
    url: str | None = None
    title: str | None = None
    retrieved_at: datetime | None = None
    match_quality: MatchQuality
    market: str | None = None


class IngredientFormula(BaseModel):
    name: str | None = None
    raw_text: str | None = None
    ingredients: list[str]
    status: Literal["complete", "partial"]
    uncertain_segments: list[str]


class IngredientAcquisitionResponse(BaseModel):
    status: IngredientStatus
    source: IngredientSource
    raw_text: str | None = None
    ingredients: list[str]
    formulas: list[IngredientFormula]
    uncertain_segments: list[str]
    image_quality: ImageQuality | None = None
    needs_package_photo: bool
    warnings: list[str]
