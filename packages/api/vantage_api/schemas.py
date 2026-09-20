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
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

#: Mirrors vantage_eval.regression.detector.ChangeType exactly. Duplicated
#: (not imported) so this wire-contract module stays free of any dependency
#: on vantage_eval's internals — see the module docstring's rationale for
#: In/Out schemas being deliberately decoupled from what produces them.
ChangeType = Literal["regression", "improvement", "stable_pass", "stable_fail", "new", "removed"]

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


class EvalScenarioSummary(BaseModel):
    """Just enough about a scenario to label a result row — not the full
    EvalScenario (input/context/expected/rubric), which the run-detail view
    doesn't need per-result."""

    model_config = ConfigDict(from_attributes=True)

    external_id: str
    category: str
    known_failing: bool
    known_failing_reason: Optional[str] = None


class EvalResultOut(BaseModel):
    """One scenario's outcome within a run, with its scenario's identity and
    known_failing status joined in — so a client can separate known-failing
    rows from real failures without a second round trip."""

    model_config = ConfigDict(from_attributes=True)

    result_id: UUID
    scenario: EvalScenarioSummary
    trace_id: Optional[UUID]
    deterministic_scores: dict[str, Any]
    llm_judge_score: Optional[float]
    llm_judge_reasoning: Optional[str]
    latency_ms: Optional[int]
    latency_within_budget: Optional[bool]
    passed: bool


class EvalRunOut(BaseModel):
    """Run summary — the shape returned by the list endpoint."""

    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    suite_id: UUID
    agent_version: str
    judge_model: str
    started_at: datetime
    finished_at: Optional[datetime]
    status: str
    is_baseline: bool
    summary: dict[str, Any]


class EvalRunDetail(EvalRunOut):
    """Run summary plus every scenario result — the single-run endpoint."""

    results: list[EvalResultOut]


class ChangeOut(BaseModel):
    """One scenario's outcome change between a baseline and a current run.

    Not `from_attributes`: built explicitly field-by-field from a
    `vantage_eval.regression.detector.ScenarioChange` dataclass in
    eval_routes.py, same as every other Out schema in this module — a
    dataclass's shape is free to change without that silently becoming a
    wire contract change here too.
    """

    external_id: str
    change_type: ChangeType
    baseline_passed: Optional[bool]
    current_passed: Optional[bool]
    baseline_llm_score: Optional[float]
    current_llm_score: Optional[float]


class RegressionReportOut(BaseModel):
    """A baseline-vs-current comparison — the shape returned by
    GET /evals/runs/{current_id}/vs/{baseline_id}."""

    baseline_run_id: str
    current_run_id: str
    baseline_pass_rate: float
    current_pass_rate: float
    pass_rate_delta: float
    changes: list[ChangeOut]
