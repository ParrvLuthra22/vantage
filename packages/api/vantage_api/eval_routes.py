"""HTTP routes for eval run retrieval.

Writes happen from vantage_eval's CLI directly against Postgres (see
vantage_eval/persistence.py), not through this API — this router only ever
reads, for the dashboard's /evals pages.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from vantage_api.database import get_db
from vantage_api.models import EvalResult, EvalRun, EvalSuite
from vantage_api.routes import verify_api_key
from vantage_api.schemas import ChangeOut, EvalRunDetail, EvalRunOut, RegressionReportOut

router = APIRouter(prefix="/evals", tags=["evals"])


@router.get("/runs", response_model=list[EvalRunOut], dependencies=[Depends(verify_api_key)])
async def list_eval_runs(
    suite: str = "orchestrator_v1",
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[EvalRun]:
    """List the most recent runs of a suite, newest first."""
    suite_row = (
        await db.execute(select(EvalSuite).where(EvalSuite.name == suite))
    ).scalar_one_or_none()
    if suite_row is None:
        return []

    result = await db.execute(
        select(EvalRun)
        .where(EvalRun.suite_id == suite_row.suite_id)
        .order_by(EvalRun.started_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


# selectinload avoids N+1: one query for the run, one for its results, one for
# those results' scenarios — matching the pattern get_trace() uses for spans.
@router.get(
    "/runs/{run_id}", response_model=EvalRunDetail, dependencies=[Depends(verify_api_key)]
)
async def get_eval_run(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> EvalRun:
    """Fetch a single eval run with every scenario result."""
    result = await db.execute(
        select(EvalRun)
        .where(EvalRun.run_id == run_id)
        .options(selectinload(EvalRun.results).selectinload(EvalResult.scenario))
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Eval run not found"
        )
    return run


# Reuses vantage_eval's own regression detector rather than reimplementing
# it here — the same algorithm the CLI's `vantage eval compare` runs (see
# vantage_eval/regression/detector.py). The import is local to the request
# handler, and vantage-eval is deliberately NOT added to this package's
# pyproject.toml dependencies: vantage_eval itself depends on vantage_api
# (for these same ORM models) AND on `vesper @ git+...`, so declaring it
# here would mean *any* install of vantage-api transitively needs to clone
# Vesper's repo just to serve traces. This works today because both
# packages are always installed together from this monorepo in dev/CI;
# splitting detect_regressions out of eval_engine (it only needs
# vantage_eval.models, not vesper) would let this be a real, declared
# dependency if vantage_api is ever deployed standalone.
@router.get(
    "/runs/{current_id}/vs/{baseline_id}",
    response_model=RegressionReportOut,
    dependencies=[Depends(verify_api_key)],
)
async def compare_runs(
    current_id: uuid.UUID,
    baseline_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> RegressionReportOut:
    """Compare two runs and classify every scenario's outcome change."""
    from vantage_eval.regression.detector import detect_regressions
    from vantage_eval.regression.loader import db_run_to_suite_run, load_run

    current_db = await load_run(db, current_id)
    baseline_db = await load_run(db, baseline_id)
    if current_db is None or baseline_db is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Eval run not found")

    report = detect_regressions(db_run_to_suite_run(baseline_db), db_run_to_suite_run(current_db))

    return RegressionReportOut(
        baseline_run_id=report.baseline_run_id,
        current_run_id=report.current_run_id,
        baseline_pass_rate=report.baseline_pass_rate,
        current_pass_rate=report.current_pass_rate,
        pass_rate_delta=report.pass_rate_delta,
        changes=[
            ChangeOut(
                external_id=c.external_id,
                change_type=c.change_type,
                baseline_passed=c.baseline_passed,
                current_passed=c.current_passed,
                baseline_llm_score=c.baseline_llm_score,
                current_llm_score=c.current_llm_score,
            )
            for c in report.changes
        ],
    )


@router.post(
    "/runs/{run_id}/set-baseline",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_api_key)],
)
async def set_baseline(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    """Mark a run as the current baseline for its suite, unmarking any
    previous one. Same unset-then-set-in-one-transaction shape as
    vantage_eval/persistence.py's `_mark_baseline` (used by `vantage eval
    run --mark-baseline`) -- duplicated rather than imported since it's
    three lines of plain SQLAlchemy with no vantage_eval-specific logic,
    unlike compare_runs above, and `_mark_baseline` is a private helper of
    that module."""
    run = (
        await db.execute(select(EvalRun).where(EvalRun.run_id == run_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Eval run not found")

    await db.execute(
        update(EvalRun).where(EvalRun.suite_id == run.suite_id).values(is_baseline=False)
    )
    run.is_baseline = True
    await db.commit()
