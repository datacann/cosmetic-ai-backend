import os
import secrets
from dataclasses import dataclass

import jwt

from app.models.auth import normalize_email


class SocialTokenError(RuntimeError):
    pass


@dataclass(frozen=True)
class VerifiedSocialIdentity:
    provider: str
    subject: str
    email: str | None
    email_verified: bool


def _audiences(provider: str) -> list[str]:
    key = "GOOGLE_AUTH_CLIENT_IDS" if provider == "google" else "APPLE_AUTH_CLIENT_IDS"
    values = [value.strip() for value in os.getenv(key, "").split(",") if value.strip()]
    if not values:
        raise SocialTokenError(f"{provider.title()} istemci kimliği yapılandırılmamış.")
    return values


def _decode(token: str, *, provider: str, jwks_url: str, issuers: set[str]) -> dict:
    try:
        signing_key = jwt.PyJWKClient(jwks_url, cache_keys=True).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=_audiences(provider),
            options={"verify_iss": False},
        )
    except (jwt.PyJWTError, OSError, ValueError) as exc:
        raise SocialTokenError("Sosyal giriş kimliği doğrulanamadı.") from exc
    if claims.get("iss") not in issuers or not claims.get("sub"):
        raise SocialTokenError("Sosyal giriş sağlayıcısı geçersiz.")
    return claims


def verify_google_token(token: str) -> VerifiedSocialIdentity:
    claims = _decode(
        token,
        provider="google",
        jwks_url="https://www.googleapis.com/oauth2/v3/certs",
        issuers={"accounts.google.com", "https://accounts.google.com"},
    )
    verified = claims.get("email_verified") in (True, "true")
    email = normalize_email(claims["email"]) if claims.get("email") and verified else None
    return VerifiedSocialIdentity("google", claims["sub"], email, verified)


def verify_apple_token(token: str) -> VerifiedSocialIdentity:
    claims = _decode(
        token,
        provider="apple",
        jwks_url="https://appleid.apple.com/auth/keys",
        issuers={"https://appleid.apple.com"},
    )
    verified = claims.get("email_verified") in (True, "true")
    email = normalize_email(claims["email"]) if claims.get("email") and verified else None
    return VerifiedSocialIdentity("apple", claims["sub"], email, verified)


def unusable_password() -> str:
    return secrets.token_urlsafe(48)
