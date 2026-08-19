"""Smoke tests for the SDK: exports, context isolation, and span shape."""

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
import vantage
from pydantic import ValidationError
from vantage.context import current_span_id, current_trace_id
from vantage.models import SpanCreate


def test_public_exports():
    for name in vantage.__all__:
        assert hasattr(vantage, name), f"{name} missing from vantage"
    assert vantage.__version__ == "0.1.0"


def test_span_create_roundtrip():
    s = SpanCreate(
        span_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
        name="test",
        start_time=datetime.now(timezone.utc),
    )
    assert s.status == "ok"
    assert s.attributes == {}
    assert "span_id" in s.model_dump_json()


@pytest.mark.parametrize(
    "field,value",
    [("status", "weird"), ("name", ""), ("input_tokens", -1), ("cost_usd", -0.01)],
)
def test_span_create_rejects_bad_values(field, value):
    base = dict(
        span_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
        name="n",
        start_time=datetime.now(timezone.utc),
    )
    with pytest.raises(ValidationError):
        SpanCreate(**{**base, field: value})


def test_span_without_init_is_a_noop():
    """Instrumentation must be safe in code running with no backend configured."""
    with vantage.span("orphan") as sp:
        sp.set("k", "v")
    assert current_trace_id.get() is None
    assert current_span_id.get() is None


def test_nested_spans_restore_parent_context():
    with vantage.span("outer"):
        outer = current_span_id.get()
        with vantage.span("inner"):
            assert current_span_id.get() != outer
        assert current_span_id.get() == outer
    assert current_span_id.get() is None


def test_span_records_error_and_reraises():
    with pytest.raises(ValueError, match="kaboom"):
        with vantage.span("boom"):
            raise ValueError("kaboom")
    assert current_span_id.get() is None


def test_trace_decorator_handles_sync_and_async():
    @vantage.trace(name="sync")
    def sync_fn():
        return "s"

    @vantage.trace(name="async")
    async def async_fn():
        await asyncio.sleep(0)
        return "a"

    assert sync_fn() == "s"
    assert asyncio.run(async_fn()) == "a"


def test_concurrent_tasks_get_isolated_traces():
    seen = []

    @vantage.trace(name="task")
    async def task():
        await asyncio.sleep(0.01)
        seen.append(current_trace_id.get())

    async def main():
        await asyncio.gather(*(task() for _ in range(3)))

    asyncio.run(main())
    assert len(seen) == 3
    assert len(set(seen)) == 3, "concurrent tasks must not share a trace_id"
