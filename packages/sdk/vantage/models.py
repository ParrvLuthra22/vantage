"""Wire format for spans sent to the Vantage backend.

This mirrors the backend's `SpanIn` schema field for field. The duplication is
deliberate: the SDK ships to users' machines on its own release cycle and must
not import from the API package, so the contract is written down on both sides.
Validating here means a malformed span fails at the call site, with a traceback
pointing at the instrumented code, instead of surfacing as an opaque 422 from a
background flush thread long after the fact.

Any change to this model is a wire-protocol change and has to land on the
backend's `SpanIn` too.
"""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class SpanCreate(BaseModel):
    """A single span as posted to `POST /traces/spans`."""

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
