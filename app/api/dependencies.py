from collections.abc import Generator
import os
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.database.auth_repository import AuthRepository
from app.database.models import User
from app.database.session import get_session_factory
from app.services.auth import InvalidAccessTokenError, decode_access_token


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/auth/token", auto_error=False)


def get_database_session() -> Generator[Session, None, None]:
    factory = get_session_factory()
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Veritabanı yapılandırılmamış.",
        )
    with factory() as session:
        yield session


DatabaseSession = Annotated[Session, Depends(get_database_session)]


def get_optional_current_user(
    session: DatabaseSession,
    token: Annotated[str | None, Depends(oauth2_scheme)],
) -> User | None:
    if not token:
        return None
    try:
        user_id = decode_access_token(token)
    except InvalidAccessTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    user = AuthRepository(session).get_user(user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Kullanıcı bulunamadı veya aktif değil.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


OptionalCurrentUser = Annotated[User | None, Depends(get_optional_current_user)]


def get_current_user(user: OptionalCurrentUser) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bu işlem için giriş yapmalısınız.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def is_admin_user(user: User) -> bool:
    admin_emails = {
        email.strip().casefold()
        for email in os.getenv("ADMIN_EMAILS", "").split(",")
        if email.strip()
    }
    return user.normalized_email in admin_emails


def get_admin_user(user: CurrentUser) -> User:
    if not is_admin_user(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bu alan yalnızca yöneticilere açıktır.",
        )
    return user


AdminUser = Annotated[User, Depends(get_admin_user)]
