"""FastAPI application entry point for the Vantage API."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from vantage_api.config import settings
from vantage_api.database import Base, engine
from vantage_api.routes import router as traces_router

API_VERSION = "0.1.0"


# create_all is fine for Week 1 ONLY. Week 2 introduces Alembic migrations.
# create_all just issues CREATE TABLE IF NOT EXISTS for tables it doesn't find —
# it does not handle schema *changes*. Adding a column, changing a type, or
# adding an index to an existing table is silently ignored, so once a table
# exists in an environment its shape is frozen and the code drifts away from the
# database with no error to warn you. Migrations replace this entirely.
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create missing tables on startup, dispose of the pool on shutdown."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(title="Vantage API", version=API_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(traces_router)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    """Liveness probe. Intentionally unauthenticated so orchestrators can poll it."""
    return {"status": "ok", "version": API_VERSION}
