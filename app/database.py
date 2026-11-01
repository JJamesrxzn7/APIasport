from functools import lru_cache
from collections.abc import Generator

from fastapi import HTTPException, status
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings


@lru_cache
def get_engine() -> Engine:
    if settings.database_url is None:
        raise RuntimeError("DATABASE_URL is not configured")

    database_url = make_url(settings.database_url.get_secret_value())
    if database_url.drivername in {"postgres", "postgresql"}:
        database_url = database_url.set(drivername="postgresql+psycopg")
    elif database_url.drivername != "postgresql+psycopg":
        raise ValueError("DATABASE_URL must use PostgreSQL with the psycopg driver")

    return create_engine(database_url, pool_pre_ping=True)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(
        bind=get_engine(),
        autoflush=False,
        expire_on_commit=False,
    )


def get_db() -> Generator[Session, None, None]:
    if settings.database_url is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not configured",
        )

    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()