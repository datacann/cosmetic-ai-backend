from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.models.ingredient_analysis import (
    EvidenceStatus,
    IngredientFlag,
    IngredientFunction,
)


Assessment = Literal["supportive", "neutral", "caution", "unknown"]
Dimension = Literal["skin_type", "sensitivity", "goals", "appearance", "routine"]


class CompatibilityProduct(BaseModel):
    brand: str | None = None
    product_name: str
    category: str | None = None
    formula_name: str | None = None


class CompatibilityIngredient(BaseModel):
    input_name: str
    canonical_name: str | None = None
    functions: list[IngredientFunction]
    flags: list[IngredientFlag]
    evidence_status: EvidenceStatus


class CompatibilitySkinProfile(BaseModel):
    skin_type: str | None = None
    sensitivity: str | None = None
    appearance: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    avoid_ingredients: list[str] = Field(default_factory=list)


class CompatibilityRoutineProduct(BaseModel):
    name: str
    category: str | None = None
    routine_time: Literal["morning", "evening", "both"]
    ingredients: list[str] = Field(default_factory=list)


class AnalyzeCompatibilityRequest(BaseModel):
    product: CompatibilityProduct
    ingredients: list[CompatibilityIngredient] = Field(min_length=1, max_length=100)
    skin_profile: CompatibilitySkinProfile
    routine: list[CompatibilityRoutineProduct] = Field(default_factory=list, max_length=30)

    @field_validator("ingredients", mode="before")
    @classmethod
    def remove_blank_ingredients(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return [
            item
            for item in value
            if (
                isinstance(item, CompatibilityIngredient)
                and item.input_name.strip()
            )
            or (
                isinstance(item, dict)
                and str(item.get("input_name", "")).strip()
            )
        ]


class CompatibilityDimensionModelOutput(BaseModel):
    assessment: Assessment
    explanation: str
    evidence_ingredients: list[str]


class CompatibilityAnalysisModelOutput(BaseModel):
    skin_type: CompatibilityDimensionModelOutput
    sensitivity: CompatibilityDimensionModelOutput
    goals: CompatibilityDimensionModelOutput
    appearance: CompatibilityDimensionModelOutput
    routine: CompatibilityDimensionModelOutput


class CompatibilityDimensionResult(CompatibilityDimensionModelOutput):
    available: bool


class CompatibilityBreakdownResponse(BaseModel):
    skin_type: CompatibilityDimensionResult
    sensitivity: CompatibilityDimensionResult
    goals: CompatibilityDimensionResult
    appearance: CompatibilityDimensionResult
    routine: CompatibilityDimensionResult


class AnalyzeCompatibilityResponse(BaseModel):
    product: CompatibilityProduct
    compatibility: CompatibilityBreakdownResponse
    strengths: list[str]
    cautions: list[str]
    avoid_matches: list[str]
    usage_note: str | None = None
    warnings: list[str]
    disclaimer: str
