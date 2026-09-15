"""HTTP routes for eval run retrieval.

Writes happen from vantage_eval's CLI directly against Postgres (see
vantage_eval/persistence.py), not through this API — this router only ever
reads, for the dashboard's /evals pages.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from vantage_api.database import get_db
from vantage_api.models import EvalResult, EvalRun, EvalSuite
from vantage_api.routes import verify_api_key
from vantage_api.schemas import EvalRunDetail, EvalRunOut

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
