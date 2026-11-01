from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.compatibility_analysis import CompatibilityProduct
from app.models.ingredient_knowledge import IngredientKnowledgeResponse
from app.models.user_context import UserContextResponse
from app.models.user_context import Goal


CompatibilityStatus = Literal[
    "strong_match", "match", "neutral", "caution", "conflict", "unknown"
]
RecommendedTime = Literal["morning", "evening", "both", "unknown"]
ConflictSeverity = Literal["none", "mild", "moderate", "strong"]

_MEDICAL_CLAIMS = (
    "treats acne", "cures acne", "cures redness", "prevents disease",
    "medically effective", "safe for everyone", "guaranteed safe",
    "akneyi tedavi", "akneyi iyileştir", "kızarıklığı tedavi",
    "hastalığı önler", "herkes için güvenli", "tıbben etkilidir",
)


class CompatibilitySignalModelOutput(BaseModel):
    status: CompatibilityStatus
    conflict_severity: ConflictSeverity = "none"
    reasons: list[str] = Field(max_length=3)
    evidence_ingredients: list[str] = Field(max_length=20)
    matched_context: list[str] = Field(default_factory=list, max_length=20)
    unassessed_context: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("reasons")
    @classmethod
    def reject_medical_claims(cls, values: list[str]) -> list[str]:
        for value in values:
            folded = value.casefold()
            if any(claim in folded for claim in _MEDICAL_CLAIMS):
                raise ValueError("Medical treatment/safety claims are not allowed.")
        return values

    @model_validator(mode="after")
    def unknown_has_no_conflict_severity(self):
        if self.status == "unknown" and self.conflict_severity != "none":
            raise ValueError("Unknown compatibility cannot carry conflict severity.")
        return self


class RoutineCompatibilityModelOutput(CompatibilitySignalModelOutput):
    potential_conflicts: list[str] = Field(default_factory=list, max_length=10)
    recommended_time: RecommendedTime


class GoalCompatibilityModelOutput(CompatibilitySignalModelOutput):
    goal: Goal


class GoalsCompatibilityModelOutput(BaseModel):
    assessments: list[GoalCompatibilityModelOutput] = Field(max_length=20)


class FormulaCompatibilityModelOutput(BaseModel):
    formula_name: str | None = None
    skin_type: CompatibilitySignalModelOutput
    sensitivity: CompatibilitySignalModelOutput
    goals: GoalsCompatibilityModelOutput
    current_appearance: CompatibilitySignalModelOutput
    routine: RoutineCompatibilityModelOutput


class CompatibilityAssessmentModelOutput(BaseModel):
    formulas: list[FormulaCompatibilityModelOutput]
    overall_cautions: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("overall_cautions")
    @classmethod
    def reject_medical_claims(cls, values: list[str]) -> list[str]:
        return CompatibilitySignalModelOutput.reject_medical_claims(values)


class AvoidPreferenceCompatibility(BaseModel):
    status: Literal["neutral", "conflict", "unknown"]
    conflict_severity: ConflictSeverity = "none"
    matched_avoid_ingredients: list[str]

    @model_validator(mode="after")
    def validate_severity(self):
        if self.status == "unknown" and self.conflict_severity != "none":
            raise ValueError("Unknown avoid preference cannot carry conflict severity.")
        return self


class CompatibilitySignalResult(CompatibilitySignalModelOutput):
    available: bool


class RoutineCompatibilityResult(RoutineCompatibilityModelOutput):
    available: bool


class GoalCompatibilityResult(GoalCompatibilityModelOutput):
    available: bool


class GoalsCompatibilityResult(BaseModel):
    assessments: list[GoalCompatibilityResult] = Field(max_length=20)


class FormulaCompatibilityAssessment(BaseModel):
    formula_name: str | None = None
    skin_type: CompatibilitySignalResult
    sensitivity: CompatibilitySignalResult
    goals: GoalsCompatibilityResult
    current_appearance: CompatibilitySignalResult
    avoid_preferences: AvoidPreferenceCompatibility
    routine: RoutineCompatibilityResult
    goal_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    assessment_confidence: float = Field(ge=0.0, le=1.0)


class AnalyzeContextCompatibilityRequest(BaseModel):
    product: CompatibilityProduct
    ingredient_knowledge: IngredientKnowledgeResponse
    user_context: UserContextResponse


class AnalyzeContextCompatibilityResponse(BaseModel):
    product: CompatibilityProduct
    formulas: list[FormulaCompatibilityAssessment]
    overall_cautions: list[str]
    warnings: list[str]
    disclaimer: str
