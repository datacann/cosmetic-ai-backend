import json
import os
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from sqlalchemy.orm import Session

from app.database.models import User


class RevenueCatConfigurationError(RuntimeError):
    pass


class RevenueCatUnavailableError(RuntimeError):
    pass


def _configuration() -> tuple[str, str]:
    api_key = os.getenv("REVENUECAT_SECRET_API_KEY", "").strip()
    entitlement = os.getenv("REVENUECAT_PREMIUM_ENTITLEMENT_ID", "premium").strip()
    if not api_key:
        raise RevenueCatConfigurationError("RevenueCat sunucu API anahtarı yapılandırılmamış.")
    return api_key, entitlement


def fetch_premium_entitlement(app_user_id: str) -> bool:
    api_key, entitlement_id = _configuration()
    request = Request(
        f"https://api.revenuecat.com/v1/subscribers/{quote(app_user_id, safe='')}",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RevenueCatUnavailableError("RevenueCat abonelik durumu doğrulanamadı.") from exc

    entitlement = payload.get("subscriber", {}).get("entitlements", {}).get(entitlement_id)
    if not entitlement:
        return False
    expiration = entitlement.get("expires_date")
    if expiration is None:
        return True
    try:
        expires_at = datetime.fromisoformat(expiration.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return expires_at > datetime.now(timezone.utc)


def synchronize_user_plan(session: Session, user: User) -> bool:
    premium = fetch_premium_entitlement(user.id)
    expected_plan = "premium" if premium else "free"
    if user.plan != expected_plan:
        user.plan = expected_plan
        session.commit()
        session.refresh(user)
    return premium
