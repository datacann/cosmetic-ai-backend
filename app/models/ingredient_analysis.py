from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


IngredientFunction = Literal[
    "humectant",
    "emollient",
    "occlusive",
    "surfactant",
    "emulsifier",
    "preservative",
    "antioxidant",
    "solvent",
    "fragrance",
    "colorant",
    "uv_filter",
    "ph_adjuster",
    "chelating_agent",
    "exfoliant",
    "soothing",
    "skin_conditioning",
    "hair_removal_active",
    "other",
    "unknown",
]
IngredientFlag = Literal[
    "fragrance",
    "fragrance_allergen",
    "colorant",
    "uv_filter",
    "preservative",
    "exfoliating_acid",
    "hair_removal_active",
]
EvidenceStatus = Literal["verified", "unverified"]


class AnalyzeIngredientsRequest(BaseModel):
    formula_name: str | None = None
    ingredients: list[str] = Field(min_length=1, max_length=100)
    market: str = "TR"

    @field_validator("formula_name", mode="before")
    @classmethod
    def normalize_formula_name(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return value.strip() or None

    @field_validator("ingredients", mode="before")
    @classmethod
    def normalize_ingredients(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]

    @field_validator("market", mode="before")
    @classmethod
    def normalize_market(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return value.strip().upper()


class IngredientKnowledgeModelOutput(BaseModel):
    input_name: str
    canonical_name: str | None = None
    functions: list[IngredientFunction]
    flags: list[IngredientFlag]
    note: str
    source_urls: list[str]


class IngredientAnalysisModelOutput(BaseModel):
    items: list[IngredientKnowledgeModelOutput]


class IngredientAnalysisItem(BaseModel):
    input_name: str
    canonical_name: str | None = None
    functions: list[IngredientFunction]
    flags: list[IngredientFlag]
    note: str
    evidence_status: EvidenceStatus
    source_urls: list[str]
    knowledge_source: Literal["postgresql", "cosing", "trusted_enrichment", "research"] | None = None
    last_verified_at: datetime | None = None


class AnalyzeIngredientsResponse(BaseModel):
    formula_name: str | None = None
    status: Literal["complete", "partial"]
    items: list[IngredientAnalysisItem]
    unknown_ingredients: list[str]
    warnings: list[str]
    disclaimer: str
