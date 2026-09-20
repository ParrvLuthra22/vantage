"""Load SuiteRuns from Postgres for regression comparison."""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from vantage_api.models import EvalResult, EvalRun, EvalSuite

from vantage_eval.models import (
    AgentOutput,
    DeterministicResult,
    ScenarioResult,
    SuiteRun,
    SuiteRunSummary,
)


async def load_baseline_run(session: AsyncSession, suite_name: str) -> Optional[EvalRun]:
    """Load the current baseline for a suite, or None if none marked."""
    suite = (
        await session.execute(select(EvalSuite).where(EvalSuite.name == suite_name))
    ).scalar_one_or_none()
    if not suite:
        return None

    result = await session.execute(
        select(EvalRun)
        .where(EvalRun.suite_id == suite.suite_id, EvalRun.is_baseline.is_(True))
        .options(selectinload(EvalRun.results).selectinload(EvalResult.scenario))
    )
    return result.scalar_one_or_none()


async def load_run(session: AsyncSession, run_id: UUID) -> Optional[EvalRun]:
    result = await session.execute(
        select(EvalRun)
        .where(EvalRun.run_id == run_id)
        .options(selectinload(EvalRun.results).selectinload(EvalResult.scenario))
    )
    return result.scalar_one_or_none()


def db_run_to_suite_run(db_run: EvalRun) -> SuiteRun:
    """Adapt an ORM EvalRun into the runtime SuiteRun type used by the detector.

    This is a reconstruction, not a faithful replay: `AgentOutput.routed_agent`
    isn't persisted anywhere in `eval_results` (only its downstream scores —
    deterministic_scores/llm_judge_score/passed — are), so there is no real
    value to put there. The detector only ever reads `ScenarioResult.passed`,
    `.llm_judge_score`, and `.deterministic_results`, never `.output` itself,
    so the sentinel below is inert as far as regression detection is
    concerned — it exists solely to satisfy AgentOutput's required field.
    """
    results = [
        ScenarioResult(
            external_id=r.scenario.external_id,
            output=AgentOutput(routed_agent="RECONSTRUCTED", latency_ms=r.latency_ms or 0),
            deterministic_results=[
                DeterministicResult(check_name=name, passed=v["passed"], detail=v.get("detail"))
                for name, v in (r.deterministic_scores or {}).items()
            ],
            llm_judge_score=r.llm_judge_score,
            llm_judge_reasoning=r.llm_judge_reasoning,
            latency_within_budget=r.latency_within_budget,
            passed=r.passed,
        )
        for r in db_run.results
    ]
    summary: dict[str, Any] | None = db_run.summary or None
    return SuiteRun(
        run_id=db_run.run_id,
        suite_name="reconstructed",
        agent_version=db_run.agent_version,
        judge_model=db_run.judge_model,
        started_at=db_run.started_at,
        finished_at=db_run.finished_at,
        results=results,
        summary=SuiteRunSummary(**summary) if summary else None,
    )
