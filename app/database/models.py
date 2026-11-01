from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


JsonType = JSON().with_variant(JSONB(), "postgresql")


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("normalized_key", name="uq_products_normalized_key"),
        Index("ix_products_brand_name", "brand", "product_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    brand: Mapped[str] = mapped_column(String(160))
    product_name: Mapped[str] = mapped_column(String(300))
    variant: Mapped[str | None] = mapped_column(String(300))
    category: Mapped[str | None] = mapped_column(String(100))
    market: Mapped[str] = mapped_column(String(10), default="TR")
    barcode: Mapped[str | None] = mapped_column(String(32), unique=True)
    normalized_key: Mapped[str] = mapped_column(String(900))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    formulas: Mapped[list[ProductFormula]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductFormula(Base):
    __tablename__ = "product_formulas"
    __table_args__ = (
        UniqueConstraint("product_id", "formula_name", "formula_version", name="uq_product_formula_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    formula_name: Mapped[str] = mapped_column(String(300), default="Ana formül")
    formula_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30))
    source_type: Mapped[str] = mapped_column(String(40))
    source_url: Mapped[str | None] = mapped_column(Text)
    source_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    product: Mapped[Product] = relationship(back_populates="formulas")
    ingredient_links: Mapped[list[FormulaIngredient]] = relationship(
        back_populates="formula",
        cascade="all, delete-orphan",
        order_by="FormulaIngredient.position",
    )


class Ingredient(Base):
    __tablename__ = "ingredients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    inci_name: Mapped[str] = mapped_column(String(300))
    normalized_name: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    display_name_tr: Mapped[str | None] = mapped_column(String(300))
    functions: Mapped[list[str]] = mapped_column(JsonType, default=list)
    flags: Mapped[list[str]] = mapped_column(JsonType, default=list)
    evidence_status: Mapped[str] = mapped_column(String(30), default="unverified")
    knowledge_source: Mapped[str | None] = mapped_column(String(80))
    source_urls: Mapped[list[str]] = mapped_column(JsonType, default=list)
    knowledge_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class FormulaIngredient(Base):
    __tablename__ = "formula_ingredients"
    __table_args__ = (
        UniqueConstraint("formula_id", "position", name="uq_formula_ingredient_position"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    formula_id: Mapped[str] = mapped_column(ForeignKey("product_formulas.id", ondelete="CASCADE"), index=True)
    ingredient_id: Mapped[str] = mapped_column(ForeignKey("ingredients.id", ondelete="RESTRICT"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    raw_name: Mapped[str] = mapped_column(String(300))
    verification_status: Mapped[str] = mapped_column(String(30), default="unverified")
    confidence: Mapped[float | None] = mapped_column(Float)
    evidence_source_urls: Mapped[list[str]] = mapped_column(JsonType, default=list)

    formula: Mapped[ProductFormula] = relationship(back_populates="ingredient_links")
    ingredient: Mapped[Ingredient] = relationship()


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320))
    normalized_email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(500))
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    plan: Mapped[str] = mapped_column(String(20), default="free")
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    skin_profile: Mapped[SkinProfile | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    social_identities: Mapped[list[SocialIdentity]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class SocialIdentity(Base):
    __tablename__ = "social_identities"
    __table_args__ = (
        UniqueConstraint("provider", "provider_subject", name="uq_social_identity_provider_subject"),
        UniqueConstraint("user_id", "provider", name="uq_social_identity_user_provider"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20), index=True)
    provider_subject: Mapped[str] = mapped_column(String(255))
    provider_email: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_login_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    user: Mapped[User] = relationship(back_populates="social_identities")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    user: Mapped[User] = relationship(back_populates="refresh_tokens")


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EmailVerificationToken(Base):
    __tablename__ = "email_verification_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SkinProfile(Base):
    __tablename__ = "skin_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    skin_type: Mapped[str | None] = mapped_column(String(100))
    sensitivity: Mapped[str | None] = mapped_column(String(100))
    appearance: Mapped[list[str]] = mapped_column(JsonType, default=list)
    goals: Mapped[list[str]] = mapped_column(JsonType, default=list)
    avoid_ingredients: Mapped[list[str]] = mapped_column(JsonType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    user: Mapped[User] = relationship(back_populates="skin_profile")


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), index=True)
    skin_profile_snapshot: Mapped[dict[str, Any]] = mapped_column(JsonType)
    routine_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JsonType)
    status: Mapped[str] = mapped_column(String(30))
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)

    results: Mapped[list[AnalysisFormulaResult]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan"
    )


class AnalysisFormulaResult(Base):
    __tablename__ = "analysis_formula_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    formula_id: Mapped[str] = mapped_column(ForeignKey("product_formulas.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(30))
    score: Mapped[int | None] = mapped_column(Integer)
    raw_score: Mapped[float | None] = mapped_column(Float)
    verdict: Mapped[str | None] = mapped_column(String(80))
    coverage: Mapped[float | None] = mapped_column(Float)
    dimensions: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    strengths: Mapped[list[str]] = mapped_column(JsonType, default=list)
    cautions: Mapped[list[str]] = mapped_column(JsonType, default=list)
    avoid_matches: Mapped[list[str]] = mapped_column(JsonType, default=list)
    warnings: Mapped[list[str]] = mapped_column(JsonType, default=list)
    scoring_version: Mapped[str] = mapped_column(String(30), default="1.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    analysis: Mapped[Analysis] = relationship(back_populates="results")
    formula: Mapped[ProductFormula] = relationship()


class ProductFeedback(Base):
    __tablename__ = "product_feedback"
    __table_args__ = (
        UniqueConstraint("user_id", "analysis_id", "reason", name="uq_product_feedback_reason"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    reason: Mapped[str] = mapped_column(String(40))
    details: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    guest_key: Mapped[str | None] = mapped_column(String(64), index=True)
    request_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(40), default="vision_analysis")
    charged: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default="reserved", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnalysisMetric(Base):
    __tablename__ = "analysis_metrics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    analysis_id: Mapped[str | None] = mapped_column(ForeignKey("analyses.id", ondelete="SET NULL"), index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    request_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    flow_type: Mapped[str] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    total_ms: Mapped[int] = mapped_column(Integer)
    stage_durations_ms: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    formula_cache_used: Mapped[bool] = mapped_column(Boolean, default=False)
    ingredient_cache_used: Mapped[bool] = mapped_column(Boolean, default=False)
    error_stage: Mapped[str | None] = mapped_column(String(50))
    error_type: Mapped[str | None] = mapped_column(String(120))
    model_usage: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class RoutineProduct(Base):
    __tablename__ = "routine_products"
    __table_args__ = (
        UniqueConstraint("user_id", "product_id", "routine_time", name="uq_routine_product_time"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[str | None] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    manual_brand: Mapped[str | None] = mapped_column(String(120))
    manual_name: Mapped[str | None] = mapped_column(String(200))
    manual_variant: Mapped[str | None] = mapped_column(String(160))
    manual_category: Mapped[str | None] = mapped_column(String(80))
    routine_time: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    product: Mapped[Product | None] = relationship()
