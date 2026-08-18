"""Pydantic v2 schemas: the wire contract for the Vantage API.

These are deliberately kept separate from the SQLAlchemy models in `models.py`,
and ORM instances are never returned from a route directly.

* Why In/Out are separate types. The ORM layer is free to churn — adding a
  column, renaming an internal field, denormalizing something for performance —
  without any of that silently leaking into responses and breaking API
  consumers. Serializing ORM objects directly makes every schema migration a
  potential undeclared breaking change, and the failure is invisible at review
  time because nothing in the diff mentions the API. An explicit Out schema
  means a field only reaches a client when someone writes it down here. The same
  boundary protects the write path: an In schema pins exactly which fields a
  client may set, so a new ORM column can never become accidentally
  client-writable through mass assignment.

* Why the batch cap is 500 spans. Postgres has an effective ceiling on statement
  size and on bind parameters per statement, and ingestion writes a batch as a
  single multi-row INSERT. At roughly a dozen columns per span, 500 rows keeps
  the parameter count and the generated statement comfortably inside those
  limits, and keeps a typical JSON payload under about 1MB — small enough to
  parse and validate without a memory spike per request. The SDK exporter is
  responsible for chunking anything larger into multiple batches.
"""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Max spans accepted in one ingest batch — see module docstring.
MAX_BATCH_SPANS = 500


class SpanIn(BaseModel):
    """A single span exactly as posted by the SDK exporter."""

    span_id: UUID
    trace_id: UUID
    parent_span_id: Optional[UUID] = None
    name: str = Field(min_length=1, max_length=256)
    start_time: datetime
    end_time: Optional[datetime] = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(default="ok", pattern="^(ok|error)$")
    error_message: Optional[str] = None
    model: Optional[str] = None
    input_tokens: Optional[int] = Field(default=None, ge=0)
    output_tokens: Optional[int] = Field(default=None, ge=0)
    cost_usd: Optional[float] = Field(default=None, ge=0.0)


class SpanBatch(BaseModel):
    """One export call from the SDK: a project plus the spans it collected."""

    project: str = Field(min_length=1, max_length=64)
    spans: list[SpanIn] = Field(min_length=1, max_length=MAX_BATCH_SPANS)


class SpanOut(SpanIn):
    """A span on the way out. Same shape as SpanIn, populated from the ORM."""

    model_config = ConfigDict(from_attributes=True)


class TraceOut(BaseModel):
    """Trace summary — the shape returned by list endpoints."""

    model_config = ConfigDict(from_attributes=True)

    trace_id: UUID
    project: str
    root_span_id: Optional[UUID]
    start_time: datetime
    end_time: Optional[datetime]
    status: str
    total_cost_usd: float
    total_tokens: int


class TraceDetail(TraceOut):
    """Trace summary plus its full span list — the single-trace endpoint."""

    spans: list[SpanOut]
