from typing import Literal

from pydantic import BaseModel, Field

from app.models.compatibility_analysis import (
    Assessment,
    CompatibilityBreakdownResponse,
    CompatibilityProduct,
)


Verdict = Literal[
    "Çok Uygun",
    "Uygun",
    "Dikkatli Değerlendir",
    "Düşük Uyum",
]


class ScoreCompatibilityRequest(BaseModel):
    product: CompatibilityProduct | None = None
    compatibility: CompatibilityBreakdownResponse
    avoid_matches: list[str] = Field(default_factory=list)


class DimensionScore(BaseModel):
    assessment: Assessment
    value: int | None = Field(default=None, ge=0, le=100)
    configured_weight: int = Field(ge=0, le=100)
    normalized_weight: float = Field(ge=0, le=1)
    contribution: float = Field(ge=0, le=100)
    included: bool


class ScoreBreakdown(BaseModel):
    skin_type: DimensionScore
    sensitivity: DimensionScore
    goals: DimensionScore
    appearance: DimensionScore
    routine: DimensionScore


class ScoreCompatibilityResponse(BaseModel):
    product: CompatibilityProduct | None = None
    score: int = Field(ge=0, le=100)
    raw_score: int = Field(ge=0, le=100)
    verdict: Verdict
    coverage: int = Field(ge=0, le=100)
    breakdown: ScoreBreakdown
    applied_rules: list[str]
    score_meaning: str
