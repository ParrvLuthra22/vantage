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
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from vantage_api.database import Base


def _utcnow() -> datetime:
    """Default for `created_at` columns below.

    A callable (not `datetime.utcnow`) so it evaluates per-insert rather than
    once at import time, and timezone-aware so it matches `DateTime(timezone=True)`
    — `datetime.utcnow()` returns a naive datetime that Postgres would silently
    treat as local time.
    """
    return datetime.now(timezone.utc)


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


class EvalSuite(Base):
    """A named collection of eval scenarios, versioned as a group (e.g. "orchestrator_v1")."""

    __tablename__ = "eval_suites"

    suite_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    agent_target: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    scenarios: Mapped[list["EvalScenario"]] = relationship(
        back_populates="suite",
        cascade="all, delete-orphan",
    )
    runs: Mapped[list["EvalRun"]] = relationship(
        back_populates="suite",
        cascade="all, delete-orphan",
    )


class EvalScenario(Base):
    """A single test case within a suite: one input, one expected outcome, one rubric."""

    __tablename__ = "eval_scenarios"

    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    suite_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_suites.suite_id", ondelete="CASCADE")
    )
    # Separate from `scenario_id` on purpose: the YAML files key scenarios by a
    # human-readable id (e.g. "clear_001"), while the DB uses UUIDs internally.
    # That indirection lets a scenario's YAML file be renamed or restructured
    # without breaking the FK on historical `eval_results` rows that point at it.
    external_id: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32))
    complexity: Mapped[str] = mapped_column(String(32))
    input: Mapped[str] = mapped_column(Text)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    expected: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    rubric: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    suite: Mapped["EvalSuite"] = relationship(back_populates="scenarios")
    results: Mapped[list["EvalResult"]] = relationship(
        back_populates="scenario",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "suite_id", "external_id", name="uq_eval_scenarios_suite_external_id"
        ),
    )


class EvalRun(Base):
    """One execution of a suite's scenarios against a specific agent version."""

    __tablename__ = "eval_runs"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    suite_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_suites.suite_id", ondelete="CASCADE")
    )
    agent_version: Mapped[str] = mapped_column(String(64))
    # Carried as metadata on the run, not hardcoded as a constant, so that runs
    # of the same suite against different judges (GPT-4o-mini today, a
    # fine-tuned in-house judge later) stay comparable rows in the same table
    # instead of requiring a schema change when the judge changes.
    judge_model: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16))
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    suite: Mapped["EvalSuite"] = relationship(back_populates="runs")
    results: Mapped[list["EvalResult"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # "Recent runs of this suite": DESC so ORDER BY started_at DESC needs no
        # sort step, matching the ix_traces_project_start_desc pattern above.
        Index(
            "ix_eval_runs_suite_started_desc",
            "suite_id",
            text("started_at DESC"),
        ),
    )


class EvalResult(Base):
    """The outcome of one scenario within one run: scores, timing, pass/fail."""

    __tablename__ = "eval_results"

    result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("eval_runs.run_id", ondelete="CASCADE"),
        index=True,
    )
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_scenarios.scenario_id", ondelete="CASCADE")
    )
    # No FK: this points at `traces.trace_id` in the observability schema, but
    # eval and trace retention are independent concerns. A trace may be purged
    # by a retention job long after its eval result is still worth keeping, and
    # some invocations never emit a trace at all — a constraint here would force
    # eval writes to fail or race against trace ingestion for no benefit.
    trace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    deterministic_scores: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    llm_judge_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_judge_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Stored verbatim (not just the parsed score) so a judge score that looks
    # wrong can be debugged against exactly what the model said, rather than
    # against a lossy parse of it.
    llm_judge_raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_within_budget: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    run: Mapped["EvalRun"] = relationship(back_populates="results")
    scenario: Mapped["EvalScenario"] = relationship(back_populates="results")

    __table_args__ = (
        # Pass/fail filtering within a run, e.g. "show me the failures".
        Index("ix_eval_results_run_passed", "run_id", "passed"),
    )
