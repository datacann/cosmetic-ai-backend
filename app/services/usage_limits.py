import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database.models import UsageEvent, User
from app.services.plan_entitlements import entitlements_for


ISTANBUL = ZoneInfo("Europe/Istanbul")


class UsageLimitExceededError(RuntimeError):
    pass


class ConcurrentAnalysisError(RuntimeError):
    pass


class RewardedAnalysisUnavailableError(RuntimeError):
    pass


def guest_key_for_ip(ip_address: str) -> str:
    secret = os.getenv("AUTH_SECRET_KEY", "local-development-usage-secret")
    return hmac.new(secret.encode(), ip_address.encode(), hashlib.sha256).hexdigest()


def _month_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    current = (now or datetime.now(timezone.utc)).astimezone(ISTANBUL)
    start_local = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local.replace(
        year=start_local.year + (1 if start_local.month == 12 else 0),
        month=1 if start_local.month == 12 else start_local.month + 1,
    )
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _day_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    current = (now or datetime.now(timezone.utc)).astimezone(ISTANBUL)
    start_local = current.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(timezone.utc), (start_local + timedelta(days=1)).astimezone(timezone.utc)


def _subject_filter(user_id: str | None, guest_key: str | None):
    return UsageEvent.user_id == user_id if user_id else UsageEvent.guest_key == guest_key


def usage_status(session: Session, *, user: User | None, guest_key: str | None) -> dict:
    plan = user.plan if user else "guest"
    entitlement = entitlements_for(plan)
    limit = entitlement.analysis_limit
    start, reset_at = _month_window()
    subject = _subject_filter(user.id if user else None, guest_key)
    used = session.scalar(
        select(func.count()).select_from(UsageEvent).where(
            subject,
            UsageEvent.created_at >= start,
            UsageEvent.kind == "vision_analysis",
            UsageEvent.charged.is_(True),
            UsageEvent.status == "completed",
        )
    ) or 0
    rewarded_earned = session.scalar(
        select(func.count()).select_from(UsageEvent).where(
            subject,
            UsageEvent.created_at >= start,
            UsageEvent.kind == "rewarded_analysis",
            UsageEvent.status == "completed",
        )
    ) or 0
    day_start, _ = _day_window()
    rewarded_earned_today = session.scalar(
        select(func.count()).select_from(UsageEvent).where(
            subject,
            UsageEvent.created_at >= day_start,
            UsageEvent.kind == "rewarded_analysis",
            UsageEvent.status == "completed",
        )
    ) or 0
    effective_limit = limit + rewarded_earned
    return {
        "plan": entitlement.plan,
        "period": entitlement.period,
        "analysis_limit": limit,
        "used": used,
        "remaining": max(0, effective_limit - used),
        "resets_at": reset_at,
        "history_limit": entitlement.history_limit,
        "ads_enabled": entitlement.ads_enabled,
        "rewarded_daily_limit": entitlement.rewarded_daily_limit,
        "rewarded_earned": rewarded_earned,
        "rewarded_earned_today": rewarded_earned_today,
        "rewarded_available": bool(
            user
            and entitlement.ads_enabled
            and rewarded_earned_today < entitlement.rewarded_daily_limit
            and effective_limit - used <= 0
        ),
        "premium_entitled": entitlement.premium_entitled,
    }


def grant_rewarded_analysis(session: Session, *, user: User, claim_id: str) -> dict:
    entitlement = entitlements_for(user.plan)
    if not entitlement.ads_enabled or entitlement.rewarded_daily_limit <= 0:
        raise RewardedAnalysisUnavailableError("Bu planda ödüllü reklam hakkı bulunmuyor.")
    request_key = f"rewarded:{user.id}:{claim_id}"
    existing = session.scalar(select(UsageEvent).where(UsageEvent.request_key == request_key))
    if existing is None:
        status = usage_status(session, user=user, guest_key=None)
        if status["remaining"] > 0:
            raise RewardedAnalysisUnavailableError("Ödüllü reklam yalnızca aylık analiz hakkın dolduğunda kullanılabilir.")
        if status["rewarded_earned_today"] >= entitlement.rewarded_daily_limit:
            raise RewardedAnalysisUnavailableError("Bugünkü ödüllü reklam hakkını kullandın.")
        session.add(UsageEvent(
            user_id=user.id,
            request_key=request_key,
            kind="rewarded_analysis",
            charged=False,
            status="completed",
            completed_at=datetime.now(timezone.utc),
        ))
        session.commit()
    return usage_status(session, user=user, guest_key=None)


def reserve_vision_analysis(
    session: Session,
    *,
    user: User | None,
    guest_key: str | None,
    request_key: str,
) -> UsageEvent:
    existing = session.scalar(select(UsageEvent).where(UsageEvent.request_key == request_key))
    if existing is not None:
        return existing
    subject = _subject_filter(user.id if user else None, guest_key)
    active_since = datetime.now(timezone.utc) - timedelta(minutes=10)
    active = session.scalar(
        select(UsageEvent.id).where(
            subject,
            UsageEvent.status == "reserved",
            UsageEvent.created_at >= active_since,
        ).limit(1)
    )
    if active is not None:
        raise ConcurrentAnalysisError("Aynı anda yalnızca bir ürün analizi başlatabilirsin.")
    status = usage_status(session, user=user, guest_key=guest_key)
    if status["remaining"] <= 0:
        raise UsageLimitExceededError("Bu ayki fotoğraflı analiz hakkın doldu. Barkod, geçmiş ve rutin özelliklerini kullanmaya devam edebilirsin.")
    event = UsageEvent(
        user_id=user.id if user else None,
        guest_key=None if user else guest_key,
        request_key=request_key,
        kind="vision_analysis",
        charged=True,
        status="reserved",
    )
    session.add(event)
    session.commit()
    return event


def finish_usage_event(session: Session, event_id: str, *, charged: bool, status: str) -> None:
    event = session.get(UsageEvent, event_id)
    if event is None:
        return
    event.charged = charged
    event.status = status
    event.completed_at = datetime.now(timezone.utc)
    session.commit()
