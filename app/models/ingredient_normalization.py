from typing import Literal

from pydantic import BaseModel, Field, field_validator


IngredientProvenance = Literal[
    "package_image",
    "manual",
    "official_online",
    "trusted_external",
    "database",
]
IngredientMatchStatus = Literal["alias", "database", "cosing", "unmatched"]


class RawIngredientFormula(BaseModel):
    name: str | None = None
    raw_text: str | None = None
    ingredients: list[str]

    @field_validator("ingredients")
    @classmethod
    def reject_empty_ingredients(cls, value: list[str]) -> list[str]:
        if not value or any(not item.strip() for item in value):
            raise ValueError("Ingredient names must be non-empty strings.")
        return value


class NormalizeIngredientsRequest(BaseModel):
    provenance: IngredientProvenance
    formulas: list[RawIngredientFormula]

    @field_validator("formulas")
    @classmethod
    def reject_empty_formulas(cls, value: list[RawIngredientFormula]) -> list[RawIngredientFormula]:
        if not value:
            raise ValueError("At least one formula is required.")
        return value


class NormalizedIngredient(BaseModel):
    position: int = Field(ge=1)
    raw_name: str
    canonical_name: str | None = None
    concentration: float | None = Field(default=None, ge=0)
    concentration_unit: Literal["%"] | None = None
    match_status: IngredientMatchStatus


class NormalizedIngredientFormula(BaseModel):
    name: str | None = None
    raw_text: str | None = None
    ingredients: list[NormalizedIngredient]


class NormalizeIngredientsResponse(BaseModel):
    provenance: IngredientProvenance
    formulas: list[NormalizedIngredientFormula]
    unmatched_count: int = Field(ge=0)
