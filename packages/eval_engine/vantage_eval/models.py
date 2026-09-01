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


class AgentOutput(BaseModel):
    """What an AgentAdapter returns after invocation."""
    routed_agent: str
    """Which agent the orchestrator picked. Use sentinel "REFUSE" for out-of-scope refusals."""

    extracted_entities: dict[str, Any] = Field(default_factory=dict)
    reasoning: Optional[str] = None
    latency_ms: int
    trace_id: Optional[UUID] = None
    raw_output: Optional[str] = None


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

    def aggregate_pass(self, min_llm_score: float = 4.0) -> bool:
        """Compute overall pass/fail from component scores."""
        if any(not r.passed for r in self.deterministic_results):
            return False
        if self.latency_within_budget is False:
            return False
        if self.llm_judge_score is not None and self.llm_judge_score < min_llm_score:
            return False
        return True


class SuiteRunSummary(BaseModel):
    total: int
    passed: int
    failed: int
    pass_rate: float
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
