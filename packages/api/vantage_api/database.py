"""Async SQLAlchemy engine, session factory, and the FastAPI session dependency."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from vantage_api.config import settings


class Base(DeclarativeBase):
    """Declarative base every ORM model inherits from.

    Alembic autogenerate reads `Base.metadata`, so models must be imported
    before migrations run or their tables will be silently omitted.
    """


engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
    # pool_pre_ping is critical for Neon compatibility. Neon closes idle
    # connections on its side, which leaves dead sockets in our pool. Pre-ping
    # issues a cheap liveness check when a connection is checked out and
    # transparently replaces it if it's stale, instead of surfacing a
    # "connection was closed" error in the middle of a request.
    pool_pre_ping=True,
)

# expire_on_commit=False prevents implicit re-fetch queries after commit. With
# the default (True), touching any attribute of a committed object triggers a
# lazy refresh; in async code that I/O happens outside an await and raises
# MissingGreenlet. Keeping attributes loaded post-commit avoids that entirely.
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield one database session per request, closing it when the request ends.

    Used as a FastAPI dependency: `db: AsyncSession = Depends(get_db)`.
    """
    async with SessionLocal() as session:
        yield session
