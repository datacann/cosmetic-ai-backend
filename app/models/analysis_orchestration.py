from typing import Literal

from pydantic import BaseModel, Field

from app.models.compatibility_assessment import AnalyzeContextCompatibilityResponse
from app.models.ingredient_knowledge import IngredientKnowledgeResponse
from app.models.product_recognition import ProductRecognitionResult
from app.models.suitability_scoring import SuitabilityScoreResponse
from app.models.user_context import UserContextResponse


PipelineOutcome = Literal[
    "success",
    "needs_product_correction",
    "needs_ingredient_photo",
    "unsupported_product",
    "insufficient_ingredient_data",
    "temporary_service_error",
]


class AnalysisIngredientSource(BaseModel):
    type: Literal[
        "package_image", "manual", "official_online", "trusted_external", "database", "none"
    ]
    status: str
    reliability: Literal["high", "medium", "low", "unknown"]


class FormulaSuitability(BaseModel):
    formula_name: str | None = None
    result: SuitabilityScoreResponse


class AnalysisSummary(BaseModel):
    strengths: list[str] = Field(default_factory=list)
    cautions: list[str] = Field(default_factory=list)
    recommended_usage_time: Literal["morning", "evening", "both", "unknown"] = "unknown"


class AnalysisDiagnostics(BaseModel):
    indicators: list[str] = Field(default_factory=list)
    correction_needed: bool = False
    retake_needed: bool = False
    unsupported_product: bool = False
    unresolved_ingredient_count: int = Field(default=0, ge=0)


class AnalysisCostBreakdown(BaseModel):
    product_recognition_usd: float = 0
    ingredient_acquisition_usd: float = 0
    ingredient_research_usd: float = 0
    compatibility_usd: float = 0
    total_usd: float = 0
    input_tokens: int = 0
    output_tokens: int = 0
    web_search_calls: int = 0


class EndToEndAnalysisResponse(BaseModel):
    outcome: PipelineOutcome
    analysis_id: str | None = None
    product: ProductRecognitionResult | None = None
    ingredient_source: AnalysisIngredientSource | None = None
    ingredients: IngredientKnowledgeResponse | None = None
    user_context: UserContextResponse | None = None
    compatibility: AnalyzeContextCompatibilityResponse | None = None
    personal_suitability: list[FormulaSuitability] = Field(default_factory=list)
    summary: AnalysisSummary | None = None
    diagnostics: AnalysisDiagnostics
    cost: AnalysisCostBreakdown
