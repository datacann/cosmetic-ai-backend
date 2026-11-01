import os
from functools import lru_cache

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


load_dotenv()
load_dotenv(".env.database")
load_dotenv(".env.auth")


def get_database_url() -> str | None:
    value = os.getenv("DATABASE_URL", "").strip()
    return value or None


@lru_cache(maxsize=4)
def create_database_engine(database_url: str) -> Engine:
    options = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, pool_pre_ping=True, connect_args=options)


@lru_cache(maxsize=4)
def _session_factory(database_url: str) -> sessionmaker[Session]:
    return sessionmaker(
        bind=create_database_engine(database_url),
        expire_on_commit=False,
        autoflush=False,
    )


def get_session_factory(database_url: str | None = None) -> sessionmaker[Session] | None:
    resolved_url = database_url or get_database_url()
    return _session_factory(resolved_url) if resolved_url else None
