from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.models.ingredient_analysis import IngredientFunction
from app.models.ingredient_normalization import (
    IngredientProvenance,
    NormalizedIngredient,
)


CosmeticProperty = Literal[
    "supports_hydration",
    "supports_a_softer_smoother_appearance",
    "helps_reduce_moisture_loss",
    "supports_cleansing",
    "supports_formula_stability",
    "supports_product_preservation",
    "helps_protect_the_formula_from_oxidation",
    "helps_dissolve_formula_components",
    "provides_fragrance",
    "provides_color",
    "provides_a_uv_filtering_function",
    "supports_ph_adjustment",
    "supports_a_smoother_looking_surface",
    "may_support_skin_comfort",
    "supports_a_conditioned_skin_feel",
    "supports_cosmetic_hair_removal",
]
CautionTag = Literal[
    "potential_irritant",
    "exfoliating_active",
    "fragrance_related",
    "essential_oil_related",
    "drying_potential",
    "sensitization_potential",
]
KnowledgeEvidenceStatus = Literal["verified", "unverified", "unresolved"]
KnowledgeSource = Literal[
    "postgresql",
    "cosing",
    "trusted_enrichment",
    "research",
    "unresolved",
]


class IngredientKnowledgeRecord(BaseModel):
    canonical_name: str | None
    functions: list[IngredientFunction]
    cosmetic_properties: list[CosmeticProperty]
    caution_tags: list[CautionTag]
    evidence_status: KnowledgeEvidenceStatus
    source: KnowledgeSource
    source_references: list[str]
    last_verified_at: datetime | None = None


class KnowledgeEnrichedIngredient(BaseModel):
    normalized: NormalizedIngredient
    knowledge: IngredientKnowledgeRecord


class KnowledgeEnrichedFormula(BaseModel):
    name: str | None = None
    raw_text: str | None = None
    ingredients: list[KnowledgeEnrichedIngredient]


class IngredientKnowledgeResponse(BaseModel):
    provenance: IngredientProvenance
    formulas: list[KnowledgeEnrichedFormula]
    unresolved_count: int
    paid_fallback_count: int
