"""FastAPI application entry point for the Vantage API."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from vantage_api.config import settings
from vantage_api.database import engine
from vantage_api.routes import router as traces_router

API_VERSION = "0.1.0"


# Schema is owned by Alembic, not by the app. `Base.metadata.create_all` used to
# run here; it only ever issued CREATE TABLE for tables it could not find and
# silently ignored every other kind of change — added columns, altered types,
# new indexes — so once a table existed its shape was frozen and the code drifted
# away from the database with no error to warn you.
#
# Run `alembic upgrade head` before starting the app. In production that belongs
# in the deploy step, ahead of any process that serves traffic.
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Dispose of the connection pool on shutdown."""
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
