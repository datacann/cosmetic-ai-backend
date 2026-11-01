import os
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash


ALGORITHM = "HS256"
ISSUER = "cosmetic-ai-api"
AUDIENCE = "cosmetic-ai-mobile"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 30
PASSWORD_RESET_EXPIRE_MINUTES = 15
EMAIL_VERIFICATION_EXPIRE_HOURS = 24
password_hasher = PasswordHash.recommended()


class AuthConfigurationError(RuntimeError):
    pass


class InvalidAccessTokenError(ValueError):
    pass


def _secret_key() -> str:
    value = os.getenv("AUTH_SECRET_KEY", "").strip()
    if len(value) < 32:
        raise AuthConfigurationError("AUTH_SECRET_KEY en az 32 karakter olmalıdır.")
    return value


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return password_hasher.verify(password, password_hash)


def create_access_token(user_id: str) -> tuple[str, int]:
    now = datetime.now(timezone.utc)
    expires_in = ACCESS_TOKEN_EXPIRE_MINUTES * 60
    token = jwt.encode(
        {
            "sub": user_id,
            "iat": now,
            "exp": now + timedelta(seconds=expires_in),
            "iss": ISSUER,
            "aud": AUDIENCE,
        },
        _secret_key(),
        algorithm=ALGORITHM,
    )
    return token, expires_in


def create_refresh_token() -> tuple[str, str, datetime, int]:
    raw_token = secrets.token_urlsafe(48)
    expires_in = REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return raw_token, hash_refresh_token(raw_token), expires_at, expires_in


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_password_reset_token() -> tuple[str, str, datetime]:
    raw_token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=PASSWORD_RESET_EXPIRE_MINUTES)
    return raw_token, hash_refresh_token(raw_token), expires_at


def create_email_verification_token() -> tuple[str, str, datetime]:
    raw_token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=EMAIL_VERIFICATION_EXPIRE_HOURS)
    return raw_token, hash_refresh_token(raw_token), expires_at


def decode_access_token(token: str) -> str:
    try:
        payload = jwt.decode(
            token,
            _secret_key(),
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["sub", "iat", "exp", "iss", "aud"]},
        )
    except (InvalidTokenError, AuthConfigurationError) as exc:
        raise InvalidAccessTokenError("Geçersiz veya süresi dolmuş erişim anahtarı.") from exc
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise InvalidAccessTokenError("Erişim anahtarında kullanıcı kimliği yok.")
    return subject
