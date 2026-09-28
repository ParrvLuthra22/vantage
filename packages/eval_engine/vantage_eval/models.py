"""Core data models for the eval engine.

These are the runtime types the engine passes around. The wire/storage types
live in packages/api/vantage_api/schemas.py separately — do not conflate.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

ScenarioCategory = Literal["clear", "ambiguous", "adversarial", "out_of_scope", "context_dependent"]
ScenarioComplexity = Literal["single_step", "multi_step"]


class Rubric(BaseModel):
    """How a scenario should be scored."""
    hard_checks: list[str] = Field(default_factory=list)
    """
    Deterministic check names. Each name resolves to a function in the
    DeterministicScorer's registry. Examples:
      - "routed_agent_matches"
      - "extracted_entities.subject_present"
      - "refused"
      - "not_refused"
    """

    llm_judge_prompt: Optional[str] = None
    """
    Jinja2 template rendered against context {input, context, expected, actual}.
    If None, no LLM judge is run for this scenario.
    """

    latency_budget_ms: Optional[int] = None
    """If set, latency > budget marks the scenario failed regardless of judge."""


class Scenario(BaseModel):
    """A single test case."""
    external_id: str
    category: ScenarioCategory
    complexity: ScenarioComplexity
    input: str
    context: dict[str, Any] = Field(default_factory=dict)
    expected: dict[str, Any] = Field(default_factory=dict)
    rubric: Rubric
    notes: Optional[str] = None

    known_failing: bool = False
    """True for a scenario with a confirmed, filed Vesper bug (Bucket A).

    A known-failing scenario keeps running and scoring normally, but is
    excluded from the suite's pass-rate denominator (see SuiteRunSummary) so a
    documented, tracked gap doesn't gate merges — while still surfacing in
    reports so a regression or a fix is visible.
    """

    known_failing_reason: Optional[str] = None
    """Required in practice when known_failing is True: a short description
    plus the tracking issue URL, e.g. "routes to notes_agent instead of
    asking for clarification — https://github.com/.../issues/42"."""


class AgentOutput(BaseModel):
    """What an AgentAdapter returns after invocation."""
    routed_agent: str
    """Which agent the orchestrator picked. Use sentinel "REFUSE" for out-of-scope refusals."""

    extracted_entities: dict[str, Any] = Field(default_factory=dict)
    reasoning: Optional[str] = None
    latency_ms: int
    trace_id: Optional[UUID] = None
    raw_output: Optional[str] = None

    tool_sequence: list[str] = Field(default_factory=list)
    """Names of every tool the agent invoked this turn, in call order; empty when
    it answered in text alone. `routed_agent` stays just the FIRST of these (or a
    sentinel), for rubric hard_checks that reference it — anything about what
    happened AFTER the first call has to read this instead."""

    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    """Same order as `tool_sequence`, each entry `{"name": ..., "arguments": {...}}`.
    Carries the arguments a name alone can't (e.g. set_volume's level), which a
    judge needs to grade "did it pick a sensible value"."""

    final_reply: Optional[str] = None
    """The agent's user-facing reply text, if any. Unlike `reasoning` (only set
    on an aborted turn, so None for almost every run), this is populated whenever
    the agent said something — so a judge can see what the user was actually told."""


class DeterministicResult(BaseModel):
    check_name: str
    passed: bool
    detail: Optional[str] = None


class ScenarioResult(BaseModel):
    """The outcome of running one scenario."""
    scenario_id: UUID = Field(default_factory=uuid4)
    external_id: str
    output: AgentOutput
    deterministic_results: list[DeterministicResult] = Field(default_factory=list)
    llm_judge_score: Optional[float] = None
    llm_judge_reasoning: Optional[str] = None
    llm_judge_raw_response: Optional[str] = None
    latency_within_budget: Optional[bool] = None
    passed: bool = False

    known_failing: bool = False
    """Copied from Scenario.known_failing by the runner. Scored honestly by
    aggregate_pass like any other scenario — this only changes how the
    *suite* summary and reports bucket the result, not whether it "really"
    passed."""

    judge_error: bool = False
    """Set by LLMJudgeScorer when the JUDGE's own API call failed (rate limit,
    timeout, connection error) or returned an empty response — not a real
    judgment of the agent under test. Bucketed out of the pass-rate
    denominator the same way known_failing is (see SuiteRunSummary), but for
    the opposite reason: known_failing is a documented product bug that keeps
    failing; judge_error is pipeline noise that says nothing about the agent.
    llm_judge_score stays None in this case rather than a clamped 0.0, so it
    can't be mistaken for a real score."""

    infra_error: bool = False
    """Set by runner._run_one when the AGENT's own output is an infrastructure
    sentinel (routed_agent == "PLANNER_FAILURE" or "ADAPTER_ERROR") — the
    adapter/agent-under-test's own tooling broke this turn, not a routing
    decision to grade. The opposite end of the pipeline from judge_error: that
    one is the judge's infrastructure failing, this one is the agent's. The
    LLM judge is never called for a scenario flagged this way (no quality
    score exists to launder), and it's excluded from the pass-rate
    denominator the same way known_failing/judge_error are. Deterministic
    checks still run — a hard-check "expected X, got PLANNER_FAILURE" detail
    is legitimate diagnostic signal, it just doesn't count toward pass/fail."""

    def aggregate_pass(self, min_llm_score: float = 4.0) -> bool:
        """Compute overall pass/fail from component scores."""
        if self.judge_error or self.infra_error:
            return False  # no verdict was reached; excluded from the denominator anyway
        if any(not r.passed for r in self.deterministic_results):
            return False
        if self.latency_within_budget is False:
            return False
        if self.llm_judge_score is not None and self.llm_judge_score < min_llm_score:
            return False
        return True


class SuiteRunSummary(BaseModel):
    """`total`/`passed`/`failed`/`pass_rate` are the EFFECTIVE figures — i.e.
    computed only over non-known-failing scenarios, so a documented Vesper
    bug doesn't drag down the number a baseline gates on. `total_scenarios`
    is every scenario in the suite, known-failing included."""

    total: int
    passed: int
    failed: int
    pass_rate: float
    total_scenarios: int
    known_failing: int = 0
    judge_error: int = 0
    """Count of scenarios excluded from total/passed/failed because the
    judge's own API call failed or returned nothing — see
    ScenarioResult.judge_error. Distinct from known_failing: this is
    infrastructure noise, not a documented agent gap."""
    infra_error: int = 0
    """Count of scenarios excluded from total/passed/failed because the
    AGENT's own output was an infrastructure sentinel (PLANNER_FAILURE /
    ADAPTER_ERROR) — see ScenarioResult.infra_error. The agent-side twin of
    judge_error: that's the judge's infrastructure failing, this is the
    agent-under-test's."""
    avg_llm_score: Optional[float] = None
    total_judge_cost_usd: float = 0.0
    duration_seconds: float = 0.0

    by_category: dict[str, dict[str, int]] = Field(default_factory=dict)
    """Category -> {total, passed, failed}"""


class SuiteRun(BaseModel):
    run_id: UUID = Field(default_factory=uuid4)
    suite_name: str
    agent_version: str
    judge_model: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    results: list[ScenarioResult] = Field(default_factory=list)
    summary: Optional[SuiteRunSummary] = None
