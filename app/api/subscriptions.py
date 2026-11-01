import hashlib
import hmac
import json
import os
import time

from fastapi import APIRouter, HTTPException, Request

from app.api.dependencies import CurrentUser, DatabaseSession
from app.database.models import User
from app.models.auth import UserResponse
from app.services.revenuecat import (
    RevenueCatConfigurationError,
    RevenueCatUnavailableError,
    synchronize_user_plan,
)


router = APIRouter(prefix="/v1/subscriptions", tags=["Subscriptions"])


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        email_verified=user.email_verified,
        plan=user.plan,
        is_active=user.is_active,
        created_at=user.created_at,
    )


@router.post("/refresh", response_model=UserResponse)
def refresh_subscription(user: CurrentUser, session: DatabaseSession):
    try:
        synchronize_user_plan(session, user)
    except RevenueCatConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RevenueCatUnavailableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _user_response(user)


def _verify_webhook(request: Request, raw_body: bytes) -> None:
    expected_authorization = os.getenv("REVENUECAT_WEBHOOK_AUTHORIZATION", "").strip()
    if not expected_authorization:
        raise HTTPException(status_code=503, detail="RevenueCat webhook güvenliği yapılandırılmamış.")
    if not hmac.compare_digest(request.headers.get("authorization", ""), expected_authorization):
        raise HTTPException(status_code=401, detail="Geçersiz webhook yetkilendirmesi.")

    signing_secret = os.getenv("REVENUECAT_WEBHOOK_SIGNING_SECRET", "").strip()
    signature_header = request.headers.get("x-revenuecat-webhook-signature", "")
    if signing_secret:
        parts = dict(part.split("=", 1) for part in signature_header.split(",") if "=" in part)
        timestamp, received = parts.get("t"), parts.get("v1")
        if not timestamp or not received or abs(time.time() - int(timestamp)) > 300:
            raise HTTPException(status_code=401, detail="Geçersiz webhook imzası.")
        message = timestamp.encode() + b"." + raw_body
        expected = hmac.new(signing_secret.encode(), message, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(received, expected):
            raise HTTPException(status_code=401, detail="Geçersiz webhook imzası.")


@router.post("/revenuecat-webhook", status_code=200)
async def revenuecat_webhook(request: Request, session: DatabaseSession):
    raw_body = await request.body()
    _verify_webhook(request, raw_body)
    try:
        event = json.loads(raw_body).get("event", {})
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Geçersiz webhook gövdesi.") from exc
    candidate_ids = [event.get("app_user_id"), event.get("original_app_user_id"), *(event.get("aliases") or [])]
    user = None
    for candidate_id in candidate_ids:
        if candidate_id:
            user = session.get(User, candidate_id)
            if user is not None:
                break
    if user is not None:
        try:
            synchronize_user_plan(session, user)
        except (RevenueCatConfigurationError, RevenueCatUnavailableError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"received": True}
