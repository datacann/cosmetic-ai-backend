from typing import Literal

from pydantic import BaseModel, Field, model_validator


SkinType = Literal["oily", "dry", "combination", "normal", "unknown"]
Sensitivity = Literal[
    "not_sensitive", "somewhat_sensitive", "very_sensitive", "unknown"
]
Appearance = Literal[
    "dryness", "flaking", "oily_or_shiny", "redness",
    "blemish_appearance", "uneven_tone", "visible_pores",
    "rough_or_uneven_texture", "tightness", "none", "unknown",
]
Goal = Literal[
    "blemish_appearance", "uneven_tone", "redness", "visible_pores",
    "hydration", "oil_control", "texture", "fine_lines_appearance",
    "general_care",
]
RoutineTime = Literal["morning", "evening", "both"]
ContextField = Literal[
    "skin.skin_type", "skin.sensitivity", "skin.appearance", "skin.goals",
    "preferences.avoid_ingredients", "routine",
]


class UserContextProfileInput(BaseModel):
    skin_type: str | None = None
    sensitivity: str | None = None
    appearance: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    avoid_ingredients: list[str] = Field(default_factory=list)


class RoutineIngredientInput(BaseModel):
    raw_name: str
    canonical_name: str | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_plain_ingredient_name(cls, value: object) -> object:
        if isinstance(value, str):
            return {"raw_name": value}
        return value


class UserContextRoutineProductInput(BaseModel):
    brand: str | None = None
    name: str
    category: str | None = None
    routine_time: RoutineTime
    ingredients: list[RoutineIngredientInput] = Field(default_factory=list)


class BuildUserContextRequest(BaseModel):
    profile: UserContextProfileInput = Field(default_factory=UserContextProfileInput)
    routine: list[UserContextRoutineProductInput] = Field(default_factory=list)


class UserSkinContext(BaseModel):
    skin_type: SkinType
    sensitivity: Sensitivity
    appearance: list[Appearance]
    goals: list[Goal]


class ContextIngredientIdentity(BaseModel):
    raw_name: str
    canonical_name: str | None


class UserPreferenceContext(BaseModel):
    avoid_ingredients: list[ContextIngredientIdentity]


class ContextRoutineProduct(BaseModel):
    brand: str | None = None
    name: str
    category: str | None = None
    ingredients: list[ContextIngredientIdentity]


class UserRoutineContext(BaseModel):
    morning: list[ContextRoutineProduct]
    evening: list[ContextRoutineProduct]


class UserContextResponse(BaseModel):
    skin: UserSkinContext
    preferences: UserPreferenceContext
    routine: UserRoutineContext
    known_fields: list[ContextField]
    unknown_fields: list[ContextField]
