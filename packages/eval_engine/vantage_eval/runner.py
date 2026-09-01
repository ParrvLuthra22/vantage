"""Suite runner: iterate scenarios, invoke agent, score, aggregate."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

from vantage_eval.agents.base import AgentAdapter
from vantage_eval.loader import Suite
from vantage_eval.models import ScenarioResult, SuiteRun, SuiteRunSummary
from vantage_eval.scorers.deterministic import DeterministicScorer
from vantage_eval.scorers.llm_judge import LLMJudgeScorer


def run_suite(
    suite: Suite,
    adapter: AgentAdapter,
    agent_version: str,
    judge: Optional[LLMJudgeScorer] = None,
    min_llm_pass: float = 4.0,
    verbose: bool = False,
) -> SuiteRun:
    """Execute every scenario in a suite. Returns the completed SuiteRun."""
    console = Console()
    started = datetime.now(timezone.utc)
    started_mono = time.monotonic()

    det_scorer = DeterministicScorer()

    run = SuiteRun(
        suite_name=suite.name,
        agent_version=agent_version,
        judge_model=judge.model if judge else "none",
        started_at=started,
    )

    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("[cyan]{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"Running {suite.name}", total=len(suite.scenarios))

        for scenario in suite.scenarios:
            result = _run_one(scenario, adapter, det_scorer, judge)
            result.passed = result.aggregate_pass(min_llm_pass)
            run.results.append(result)
            progress.advance(task)

            if verbose:
                mark = "✓" if result.passed else "✗"
                console.print(
                    f"  {mark} {scenario.external_id} — {scenario.category}",
                    style="green" if result.passed else "red",
                )

    finished = datetime.now(timezone.utc)
    duration = time.monotonic() - started_mono

    run.finished_at = finished
    run.summary = _summarize(run.results, duration, judge.total_cost_usd if judge else 0.0)
    return run


def _run_one(
    scenario,
    adapter: AgentAdapter,
    det_scorer: DeterministicScorer,
    judge: Optional[LLMJudgeScorer],
) -> ScenarioResult:
    try:
        output = adapter.invoke(scenario.input, scenario.context)
    except Exception as e:
        # Adapter crashed — record and continue with the suite
        from vantage_eval.models import AgentOutput

        output = AgentOutput(
            routed_agent="ADAPTER_ERROR",
            latency_ms=0,
            reasoning=f"{type(e).__name__}: {e}",
        )

    result = ScenarioResult(external_id=scenario.external_id, output=output)

    if scenario.rubric.latency_budget_ms is not None:
        result.latency_within_budget = output.latency_ms <= scenario.rubric.latency_budget_ms

    det_scorer.score(scenario, output, result)
    if judge is not None:
        judge.score(scenario, output, result)

    return result


def _summarize(
    results: list[ScenarioResult],
    duration_s: float,
    judge_cost: float,
) -> SuiteRunSummary:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed
    pass_rate = passed / total if total else 0.0

    llm_scores = [r.llm_judge_score for r in results if r.llm_judge_score is not None]
    avg_llm = sum(llm_scores) / len(llm_scores) if llm_scores else None

    # Category isn't on ScenarioResult; by_category stays empty until P29 carries
    # it forward (or looks it up) when we start persisting to Postgres.
    return SuiteRunSummary(
        total=total,
        passed=passed,
        failed=failed,
        pass_rate=pass_rate,
        avg_llm_score=avg_llm,
        total_judge_cost_usd=judge_cost,
        duration_seconds=duration_s,
    )
