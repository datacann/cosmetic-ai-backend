from datetime import datetime, timezone
import logging
import os
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from typing import Annotated

from fastapi import Depends

from app.api.dependencies import AdminUser, CurrentUser, DatabaseSession, is_admin_user
from app.database.auth_repository import AuthRepository
from app.database.models import AnalysisMetric, Product, ProductFeedback, RoutineProduct, SkinProfile, User
from app.models.auth import (
    AnalysisHistoryItem,
    AnalysisHistoryPage,
    AdminFeedbackItem,
    AdminFeedbackStatusRequest,
    AdminMetricSummary,
    AnalysisDetailResponse,
    ChangePasswordRequest,
    DeleteAccountRequest,
    FavoriteRequest,
    ProductFeedbackRequest,
    ProductFeedbackResponse,
    ForgotPasswordRequest,
    RoutineProductRequest,
    ManualRoutineProductRequest,
    RoutineProductResponse,
    RoutineTimeRequest,
    RefreshTokenRequest,
    ResetPasswordRequest,
    SocialLoginRequest,
    VerifyEmailRequest,
    RegisterRequest,
    SkinProfileResponse,
    TokenResponse,
    UserResponse,
)
from app.models.compatibility_analysis import CompatibilitySkinProfile
from app.services.auth import create_access_token, create_email_verification_token, create_password_reset_token, create_refresh_token, hash_password, hash_refresh_token, verify_password
from app.services.social_auth import SocialTokenError, VerifiedSocialIdentity, unusable_password, verify_apple_token, verify_google_token
from app.services.email import EmailDeliveryError, send_email_verification, send_password_reset_email
from app.services.plan_entitlements import entitlements_for


router = APIRouter(prefix="/v1", tags=["Auth & Profile"])
logger = logging.getLogger(__name__)


def user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        email_verified=user.email_verified,
        plan=user.plan,
        is_admin=is_admin_user(user),
        is_active=user.is_active,
        created_at=user.created_at,
    )


def token_response(user: User, repository: AuthRepository) -> TokenResponse:
    token, expires_in = create_access_token(user.id)
    refresh_token, refresh_hash, refresh_expires_at, refresh_expires_in = create_refresh_token()
    repository.create_refresh_token(user.id, refresh_hash, refresh_expires_at)
    repository.session.commit()
    return TokenResponse(
        access_token=token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        refresh_expires_in=refresh_expires_in,
        user=user_response(user),
    )


def _verify_social(provider: str, identity_token: str) -> VerifiedSocialIdentity:
    try:
        return verify_google_token(identity_token) if provider == "google" else verify_apple_token(identity_token)
    except SocialTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _social_token_response(identity: VerifiedSocialIdentity, session: DatabaseSession) -> TokenResponse:
    repository = AuthRepository(session)
    stored_identity = repository.get_social_identity(identity.provider, identity.subject)
    if stored_identity is not None:
        stored_identity.last_login_at = datetime.now(timezone.utc)
        session.commit()
        user = repository.get_user(stored_identity.user_id)
        if user is None or not user.is_active:
            raise HTTPException(status_code=401, detail="Kullanıcı bulunamadı veya aktif değil.")
        return token_response(user, repository)
    if not identity.email or not identity.email_verified:
        raise HTTPException(status_code=409, detail="İlk sosyal girişte doğrulanmış e-posta adresi gerekli.")
    if repository.get_user_by_email(identity.email) is not None:
        raise HTTPException(
            status_code=409,
            detail="Bu e-posta ile bir hesap var. Önce e-posta ve şifrenle giriş yap, sonra Hesap ekranından sosyal hesabını bağla.",
        )
    try:
        user = repository.create_user(email=identity.email, password_hash=hash_password(unusable_password()))
        user.email_verified = True
        user.email_verified_at = datetime.now(timezone.utc)
        session.commit()
        repository.add_social_identity(user, provider=identity.provider, subject=identity.subject, email=identity.email)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Bu sosyal hesap başka bir kullanıcıya bağlı.") from exc
    return token_response(user, repository)


def _link_social_identity(provider: str, identity: VerifiedSocialIdentity, user: User, session: DatabaseSession) -> UserResponse:
    repository = AuthRepository(session)
    existing = repository.get_social_identity(provider, identity.subject)
    if existing is not None and existing.user_id != user.id:
        raise HTTPException(status_code=409, detail="Bu sosyal hesap başka bir kullanıcıya bağlı.")
    if existing is None:
        try:
            repository.add_social_identity(user, provider=provider, subject=identity.subject, email=identity.email)
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=f"Hesabına zaten farklı bir {provider.title()} kimliği bağlı.") from exc
    return user_response(user)


def profile_response(profile: SkinProfile | None) -> SkinProfileResponse:
    if profile is None:
        return SkinProfileResponse()
    return SkinProfileResponse(
        skin_type=profile.skin_type,
        sensitivity=profile.sensitivity,
        appearance=profile.appearance,
        goals=profile.goals,
        avoid_ingredients=profile.avoid_ingredients,
        updated_at=profile.updated_at,
    )


@router.post("/auth/register", response_model=TokenResponse, status_code=201)
def register(request: RegisterRequest, session: DatabaseSession):
    repository = AuthRepository(session)
    if repository.get_user_by_email(request.email) is not None:
        raise HTTPException(status_code=409, detail="Bu e-posta adresi zaten kayıtlı.")
    try:
        user = repository.create_user(
            email=request.email,
            password_hash=hash_password(request.password),
        )
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Bu e-posta adresi zaten kayıtlı.") from exc
    try:
        _send_verification(user, repository)
    except EmailDeliveryError:
        logger.exception("Initial verification email could not be delivered")
    return token_response(user, repository)


def _send_verification(user: User, repository: AuthRepository) -> None:
    raw_token, token_hash, expires_at = create_email_verification_token()
    repository.create_email_verification_token(user.id, token_hash, expires_at)
    base_url = os.getenv("EMAIL_VERIFICATION_URL_BASE", "cosmeticai://verify-email")
    separator = "&" if "?" in base_url else "?"
    verification_link = f"{base_url}{separator}token={quote(raw_token)}"
    send_email_verification(user.email, verification_link)


@router.post("/auth/token", response_model=TokenResponse)
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: DatabaseSession,
):
    user = AuthRepository(session).get_user_by_email(form.username)
    if user is None or not user.is_active or not verify_password(form.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-posta veya şifre hatalı.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token_response(user, AuthRepository(session))


@router.post("/auth/google", response_model=TokenResponse)
def google_login(request: SocialLoginRequest, session: DatabaseSession):
    return _social_token_response(_verify_social("google", request.identity_token), session)


@router.post("/auth/apple", response_model=TokenResponse)
def apple_login(request: SocialLoginRequest, session: DatabaseSession):
    return _social_token_response(_verify_social("apple", request.identity_token), session)


@router.post("/auth/google/link", response_model=UserResponse)
def link_google(request: SocialLoginRequest, user: CurrentUser, session: DatabaseSession):
    return _link_social_identity("google", _verify_social("google", request.identity_token), user, session)


@router.post("/auth/apple/link", response_model=UserResponse)
def link_apple(request: SocialLoginRequest, user: CurrentUser, session: DatabaseSession):
    return _link_social_identity("apple", _verify_social("apple", request.identity_token), user, session)


@router.post("/auth/refresh", response_model=TokenResponse)
def refresh_access_token(request: RefreshTokenRequest, session: DatabaseSession):
    repository = AuthRepository(session)
    stored = repository.get_refresh_token(hash_refresh_token(request.refresh_token))
    now = datetime.now(timezone.utc)
    if stored is None or stored.revoked_at is not None:
        raise HTTPException(status_code=401, detail="Oturum yenileme anahtarı geçersiz.")
    expires_at = stored.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        repository.revoke_refresh_token(stored, now)
        session.commit()
        raise HTTPException(status_code=401, detail="Oturumun süresi dolmuş. Tekrar giriş yap.")
    user = repository.get_user(stored.user_id)
    if user is None or not user.is_active:
        repository.revoke_refresh_token(stored, now)
        session.commit()
        raise HTTPException(status_code=401, detail="Kullanıcı bulunamadı veya aktif değil.")
    repository.revoke_refresh_token(stored, now)
    return token_response(user, repository)


@router.post("/auth/logout", status_code=204)
def logout(request: RefreshTokenRequest, session: DatabaseSession):
    repository = AuthRepository(session)
    stored = repository.get_refresh_token(hash_refresh_token(request.refresh_token))
    if stored is not None and stored.revoked_at is None:
        repository.revoke_refresh_token(stored, datetime.now(timezone.utc))
        session.commit()


@router.post("/auth/forgot-password", status_code=202)
def forgot_password(request: ForgotPasswordRequest, session: DatabaseSession):
    repository = AuthRepository(session)
    user = repository.get_user_by_email(request.email)
    if user is None or not user.is_active:
        return
    raw_token, token_hash, expires_at = create_password_reset_token()
    repository.create_password_reset_token(user.id, token_hash, expires_at)
    base_url = os.getenv("PASSWORD_RESET_URL_BASE", "cosmeticai://reset-password")
    separator = "&" if "?" in base_url else "?"
    reset_link = f"{base_url}{separator}token={quote(raw_token)}"
    try:
        send_password_reset_email(user.email, reset_link)
    except EmailDeliveryError as exc:
        raise HTTPException(status_code=503, detail="Sıfırlama e-postası gönderilemedi.") from exc


@router.post("/auth/reset-password", status_code=204)
def reset_password(request: ResetPasswordRequest, session: DatabaseSession):
    repository = AuthRepository(session)
    stored = repository.get_password_reset_token(hash_refresh_token(request.token))
    now = datetime.now(timezone.utc)
    if stored is None or stored.used_at is not None:
        raise HTTPException(status_code=400, detail="Sıfırlama bağlantısı geçersiz veya kullanılmış.")
    expires_at = stored.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        stored.used_at = now
        session.commit()
        raise HTTPException(status_code=400, detail="Sıfırlama bağlantısının süresi dolmuş.")
    user = repository.get_user(stored.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=400, detail="Sıfırlama bağlantısı geçersiz.")
    user.password_hash = hash_password(request.new_password)
    stored.used_at = now
    repository.revoke_all_refresh_tokens(user.id, now)
    session.commit()


@router.post("/auth/verify-email", status_code=204)
def verify_email(request: VerifyEmailRequest, session: DatabaseSession):
    repository = AuthRepository(session)
    stored = repository.get_email_verification_token(hash_refresh_token(request.token))
    now = datetime.now(timezone.utc)
    if stored is None or stored.used_at is not None:
        raise HTTPException(status_code=400, detail="Doğrulama bağlantısı geçersiz veya kullanılmış.")
    expires_at = stored.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        stored.used_at = now
        session.commit()
        raise HTTPException(status_code=400, detail="Doğrulama bağlantısının süresi dolmuş.")
    user = repository.get_user(stored.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=400, detail="Doğrulama bağlantısı geçersiz.")
    user.email_verified = True
    user.email_verified_at = now
    stored.used_at = now
    session.commit()


@router.post("/users/me/email-verification", status_code=202)
def resend_email_verification(user: CurrentUser, session: DatabaseSession):
    if user.email_verified:
        return
    try:
        _send_verification(user, AuthRepository(session))
    except EmailDeliveryError as exc:
        raise HTTPException(status_code=503, detail="Doğrulama e-postası gönderilemedi.") from exc


@router.get("/users/me", response_model=UserResponse)
def get_me(user: CurrentUser):
    return user_response(user)


@router.get("/admin/metrics/summary", response_model=AdminMetricSummary, tags=["Admin"])
def get_admin_metric_summary(_admin: AdminUser, session: DatabaseSession):
    total, successful, failed, average_ms, formula_hits, ingredient_hits = session.execute(
        select(
            func.count(AnalysisMetric.id),
            func.sum(case((AnalysisMetric.status.in_(("complete", "partial")), 1), else_=0)),
            func.sum(case((AnalysisMetric.status.in_(("failed", "rejected", "not_found")), 1), else_=0)),
            func.avg(AnalysisMetric.total_ms),
            func.sum(case((AnalysisMetric.formula_cache_used.is_(True), 1), else_=0)),
            func.sum(case((AnalysisMetric.ingredient_cache_used.is_(True), 1), else_=0)),
        )
    ).one()
    pending = session.scalar(
        select(func.count(ProductFeedback.id)).where(ProductFeedback.status == "pending")
    ) or 0
    total = int(total or 0)
    successful = int(successful or 0)
    return AdminMetricSummary(
        total_analyses=total,
        successful_analyses=successful,
        failed_analyses=int(failed or 0),
        success_rate=round(successful * 100 / total, 1) if total else 0,
        average_duration_ms=round(float(average_ms or 0)),
        formula_cache_hits=int(formula_hits or 0),
        ingredient_cache_hits=int(ingredient_hits or 0),
        pending_feedback=int(pending),
    )


@router.get("/admin/feedback", response_model=list[AdminFeedbackItem], tags=["Admin"])
def get_admin_feedback(_admin: AdminUser, session: DatabaseSession):
    rows = session.execute(
        select(ProductFeedback, Product, User)
        .join(Product, Product.id == ProductFeedback.product_id)
        .join(User, User.id == ProductFeedback.user_id)
        .order_by(ProductFeedback.created_at.desc())
        .limit(100)
    ).all()
    return [
        AdminFeedbackItem(
            id=feedback.id,
            analysis_id=feedback.analysis_id,
            reason=feedback.reason,
            details=feedback.details,
            status=feedback.status,
            product_brand=product.brand,
            product_name=product.product_name,
            user_email=user.email,
            created_at=feedback.created_at,
        )
        for feedback, product, user in rows
    ]


@router.patch("/admin/feedback/{feedback_id}", response_model=AdminFeedbackItem, tags=["Admin"])
def update_admin_feedback(
    feedback_id: str,
    request: AdminFeedbackStatusRequest,
    _admin: AdminUser,
    session: DatabaseSession,
):
    row = session.execute(
        select(ProductFeedback, Product, User)
        .join(Product, Product.id == ProductFeedback.product_id)
        .join(User, User.id == ProductFeedback.user_id)
        .where(ProductFeedback.id == feedback_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Bildirim bulunamadı.")
    feedback, product, user = row
    feedback.status = request.status
    feedback.reviewed_at = datetime.now(timezone.utc) if request.status == "reviewed" else None
    session.commit()
    return AdminFeedbackItem(
        id=feedback.id,
        analysis_id=feedback.analysis_id,
        reason=feedback.reason,
        details=feedback.details,
        status=feedback.status,
        product_brand=product.brand,
        product_name=product.product_name,
        user_email=user.email,
        created_at=feedback.created_at,
    )


@router.put("/users/me/password", status_code=204)
def change_password(
    request: ChangePasswordRequest,
    user: CurrentUser,
    session: DatabaseSession,
):
    if not verify_password(request.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Mevcut şifre hatalı.")
    if verify_password(request.new_password, user.password_hash):
        raise HTTPException(
            status_code=400,
            detail="Yeni şifre mevcut şifreden farklı olmalıdır.",
        )
    AuthRepository(session).update_password(user, hash_password(request.new_password))


@router.delete("/users/me", status_code=204)
def delete_account(request: DeleteAccountRequest, user: CurrentUser, session: DatabaseSession):
    if not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=400, detail="Şifre hatalı.")
    AuthRepository(session).delete_user_account(user)


@router.get("/users/me/profile", response_model=SkinProfileResponse)
def get_my_profile(user: CurrentUser, session: DatabaseSession):
    return profile_response(AuthRepository(session).get_skin_profile(user.id))


@router.put("/users/me/profile", response_model=SkinProfileResponse)
def update_my_profile(
    profile: CompatibilitySkinProfile,
    user: CurrentUser,
    session: DatabaseSession,
):
    stored = AuthRepository(session).upsert_skin_profile(user.id, profile)
    return profile_response(stored)


@router.get("/users/me/analyses", response_model=AnalysisHistoryPage)
def get_my_analyses(
    user: CurrentUser,
    session: DatabaseSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
):
    repository = AuthRepository(session)
    stored_total = repository.count_user_analyses(user.id)
    history_limit = entitlements_for(user.plan).history_limit
    visible_total = stored_total if history_limit is None else min(stored_total, history_limit)
    offset = (page - 1) * page_size
    rows = repository.list_user_analyses(
        user.id,
        limit=max(0, min(page_size, visible_total - offset)),
        offset=offset,
    ) if offset < visible_total else []
    items = [
        AnalysisHistoryItem(
            id=analysis.id,
            status=analysis.status,
            product_brand=product.brand,
            product_name=product.product_name,
            product_variant=product.variant,
            created_at=analysis.created_at,
            is_favorite=analysis.is_favorite,
        )
        for analysis, product in rows
    ]
    return AnalysisHistoryPage(
        items=items,
        page=page,
        page_size=page_size,
        total=visible_total,
        has_more=offset + len(items) < visible_total,
        history_limit=history_limit,
    )


def _require_history_access(user: CurrentUser, session: DatabaseSession, analysis_id: str) -> None:
    history_limit = entitlements_for(user.plan).history_limit
    if history_limit is not None and not AuthRepository(session).analysis_is_in_recent_history(
        user.id, analysis_id, history_limit
    ):
        raise HTTPException(
            status_code=403,
            detail="Ücretsiz planda yalnızca son 10 analize erişebilirsin.",
        )


@router.get("/users/me/analyses/{analysis_id}", response_model=AnalysisDetailResponse)
def get_my_analysis(analysis_id: str, user: CurrentUser, session: DatabaseSession):
    _require_history_access(user, session, analysis_id)
    analysis = AuthRepository(session).get_user_analysis(user.id, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Analiz bulunamadı.")
    product = session.get(Product, analysis.product_id)
    formulas = []
    for result in analysis.results:
        links = result.formula.ingredient_links
        formulas.append({
            "formula_name": result.formula.formula_name,
            "status": result.status,
            "score": result.score,
            "raw_score": result.raw_score,
            "verdict": result.verdict,
            "coverage": result.coverage,
            "dimensions": result.dimensions or {},
            "strengths": result.strengths,
            "cautions": result.cautions,
            "avoid_matches": result.avoid_matches,
            "warnings": result.warnings,
            "ingredients": [{"name": link.raw_name, "canonical_name": link.ingredient.inci_name, "functions": link.ingredient.functions, "flags": link.ingredient.flags, "evidence_status": link.verification_status} for link in links],
        })
    return AnalysisDetailResponse(id=analysis.id, status=analysis.status, created_at=analysis.created_at, is_favorite=analysis.is_favorite, product={"brand": product.brand, "product_name": product.product_name, "variant": product.variant, "category": product.category}, formulas=formulas)


@router.put("/users/me/analyses/{analysis_id}/favorite", response_model=AnalysisHistoryItem)
def update_favorite(analysis_id: str, request: FavoriteRequest, user: CurrentUser, session: DatabaseSession):
    _require_history_access(user, session, analysis_id)
    analysis = AuthRepository(session).set_analysis_favorite(user.id, analysis_id, request.is_favorite)
    if analysis is None:
        raise HTTPException(status_code=404, detail="Analiz bulunamadı.")
    product = session.get(Product, analysis.product_id)
    return AnalysisHistoryItem(id=analysis.id, status=analysis.status, product_brand=product.brand, product_name=product.product_name, product_variant=product.variant, created_at=analysis.created_at, is_favorite=analysis.is_favorite)


@router.post("/users/me/analyses/{analysis_id}/feedback", response_model=ProductFeedbackResponse, status_code=201)
def create_product_feedback(
    analysis_id: str,
    request: ProductFeedbackRequest,
    user: CurrentUser,
    session: DatabaseSession,
):
    feedback = AuthRepository(session).create_product_feedback(
        user.id, analysis_id, request.reason, request.details
    )
    if feedback is None:
        raise HTTPException(status_code=404, detail="Analiz bulunamadı.")
    return ProductFeedbackResponse(
        id=feedback.id,
        reason=feedback.reason,
        details=feedback.details,
        status=feedback.status,
        created_at=feedback.created_at,
    )


@router.post("/users/me/routine", response_model=RoutineProductResponse, status_code=201)
def add_to_routine(request: RoutineProductRequest, user: CurrentUser, session: DatabaseSession):
    item = AuthRepository(session).add_routine_product(user.id, request.analysis_id, request.routine_time)
    if item is None:
        raise HTTPException(status_code=404, detail="Analiz bulunamadı.")
    return _routine_response(item)


@router.post("/users/me/routine/manual", response_model=RoutineProductResponse, status_code=201)
def add_manual_to_routine(
    request: ManualRoutineProductRequest,
    user: CurrentUser,
    session: DatabaseSession,
):
    values = {
        "product_name": request.product_name.strip(),
        "product_brand": request.product_brand.strip() if request.product_brand else None,
        "product_variant": request.product_variant.strip() if request.product_variant else None,
        "product_category": request.product_category.strip() if request.product_category else None,
    }
    if not values["product_name"]:
        raise HTTPException(status_code=422, detail="Ürün adı boş bırakılamaz.")
    item = AuthRepository(session).add_manual_routine_product(
        user.id, **values, routine_time=request.routine_time,
    )
    return _routine_response(item)


@router.get("/users/me/routine", response_model=list[RoutineProductResponse])
def get_routine(user: CurrentUser, session: DatabaseSession):
    return [_routine_response(item) for item in AuthRepository(session).list_routine_products(user.id)]


def _routine_response(item: RoutineProduct) -> RoutineProductResponse:
    if item.product is None:
        return RoutineProductResponse(
            id=item.id,
            product_brand=item.manual_brand or "Marka belirtilmedi",
            product_name=item.manual_name or "Ürün adı belirtilmedi",
            product_variant=item.manual_variant,
            product_category=item.manual_category,
            routine_time=item.routine_time,
            ingredients=[],
        )
    latest_formula = max(
        item.product.formulas,
        key=lambda formula: formula.formula_version,
        default=None,
    )
    ingredients = (
        [link.raw_name for link in latest_formula.ingredient_links]
        if latest_formula is not None
        else []
    )
    return RoutineProductResponse(
        id=item.id,
        product_brand=item.product.brand,
        product_name=item.product.product_name,
        product_variant=item.product.variant,
        product_category=item.product.category,
        routine_time=item.routine_time,
        ingredients=ingredients,
    )


@router.put("/users/me/routine/{routine_id}", response_model=RoutineProductResponse)
def update_routine_time(
    routine_id: str,
    request: RoutineTimeRequest,
    user: CurrentUser,
    session: DatabaseSession,
):
    try:
        item = AuthRepository(session).update_routine_time(
            user.id, routine_id, request.routine_time
        )
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Bu ürün seçilen zamanda zaten rutinde.",
        ) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Rutin ürünü bulunamadı.")
    return _routine_response(item)


@router.delete("/users/me/routine/{routine_id}", status_code=204)
def remove_from_routine(routine_id: str, user: CurrentUser, session: DatabaseSession):
    if not AuthRepository(session).remove_routine_product(user.id, routine_id):
        raise HTTPException(status_code=404, detail="Rutin ürünü bulunamadı.")
