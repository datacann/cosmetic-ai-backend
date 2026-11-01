from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class SkinProfile(BaseModel):
    skin_type: Optional[str] = None
    sensitivity: Optional[str] = None
    appearance: List[str] = []
    goals: List[str] = []
    avoid_ingredients: List[str] = []


class RoutineProduct(BaseModel):
    brand: Optional[str] = None
    name: str
    category: Optional[str] = None
    routine_time: Literal["morning", "evening", "both"]
    ingredients: List[str] = []


class AnalyzeProductRequest(BaseModel):
    skin_profile: SkinProfile
    routine: List[RoutineProduct] = []


class ProductIdentity(BaseModel):
    brand: Optional[str] = None
    product_name: Optional[str] = None
    category: Optional[str] = None
    confidence: Optional[float] = None


class CompatibilityBreakdown(BaseModel):
    skin_type: int = Field(ge=0, le=100)
    sensitivity: int = Field(ge=0, le=100)
    goals: int = Field(ge=0, le=100)
    appearance: int = Field(ge=0, le=100)
    routine: Optional[int] = Field(default=None, ge=0, le=100)


class AnalyzeProductResponse(BaseModel):
    product: ProductIdentity
    ingredients: List[str]

    score: int = Field(ge=0, le=100)
    verdict: str

    compatibility: CompatibilityBreakdown

    strengths: List[str]
    cautions: List[str]

    recommended_usage: Optional[str] = None