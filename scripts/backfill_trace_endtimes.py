"""One-off: backfill trace.end_time and trace.status from spans for existing rows.

Traces written before the end_time/status rollup landed have end_time = NULL and
status = "ok" regardless of what their spans recorded. This recomputes both from
the spans already in the database. Safe to re-run: it derives values rather than
incrementing, so a second pass produces the same result.
"""
import asyncio

from sqlalchemy import func, select, update
from vantage_api.database import SessionLocal, engine
from vantage_api.models import Span, Trace


async def main():
    async with SessionLocal() as db:
        # For each trace, compute max span end_time and any-error status
        result = await db.execute(
            select(
                Span.trace_id,
                func.max(Span.end_time).label("max_end"),
                func.bool_or(Span.status == "error").label("has_error"),
            ).group_by(Span.trace_id)
        )
        rows = result.all()

        for trace_id, max_end, has_error in rows:
            await db.execute(
                update(Trace)
                .where(Trace.trace_id == trace_id)
                .values(
                    end_time=max_end,
                    status="error" if has_error else "ok",
                )
            )
        await db.commit()
        print(f"Backfilled {len(rows)} traces")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
