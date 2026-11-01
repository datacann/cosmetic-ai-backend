from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.models.compatibility_analysis import (
    AnalyzeCompatibilityResponse,
    CompatibilityRoutineProduct,
    CompatibilitySkinProfile,
)
from app.models.compatibility_scoring import ScoreCompatibilityResponse
from app.models.ingredient_acquisition import IngredientAcquisitionResponse
from app.models.ingredient_analysis import AnalyzeIngredientsResponse
from app.models.product_recognition import ProductRecognitionResult


class FormulaAnalysisResult(BaseModel):
    formula_name: str | None = None
    status: Literal["complete", "partial", "blocked"]
    ingredients: list[str]
    ingredient_analysis: AnalyzeIngredientsResponse | None = None
    compatibility: AnalyzeCompatibilityResponse | None = None
    scoring: ScoreCompatibilityResponse | None = None
    blockers: list[str]


class FullProductAnalysisResponse(BaseModel):
    analysis_id: str | None = None
    status: Literal["complete", "partial", "needs_input"]
    product: ProductRecognitionResult
    ingredient_acquisition: IngredientAcquisitionResponse | None = None
    formulas: list[FormulaAnalysisResult]
    warnings: list[str]


class BarcodeLookupResponse(BaseModel):
    found: bool
    barcode: str
    product: ProductRecognitionResult | None = None


class CatalogProductItem(BaseModel):
    id: str
    brand: str
    product_name: str
    variant: str | None = None
    category: str | None = None
    barcode: str | None = None
    formula_count: int
    ingredients_available: bool


class CatalogProductPage(BaseModel):
    items: list[CatalogProductItem]
    total: int
    limit: int
    offset: int
    categories: list[str]


class CatalogIngredientItem(BaseModel):
    name: str
    canonical_name: str
    functions: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    verification_status: str


class CatalogFormulaDetail(BaseModel):
    id: str
    name: str
    version: int
    status: str
    source_type: str
    source_url: str | None = None
    source_accessed_at: str | None = None
    created_at: str
    ingredients: list[CatalogIngredientItem]


class CatalogProductDetail(BaseModel):
    id: str
    brand: str
    product_name: str
    variant: str | None = None
    category: str | None = None
    barcode: str | None = None
    market: str
    updated_at: str
    formulas: list[CatalogFormulaDetail]


class CatalogAnalysisRequest(BaseModel):
    skin_profile: CompatibilitySkinProfile | None = None
    routine: list[CompatibilityRoutineProduct] = Field(default_factory=list)
    market: str = "TR"
    progress_id: str | None = None


class BarcodeAnalysisRequest(BaseModel):
    barcode: str = Field(min_length=8, max_length=20)
    skin_profile: CompatibilitySkinProfile | None = None
    routine: list[CompatibilityRoutineProduct] = Field(default_factory=list)
    market: str = "TR"
    progress_id: str | None = None

    @field_validator("barcode", mode="before")
    @classmethod
    def normalize_barcode(cls, value: object) -> object:
        if isinstance(value, str):
            return value.replace(" ", "").replace("-", "")
        return value

    @field_validator("market", mode="before")
    @classmethod
    def normalize_market(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value


class UsageStatusResponse(BaseModel):
    plan: str
    period: Literal["monthly"]
    analysis_limit: int
    used: int
    remaining: int
    resets_at: str
    history_limit: int | None
    ads_enabled: bool
    rewarded_daily_limit: int
    rewarded_earned: int
    rewarded_earned_today: int
    rewarded_available: bool
    premium_entitled: bool


class RewardedAnalysisClaim(BaseModel):
    claim_id: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")
