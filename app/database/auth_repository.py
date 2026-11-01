from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, selectinload

from app.database.models import Analysis, AnalysisFormulaResult, AnalysisMetric, EmailVerificationToken, FormulaIngredient, PasswordResetToken, Product, ProductFeedback, ProductFormula, RefreshToken, RoutineProduct, SkinProfile, SocialIdentity, User
from app.models.auth import normalize_email
from app.models.compatibility_analysis import CompatibilitySkinProfile


class AuthRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_user_by_email(self, email: str) -> User | None:
        return self.session.scalar(
            select(User).where(User.normalized_email == normalize_email(email))
        )

    def get_user(self, user_id: str) -> User | None:
        return self.session.get(User, user_id)

    def get_social_identity(self, provider: str, subject: str) -> SocialIdentity | None:
        return self.session.scalar(select(SocialIdentity).where(
            SocialIdentity.provider == provider,
            SocialIdentity.provider_subject == subject,
        ))

    def add_social_identity(self, user: User, *, provider: str, subject: str, email: str | None) -> SocialIdentity:
        identity = SocialIdentity(
            user_id=user.id,
            provider=provider,
            provider_subject=subject,
            provider_email=normalize_email(email) if email else None,
        )
        self.session.add(identity)
        self.session.commit()
        return identity

    def delete_user_account(self, user: User) -> None:
        analysis_ids = select(Analysis.id).where(Analysis.user_id == user.id)
        self.session.execute(delete(ProductFeedback).where(ProductFeedback.user_id == user.id))
        self.session.execute(update(AnalysisMetric).where(AnalysisMetric.user_id == user.id).values(user_id=None, analysis_id=None))
        self.session.execute(delete(AnalysisFormulaResult).where(AnalysisFormulaResult.analysis_id.in_(analysis_ids)))
        self.session.execute(delete(Analysis).where(Analysis.user_id == user.id))
        self.session.execute(delete(RoutineProduct).where(RoutineProduct.user_id == user.id))
        self.session.execute(delete(SkinProfile).where(SkinProfile.user_id == user.id))
        self.session.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))
        self.session.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == user.id))
        self.session.execute(delete(EmailVerificationToken).where(EmailVerificationToken.user_id == user.id))
        self.session.execute(delete(SocialIdentity).where(SocialIdentity.user_id == user.id))
        self.session.delete(user)
        self.session.commit()

    def create_user(self, *, email: str, password_hash: str) -> User:
        normalized = normalize_email(email)
        user = User(
            email=normalized,
            normalized_email=normalized,
            password_hash=password_hash,
        )
        self.session.add(user)
        self.session.commit()
        return user

    def update_password(self, user: User, password_hash: str) -> None:
        user.password_hash = password_hash
        self.session.commit()

    def create_refresh_token(self, user_id: str, token_hash: str, expires_at):
        token = RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        self.session.add(token)
        return token

    def get_refresh_token(self, token_hash: str):
        return self.session.scalar(
            select(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .with_for_update()
        )

    def revoke_refresh_token(self, token: RefreshToken, revoked_at) -> None:
        token.revoked_at = revoked_at

    def revoke_all_refresh_tokens(self, user_id: str, revoked_at) -> None:
        for token in self.session.scalars(
            select(RefreshToken).where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
        ):
            token.revoked_at = revoked_at

    def create_password_reset_token(self, user_id: str, token_hash: str, expires_at):
        for token in self.session.scalars(
            select(PasswordResetToken).where(
                PasswordResetToken.user_id == user_id,
                PasswordResetToken.used_at.is_(None),
            )
        ):
            token.used_at = expires_at
        token = PasswordResetToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
        self.session.add(token)
        self.session.commit()

    def get_password_reset_token(self, token_hash: str):
        return self.session.scalar(
            select(PasswordResetToken)
            .where(PasswordResetToken.token_hash == token_hash)
            .with_for_update()
        )

    def create_email_verification_token(self, user_id: str, token_hash: str, expires_at):
        for token in self.session.scalars(
            select(EmailVerificationToken).where(
                EmailVerificationToken.user_id == user_id,
                EmailVerificationToken.used_at.is_(None),
            )
        ):
            token.used_at = expires_at
        token = EmailVerificationToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
        self.session.add(token)
        self.session.commit()

    def get_email_verification_token(self, token_hash: str):
        return self.session.scalar(
            select(EmailVerificationToken)
            .where(EmailVerificationToken.token_hash == token_hash)
            .with_for_update()
        )

    def get_skin_profile(self, user_id: str) -> SkinProfile | None:
        return self.session.scalar(
            select(SkinProfile).where(SkinProfile.user_id == user_id)
        )

    def upsert_skin_profile(
        self, user_id: str, profile: CompatibilitySkinProfile
    ) -> SkinProfile:
        stored = self.get_skin_profile(user_id)
        if stored is None:
            stored = SkinProfile(user_id=user_id)
            self.session.add(stored)
        stored.skin_type = profile.skin_type
        stored.sensitivity = profile.sensitivity
        stored.appearance = profile.appearance
        stored.goals = profile.goals
        stored.avoid_ingredients = profile.avoid_ingredients
        self.session.commit()
        return stored

    def list_user_analyses(self, user_id: str, *, limit: int = 20, offset: int = 0):
        return list(
            self.session.execute(
                select(Analysis, Product)
                .join(Product, Product.id == Analysis.product_id)
                .where(Analysis.user_id == user_id)
                .order_by(Analysis.created_at.desc())
                .offset(offset)
                .limit(limit)
            ).all()
        )

    def count_user_analyses(self, user_id: str) -> int:
        return self.session.scalar(
            select(func.count()).select_from(Analysis).where(Analysis.user_id == user_id)
        ) or 0

    def analysis_is_in_recent_history(self, user_id: str, analysis_id: str, limit: int) -> bool:
        recent_ids = select(Analysis.id).where(Analysis.user_id == user_id).order_by(
            Analysis.created_at.desc()
        ).limit(limit)
        return self.session.scalar(
            select(Analysis.id).where(Analysis.id == analysis_id, Analysis.id.in_(recent_ids))
        ) is not None

    def get_user_analysis(self, user_id: str, analysis_id: str):
        return self.session.scalar(
            select(Analysis)
            .where(Analysis.id == analysis_id, Analysis.user_id == user_id)
            .options(selectinload(Analysis.results).selectinload(AnalysisFormulaResult.formula).selectinload(ProductFormula.ingredient_links).selectinload(FormulaIngredient.ingredient))
        )

    def set_analysis_favorite(self, user_id: str, analysis_id: str, value: bool):
        analysis = self.get_user_analysis(user_id, analysis_id)
        if analysis is None:
            return None
        analysis.is_favorite = value
        self.session.commit()
        return analysis

    def create_product_feedback(self, user_id: str, analysis_id: str, reason: str, details: str | None):
        analysis = self.get_user_analysis(user_id, analysis_id)
        if analysis is None:
            return None
        existing = self.session.scalar(
            select(ProductFeedback).where(
                ProductFeedback.user_id == user_id,
                ProductFeedback.analysis_id == analysis_id,
                ProductFeedback.reason == reason,
            )
        )
        if existing is not None:
            if details:
                existing.details = details
                self.session.commit()
            return existing
        feedback = ProductFeedback(
            user_id=user_id,
            analysis_id=analysis_id,
            product_id=analysis.product_id,
            reason=reason,
            details=details,
        )
        self.session.add(feedback)
        self.session.commit()
        return feedback

    def add_routine_product(self, user_id: str, analysis_id: str, routine_time: str):
        analysis = self.get_user_analysis(user_id, analysis_id)
        if analysis is None:
            return None
        existing = self.session.scalar(select(RoutineProduct).where(RoutineProduct.user_id == user_id, RoutineProduct.product_id == analysis.product_id, RoutineProduct.routine_time == routine_time))
        if existing is not None:
            return existing
        item = RoutineProduct(user_id=user_id, product_id=analysis.product_id, routine_time=routine_time)
        self.session.add(item)
        self.session.commit()
        return item

    def add_manual_routine_product(
        self, user_id: str, *, product_name: str, product_brand: str | None,
        product_variant: str | None, product_category: str | None, routine_time: str,
    ):
        item = RoutineProduct(
            user_id=user_id,
            product_id=None,
            manual_name=product_name,
            manual_brand=product_brand,
            manual_variant=product_variant,
            manual_category=product_category,
            routine_time=routine_time,
        )
        self.session.add(item)
        self.session.commit()
        return item

    def list_routine_products(self, user_id: str):
        return list(
            self.session.scalars(
                select(RoutineProduct)
                .where(RoutineProduct.user_id == user_id)
                .options(
                    selectinload(RoutineProduct.product)
                    .selectinload(Product.formulas)
                    .selectinload(ProductFormula.ingredient_links)
                )
                .order_by(RoutineProduct.created_at.desc())
            )
        )

    def update_routine_time(self, user_id: str, routine_id: str, routine_time: str):
        item = self.session.scalar(
            select(RoutineProduct)
            .where(RoutineProduct.id == routine_id, RoutineProduct.user_id == user_id)
            .options(
                selectinload(RoutineProduct.product)
                .selectinload(Product.formulas)
                .selectinload(ProductFormula.ingredient_links)
            )
        )
        if item is None:
            return None
        item.routine_time = routine_time
        self.session.commit()
        return item

    def remove_routine_product(self, user_id: str, routine_id: str) -> bool:
        item = self.session.scalar(select(RoutineProduct).where(RoutineProduct.id == routine_id, RoutineProduct.user_id == user_id))
        if item is None:
            return False
        self.session.delete(item)
        self.session.commit()
        return True
