from datetime import datetime

from pydantic import BaseModel, Field, field_validator


def normalize_email(value: str) -> str:
    return value.strip().casefold()


class RegisterRequest(BaseModel):
    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=10, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = normalize_email(value)
        local, separator, domain = normalized.partition("@")
        if not separator or not local or "." not in domain or domain.startswith("."):
            raise ValueError("Geçerli bir e-posta adresi girin.")
        return normalized


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


class DeleteAccountRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    confirmation: str = Field(pattern="^SİL$")


class UserResponse(BaseModel):
    id: str
    email: str
    email_verified: bool
    plan: str = "free"
    is_admin: bool = False
    is_active: bool
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_expires_in: int
    user: UserResponse


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=500)


class SocialLoginRequest(BaseModel):
    identity_token: str = Field(min_length=20, max_length=10000)


class ForgotPasswordRequest(BaseModel):
    email: str = Field(min_length=5, max_length=320)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return RegisterRequest.validate_email(value)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=32, max_length=500)
    new_password: str = Field(min_length=10, max_length=128)


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=32, max_length=500)


class SkinProfileResponse(BaseModel):
    skin_type: str | None = None
    sensitivity: str | None = None
    appearance: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    avoid_ingredients: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


class AnalysisHistoryItem(BaseModel):
    id: str
    status: str
    product_brand: str
    product_name: str
    product_variant: str | None = None
    created_at: datetime
    is_favorite: bool = False


class AnalysisHistoryPage(BaseModel):
    items: list[AnalysisHistoryItem]
    page: int
    page_size: int
    total: int
    has_more: bool
    history_limit: int | None = None


class AnalysisDetailResponse(BaseModel):
    id: str
    status: str
    created_at: datetime
    is_favorite: bool
    product: dict
    formulas: list[dict]


class FavoriteRequest(BaseModel):
    is_favorite: bool


class ProductFeedbackRequest(BaseModel):
    reason: str = Field(pattern="^(wrong_product|wrong_variant|wrong_ingredients|wrong_barcode|outdated_formula|other)$")
    details: str | None = Field(default=None, max_length=500)

    @field_validator("details", mode="before")
    @classmethod
    def normalize_details(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return value.strip() or None


class ProductFeedbackResponse(BaseModel):
    id: str
    reason: str
    details: str | None = None
    status: str
    created_at: datetime


class AdminMetricSummary(BaseModel):
    total_analyses: int
    successful_analyses: int
    failed_analyses: int
    success_rate: float
    average_duration_ms: int
    formula_cache_hits: int
    ingredient_cache_hits: int
    pending_feedback: int


class AdminFeedbackItem(BaseModel):
    id: str
    analysis_id: str
    reason: str
    details: str | None = None
    status: str
    product_brand: str
    product_name: str
    user_email: str
    created_at: datetime


class AdminFeedbackStatusRequest(BaseModel):
    status: str = Field(pattern="^(pending|reviewed)$")


class RoutineProductRequest(BaseModel):
    analysis_id: str
    routine_time: str = Field(pattern="^(morning|evening|both)$")


class ManualRoutineProductRequest(BaseModel):
    product_name: str = Field(min_length=1, max_length=200)
    product_brand: str | None = Field(default=None, max_length=120)
    product_variant: str | None = Field(default=None, max_length=160)
    product_category: str | None = Field(default=None, max_length=80)
    routine_time: str = Field(pattern="^(morning|evening|both)$")


class RoutineTimeRequest(BaseModel):
    routine_time: str = Field(pattern="^(morning|evening|both)$")


class RoutineProductResponse(BaseModel):
    id: str
    product_brand: str
    product_name: str
    product_variant: str | None = None
    product_category: str | None = None
    routine_time: str
    ingredients: list[str] = Field(default_factory=list)
