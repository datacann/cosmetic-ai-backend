from typing import Literal

from pydantic import BaseModel, Field

from app.models.compatibility_assessment import FormulaCompatibilityAssessment


Verdict = Literal["Çok Uygun", "Uygun", "Dikkatli Değerlendir", "Düşük Uyum"]


class CalculateSuitabilityScoreRequest(BaseModel):
    compatibility: FormulaCompatibilityAssessment
    ingredient_source_type: Literal[
        "package_image", "manual", "official_online", "trusted_external", "database", "none"
    ] | None = None
    ingredient_source_reliability: Literal["high", "medium", "low", "unknown"] | None = None


class SuitabilityDimensionScores(BaseModel):
    skin_type: int | None = Field(default=None, ge=0, le=100)
    sensitivity: int | None = Field(default=None, ge=0, le=100)
    goals: float | None = Field(default=None, ge=0, le=100)
    current_appearance: int | None = Field(default=None, ge=0, le=100)
    avoid_preferences: int | None = Field(default=None, ge=0, le=100)
    routine: int | None = Field(default=None, ge=0, le=100)


class SuitabilityAppliedWeights(BaseModel):
    skin_type: float = Field(ge=0.0, le=1.0)
    sensitivity: float = Field(ge=0.0, le=1.0)
    goals: float = Field(ge=0.0, le=1.0)
    current_appearance: float = Field(ge=0.0, le=1.0)
    avoid_preferences: float = Field(ge=0.0, le=1.0)
    routine: float = Field(ge=0.0, le=1.0)


class ScorePenalty(BaseModel):
    code: str
    points: int = Field(le=0)


class ScoreCap(BaseModel):
    code: str
    max_score: int = Field(ge=0, le=100)


class SuitabilityScoreResponse(BaseModel):
    score: int = Field(ge=0, le=100)
    verdict: Verdict
    dimension_scores: SuitabilityDimensionScores
    applied_weights: SuitabilityAppliedWeights
    base_score: float = Field(ge=0.0, le=100.0)
    severity_adjustments: list[ScorePenalty]
    penalties: list[ScorePenalty]
    caps: list[ScoreCap]
    analysis_confidence: float = Field(ge=0.0, le=1.0)
    score_meaning: str
