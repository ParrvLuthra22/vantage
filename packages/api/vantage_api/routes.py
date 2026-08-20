"""HTTP routes for trace ingestion and retrieval."""

import secrets
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from vantage_api.config import settings
from vantage_api.database import get_db
from vantage_api.models import Span, Trace
from vantage_api.schemas import SpanBatch, TraceDetail, TraceOut

router = APIRouter(prefix="/traces", tags=["traces"])


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Coerce a datetime to timezone-aware UTC for safe comparison.

    Timestamps read back from a `timestamptz` column are always aware, but a
    client is free to post naive ones. Comparing the two raises TypeError, and
    the monotonic end_time check below does exactly that comparison — so a
    single naive timestamp would turn ingest into a 500. Naive input is assumed
    to be UTC here.
    """
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


async def verify_api_key(authorization: str | None = Header(default=None)) -> None:
    """Validate the `Authorization: Bearer <key>` header against the configured key.

    The header is declared optional so a *missing* one is handled here as a 401.
    Declaring it required (`Header(...)`) would make FastAPI reject the request
    during parameter validation with a 422 before this ever runs, which is the
    wrong status for an auth failure.
    """
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must use the Bearer scheme",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.removeprefix("Bearer ")
    # compare_digest rather than == so a wrong key can't be recovered by timing
    # how long the comparison takes to fail.
    if not secrets.compare_digest(token, settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.post(
    "/spans",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_api_key)],
)
async def ingest_spans(batch: SpanBatch, db: AsyncSession = Depends(get_db)) -> None:
    """Ingest a batch of spans, creating any traces they belong to.

    Idempotency is critical here because the SDK exporter retries on network
    failure. A retry replays a batch the server may have already committed —
    typically when the write succeeded but the response never made it back.
    Without ON CONFLICT DO NOTHING every retry would insert duplicate spans,
    permanently corrupting the trace with repeated entries and inflated costs.
    Keying on span_id makes replay a no-op instead.
    """
    # A batch can legitimately repeat a span_id; collapse those first so the
    # INSERT carries one row per key and the rollup can't count one span twice.
    spans_by_id = {span.span_id: span for span in batch.spans}
    unique_spans = list(spans_by_id.values())

    incoming_trace_ids = {span.trace_id for span in unique_spans}

    # One SELECT for every trace this batch touches. Fetching the rows (not just
    # the ids) means the cost/token rollup below can update existing traces
    # without a second round trip.
    existing_traces = (
        (await db.execute(select(Trace).where(Trace.trace_id.in_(incoming_trace_ids))))
        .scalars()
        .all()
    )
    traces_by_id: dict[uuid.UUID, Trace] = {t.trace_id: t for t in existing_traces}
    new_ids = incoming_trace_ids - traces_by_id.keys()

    # Group by trace so each new trace can be seeded from its earliest span.
    spans_by_trace: dict[uuid.UUID, list] = defaultdict(list)
    for span in unique_spans:
        spans_by_trace[span.trace_id].append(span)

    # Per-trace aggregates for this batch. Unlike the cost/token rollup below,
    # these are computed over *every* incoming span rather than only the newly
    # inserted ones: `max` and `or` are idempotent, so replaying a batch
    # recomputes the same value instead of compounding it.
    batch_max_end: dict[uuid.UUID, datetime] = {}
    batch_has_error: dict[uuid.UUID, bool] = {}
    for span in unique_spans:
        end = _as_utc(span.end_time)
        if end is not None:
            current = batch_max_end.get(span.trace_id)
            if current is None or end > current:
                batch_max_end[span.trace_id] = end
        if span.status == "error":
            batch_has_error[span.trace_id] = True

    new_traces = []
    for trace_id in new_ids:
        earliest = min(spans_by_trace[trace_id], key=lambda s: s.start_time)
        trace = Trace(
            trace_id=trace_id,
            project=batch.project,
            # Only a span with no parent can be the root. A batch may arrive
            # before the real root does, in which case this stays null rather
            # than mislabelling a child span as the root.
            root_span_id=earliest.span_id if earliest.parent_span_id is None else None,
            start_time=earliest.start_time,
            # Seeded from this batch; both fields advance monotonically as
            # later batches for the same trace arrive.
            end_time=batch_max_end.get(trace_id),
            status="error" if batch_has_error.get(trace_id) else "ok",
            total_cost_usd=0.0,
            total_tokens=0,
        )
        new_traces.append(trace)
        traces_by_id[trace_id] = trace

    db.add_all(new_traces)
    # Flush so the parent rows exist before the span INSERT below, which the
    # trace_id foreign key requires.
    await db.flush()

    span_dicts = [span.model_dump() for span in unique_spans]
    stmt = (
        pg_insert(Span)
        .values(span_dicts)
        .on_conflict_do_nothing(index_elements=["span_id"])
        # RETURNING yields only the rows actually inserted; conflicting rows are
        # omitted. That is what lets the rollup below stay idempotent — a replayed
        # span contributes nothing a second time.
        .returning(Span.span_id)
    )
    inserted_ids = set((await db.execute(stmt)).scalars().all())

    # Roll costs and tokens up onto the parent trace at write time so dashboard
    # reads never need a GROUP BY over spans.
    for span_id in inserted_ids:
        span = spans_by_id[span_id]
        trace = traces_by_id[span.trace_id]
        if span.cost_usd is not None:
            trace.total_cost_usd += span.cost_usd
        if span.input_tokens is not None:
            trace.total_tokens += span.input_tokens
        if span.output_tokens is not None:
            trace.total_tokens += span.output_tokens

    # Advance end_time and status on traces that already existed. Spans for one
    # trace arrive across multiple flushes — an in-progress agent emits early
    # spans long before it finishes — so these must only ever move forward:
    # end_time to a later timestamp, status from "ok" to "error". A late batch
    # carrying an earlier end_time or a healthy span must not undo either.
    for trace_id, trace in traces_by_id.items():
        if trace_id in new_ids:
            continue  # already seeded above from this same batch

        candidate_end = batch_max_end.get(trace_id)
        if candidate_end is not None:
            current_end = _as_utc(trace.end_time)
            if current_end is None or candidate_end > current_end:
                trace.end_time = candidate_end

        if batch_has_error.get(trace_id) and trace.status == "ok":
            trace.status = "error"

    await db.commit()


@router.get("/", response_model=list[TraceOut], dependencies=[Depends(verify_api_key)])
async def list_traces(
    project: str = "default",
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[Trace]:
    """List the most recent traces for a project, newest first."""
    result = await db.execute(
        select(Trace)
        .where(Trace.project == project)
        .order_by(Trace.start_time.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


# selectinload prevents N+1 queries: one query for the trace, then a single
# second query loading all of its spans via an IN clause. Without it, touching
# trace.spans would lazy-load — a query per span, and in async code that lazy
# load happens outside an await and raises MissingGreenlet instead of merely
# being slow.
@router.get(
    "/{trace_id}", response_model=TraceDetail, dependencies=[Depends(verify_api_key)]
)
async def get_trace(
    trace_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> Trace:
    """Fetch a single trace with its full span list."""
    result = await db.execute(
        select(Trace).where(Trace.trace_id == trace_id).options(selectinload(Trace.spans))
    )
    trace = result.scalar_one_or_none()
    if trace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Trace not found"
        )
    return trace
