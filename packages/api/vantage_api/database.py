"""Async SQLAlchemy engine, session factory, and the FastAPI session dependency."""

from collections.abc import AsyncGenerator

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from vantage_api.config import settings

# --- Where the database lives ------------------------------------------------
#
# Development runs Postgres in docker-compose; production runs Neon. That split
# is deliberate.
#
# Why keep a local Postgres at all, rather than pointing dev at Neon too?
#   - Iteration speed. Every query in a local container is sub-millisecond;
#     every query against Neon crosses the public internet. Over a test suite
#     that runs hundreds of statements, that difference is the whole feedback
#     loop.
#   - Blast radius. Test fixtures, backfill scripts, and `alembic downgrade
#     base` are all routine in development and all catastrophic against a
#     production database. Separate URLs mean a mistake costs nothing.
#   - Quota. The free tier meters compute time. Burning it on a test suite
#     leaves nothing for the thing users actually touch.
#
# Why Neon rather than RDS or Cloud SQL?
#   - It scales to zero. A portfolio project is idle most of the day, and Neon
#     suspends compute when nothing is connected — where RDS bills for an
#     instance that sits doing nothing.
#   - Branching. Neon can fork a database the way git forks a branch, so a PR
#     can run migrations against a copy of production data and throw it away
#     afterwards. That is genuinely hard to reproduce on RDS.
#   - The free tier is sufficient here, with no credit card and no VPC or
#     security-group setup to get a connection working.


def _resolve_url(raw_url: str) -> URL:
    """Translate libpq's `sslmode` into the `ssl` argument asyncpg understands.

    Neon's dashboard hands out URLs ending in `?sslmode=require`. That is libpq
    syntax: asyncpg has no `sslmode` parameter, and SQLAlchemy forwards unknown
    query args straight through as connect kwargs, so the URL fails at the first
    connection with `TypeError: connect() got an unexpected keyword argument
    'sslmode'` — not an SSL error, which makes it a confusing thing to debug.

    asyncpg's own `ssl` parameter accepts exactly the same vocabulary
    (`disable`, `allow`, `prefer`, `require`, `verify-ca`, `verify-full`), so
    the fix is a rename. Doing it here means a connection string pasted verbatim
    out of the Neon console just works.
    """
    url = make_url(raw_url)
    query = dict(url.query)
    sslmode = query.pop("sslmode", None)
    if sslmode is not None and "ssl" not in query:
        query["ssl"] = sslmode
    return url.set(query=query)


class Base(DeclarativeBase):
    """Declarative base every ORM model inherits from.

    Alembic autogenerate reads `Base.metadata`, so models must be imported
    before migrations run or their tables will be silently omitted.
    """


engine = create_async_engine(
    _resolve_url(settings.database_url),
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
