"""SQLAlchemy ORM models for the trace/span tree.

Design notes — why the schema looks like this:

* `attributes` / `tags` are JSONB with a GIN index rather than typed columns.
  Instrumentation attributes are a user-defined, arbitrary schema: every app
  attaches its own keys, so there is no column set we could pin down ahead of
  time. JSONB keeps writes schemaless while the GIN index still gives fast
  key-existence (`attributes ? 'user_id'`) and equality (`attributes @> '{...}'`)
  lookups, which is what trace filtering is made of. A plain B-tree can't index
  arbitrary keys this way.

* `total_cost_usd` / `total_tokens` are denormalized onto Trace. They are rolled
  up at write time so dashboard reads are O(1) per trace. Deriving them on read
  would mean a GROUP BY over `spans` on every single trace-list request — the
  hottest query in the product — and that cost grows with span count per trace.
  The tradeoff is that ingestion owns keeping these in sync.

* The `trace_id` foreign key uses ondelete="CASCADE" so deleting a trace cleans
  up its spans in one statement at the database level, with no orphan rows left
  behind if a delete arrives outside the ORM (admin SQL, retention job).

* `parent_span_id` deliberately carries NO foreign key constraint. Spans arrive
  from the SDK in whatever order the network delivers them, and a child can land
  before its parent. A self-referential FK would reject those out-of-order
  inserts and force either buffering or deferred constraints; keeping it a plain
  indexed column keeps ingestion simple and idempotent.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from vantage_api.database import Base


class Trace(Base):
    """One end-to-end agent run: the root of a tree of spans."""

    __tablename__ = "traces"

    trace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project: Mapped[str] = mapped_column(String(64), index=True)
    root_span_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="ok")
    tags: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    # Rolled up from child spans at write time — see module docstring.
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)

    spans: Mapped[list["Span"]] = relationship(
        back_populates="trace",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # Serves the primary dashboard query: filter by project, newest first.
        # Ordered DESC so the index can be walked forward for `ORDER BY
        # start_time DESC` without a sort step.
        Index(
            "ix_traces_project_start_desc",
            "project",
            text("start_time DESC"),
        ),
    )


class Span(Base):
    """A single unit of work inside a trace: an LLM call, tool call, or step."""

    __tablename__ = "spans"

    span_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    trace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("traces.trace_id", ondelete="CASCADE"),
        index=True,
    )
    # No FK constraint: spans can arrive before their parent. See module docstring.
    parent_span_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    name: Mapped[str] = mapped_column(String(256))
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # LLM-call metadata: null for spans that aren't model invocations.
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    trace: Mapped["Trace"] = relationship(back_populates="spans")

    __table_args__ = (
        # Waterfall rendering: fetch one trace's spans in chronological order.
        Index("ix_spans_trace_start", "trace_id", "start_time"),
        # Arbitrary-key containment/existence filtering over attributes.
        Index("ix_spans_attributes_gin", "attributes", postgresql_using="gin"),
    )
