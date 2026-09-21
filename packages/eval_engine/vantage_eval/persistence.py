"""Persist eval suites/scenarios/runs/results to Postgres.

Reuses vantage_api's ORM models (EvalSuite/EvalScenario/EvalRun/EvalResult)
rather than duplicating the schema — the tables are the single source of
truth, owned by packages/api's Alembic migrations; this module only ever
reads/writes through vantage_api.models, never redefines a column.

Builds its own engine from `DATABASE_URL` directly (not via
vantage_api.config.settings, which resolves its own `.env` relative to CWD)
so behavior doesn't depend on where `vantage` is invoked from.

Persistence is best-effort: every public entry point here is wrapped so a
DB failure (Postgres down, bad DATABASE_URL, ...) logs a warning and lets the
eval run finish and print its scorecard rather than crashing it — mirrors how
Tracer/VantageClient degrade gracefully elsewhere in this codebase.
"""
from __future__ import annotations

import logging
import os
from typing import Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from vantage_api.models import EvalResult, EvalRun, EvalScenario, EvalSuite

from vantage_eval.loader import Suite
from vantage_eval.models import Scenario, SuiteRun

logger = logging.getLogger(__name__)

DEFAULT_DATABASE_URL = "postgresql+asyncpg://vantage:vantage@localhost:5432/vantage"


def _resolve_url(raw_url: str):
    """Same libpq-`sslmode` -> asyncpg-`ssl` rename as vantage_api.database's
    private helper of the same name — duplicated rather than imported since
    it's an underscore-private implementation detail of another package, not
    a shared public export."""
    url = make_url(raw_url)
    query = dict(url.query)
    sslmode = query.pop("sslmode", None)
    if sslmode is not None and "ssl" not in query:
        query["ssl"] = sslmode
    return url.set(query=query)


def get_engine(url: Optional[str] = None):
    """Build an async engine for `url`, or `DATABASE_URL`/the local default
    if not given. Public: also used by `vantage eval compare` to open its
    own session against the same DB persist_run just wrote to."""
    resolved = url or os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    return create_async_engine(_resolve_url(resolved), pool_pre_ping=True)


async def persist_run(
    suite: Suite,
    run: SuiteRun,
    mark_baseline: bool = False,
) -> Optional[UUID]:
    """Upsert the suite/scenarios, insert this run + its results, optionally
    mark it as the suite's baseline. Returns the persisted run_id, or None if
    persistence failed (a warning is logged; the caller should keep going).
    """
    engine = get_engine()
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with session_factory() as session:
            async with session.begin():
                suite_row = await _upsert_suite(session, suite)
                scenario_rows = await _upsert_scenarios(session, suite_row, suite.scenarios)
                run_row = _build_run(suite_row, run)
                session.add(run_row)
                await session.flush()  # assigns run_row.run_id for the FK below

                for result in run.results:
                    scenario_row = scenario_rows.get(result.external_id)
                    if scenario_row is None:
                        logger.warning(
                            "persist_run: no EvalScenario for external_id=%r, skipping result",
                            result.external_id,
                        )
                        continue
                    session.add(_build_result(run_row, scenario_row, result))

                if mark_baseline:
                    await _mark_baseline(session, suite_row.suite_id, run_row.run_id)

            return run_row.run_id
    except Exception:
        logger.warning("persist_run: failed to persist eval run to Postgres", exc_info=True)
        return None
    finally:
        await engine.dispose()


async def _upsert_suite(session: AsyncSession, suite: Suite) -> EvalSuite:
    existing = (
        await session.execute(select(EvalSuite).where(EvalSuite.name == suite.name))
    ).scalar_one_or_none()
    if existing is not None:
        existing.description = suite.description
        existing.agent_target = suite.agent_target
        return existing

    row = EvalSuite(name=suite.name, description=suite.description, agent_target=suite.agent_target)
    session.add(row)
    await session.flush()
    return row


async def _upsert_scenarios(
    session: AsyncSession, suite_row: EvalSuite, scenarios: list[Scenario]
) -> dict[str, EvalScenario]:
    existing_rows = (
        await session.execute(
            select(EvalScenario).where(EvalScenario.suite_id == suite_row.suite_id)
        )
    ).scalars().all()
    by_external_id = {row.external_id: row for row in existing_rows}

    result: dict[str, EvalScenario] = {}
    for scenario in scenarios:
        row = by_external_id.get(scenario.external_id)
        if row is None:
            row = EvalScenario(suite_id=suite_row.suite_id, external_id=scenario.external_id)
            session.add(row)

        # Update every run so a YAML edit (rubric loosened, known_failing set,
        # ...) propagates to Postgres without a separate migration/backfill.
        row.category = scenario.category
        row.complexity = scenario.complexity
        row.input = scenario.input
        row.context = scenario.context
        row.expected = scenario.expected
        row.rubric = scenario.rubric.model_dump()
        row.notes = scenario.notes
        row.known_failing = scenario.known_failing
        row.known_failing_reason = scenario.known_failing_reason

        result[scenario.external_id] = row

    await session.flush()
    return result


def _build_run(suite_row: EvalSuite, run: SuiteRun) -> EvalRun:
    return EvalRun(
        suite_id=suite_row.suite_id,
        agent_version=run.agent_version,
        judge_model=run.judge_model,
        started_at=run.started_at,
        finished_at=run.finished_at,
        status="completed",
        summary=run.summary.model_dump() if run.summary else {},
        is_baseline=False,  # set separately by _mark_baseline, inside the same transaction
    )


def _build_result(run_row: EvalRun, scenario_row: EvalScenario, result) -> EvalResult:
    return EvalResult(
        run_id=run_row.run_id,
        scenario_id=scenario_row.scenario_id,
        trace_id=result.output.trace_id,
        deterministic_scores={d.check_name: d.model_dump() for d in result.deterministic_results},
        llm_judge_score=result.llm_judge_score,
        llm_judge_reasoning=result.llm_judge_reasoning,
        llm_judge_raw_response=result.llm_judge_raw_response,
        latency_ms=result.output.latency_ms,
        latency_within_budget=result.latency_within_budget,
        passed=result.passed,
    )


async def _mark_baseline(session: AsyncSession, suite_id: UUID, run_id: UUID) -> None:
    """Unset any previous baseline for this suite, then set this run's flag —
    one transaction, so a crash between the two statements can never leave a
    suite with zero or two baselines."""
    await session.execute(
        update(EvalRun)
        .where(EvalRun.suite_id == suite_id, EvalRun.is_baseline.is_(True))
        .values(is_baseline=False)
    )
    await session.execute(
        update(EvalRun).where(EvalRun.run_id == run_id).values(is_baseline=True)
    )
