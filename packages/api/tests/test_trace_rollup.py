"""Trace-level end_time/status rollup: seeded on create, monotonic on update.

Spans for one trace arrive across multiple flushes, so these fields must only
ever move forward — end_time to a later timestamp, status from "ok" to "error".
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from vantage_api.config import settings
from vantage_api.database import SessionLocal
from vantage_api.main import app

AUTH = {"Authorization": f"Bearer {settings.api_key}"}
PROJECT = "rollup-test"
T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
    # Tear down every trace this module created.
    async def cleanup():
        from sqlalchemy import delete
        from vantage_api.models import Trace

        async with SessionLocal() as db:
            await db.execute(delete(Trace).where(Trace.project == PROJECT))
            await db.commit()

    asyncio.run(cleanup())


def make_span(trace_id, *, end_offset_s=None, status="ok", parent=None):
    span = {
        "span_id": str(uuid.uuid4()),
        "trace_id": str(trace_id),
        "parent_span_id": str(parent) if parent else None,
        "name": "unit",
        "start_time": T0.isoformat(),
        "status": status,
    }
    if end_offset_s is not None:
        span["end_time"] = (T0 + timedelta(seconds=end_offset_s)).isoformat()
    if status == "error":
        span["error_message"] = "ValueError: boom"
    return span


def ingest(client, spans):
    r = client.post(
        "/traces/spans", json={"project": PROJECT, "spans": spans}, headers=AUTH
    )
    assert r.status_code == 204, r.text
    return r


def get_trace(client, trace_id):
    r = client.get(f"/traces/{trace_id}", headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def test_new_trace_seeds_end_time_from_max_span_and_status_ok(client):
    tid = uuid.uuid4()
    ingest(client, [make_span(tid, end_offset_s=1), make_span(tid, end_offset_s=5)])

    trace = get_trace(client, tid)
    assert parse(trace["end_time"]) == T0 + timedelta(seconds=5)
    assert trace["status"] == "ok"


def test_status_escalates_to_error_and_end_time_advances(client):
    tid = uuid.uuid4()
    ingest(client, [make_span(tid, end_offset_s=1), make_span(tid, end_offset_s=5)])

    ingest(client, [make_span(tid, end_offset_s=9, status="error")])

    trace = get_trace(client, tid)
    assert trace["status"] == "error"
    assert parse(trace["end_time"]) == T0 + timedelta(seconds=9)


def test_end_time_does_not_regress_and_status_does_not_recover(client):
    tid = uuid.uuid4()
    ingest(client, [make_span(tid, end_offset_s=5)])
    ingest(client, [make_span(tid, end_offset_s=9, status="error")])

    # A late batch carrying an EARLIER end_time and a healthy status.
    ingest(client, [make_span(tid, end_offset_s=2)])

    trace = get_trace(client, tid)
    assert parse(trace["end_time"]) == T0 + timedelta(seconds=9), "end_time regressed"
    assert trace["status"] == "error", "status regressed from error to ok"


def test_new_trace_created_with_error_status_from_the_start(client):
    tid = uuid.uuid4()
    ingest(client, [make_span(tid, end_offset_s=3, status="error")])

    trace = get_trace(client, tid)
    assert trace["status"] == "error"
    assert parse(trace["end_time"]) == T0 + timedelta(seconds=3)


def test_spans_with_no_end_time_leave_trace_end_time_null(client):
    tid = uuid.uuid4()
    ingest(client, [make_span(tid), make_span(tid)])

    trace = get_trace(client, tid)
    assert trace["end_time"] is None
    assert trace["status"] == "ok"


def test_replaying_a_batch_does_not_change_the_rollup(client):
    """max/or are idempotent; a retried batch must produce identical values."""
    tid = uuid.uuid4()
    spans = [make_span(tid, end_offset_s=4), make_span(tid, end_offset_s=8)]
    ingest(client, spans)
    first = get_trace(client, tid)

    ingest(client, spans)
    ingest(client, spans)
    again = get_trace(client, tid)

    assert again["end_time"] == first["end_time"]
    assert again["status"] == first["status"]
    assert again["total_tokens"] == first["total_tokens"]
    assert again["total_cost_usd"] == first["total_cost_usd"]
    assert len(again["spans"]) == 2
