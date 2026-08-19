"""The developer-facing instrumentation API: `span()` and `@trace()`."""

import functools
import inspect
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from vantage.client import get_client
from vantage.context import current_span_id, current_trace_id
from vantage.models import SpanCreate

# Keys SpanProxy.set_llm writes into the attribute dict. They are lifted out on
# emit and promoted to dedicated SpanCreate fields, so they end up as real
# columns the backend can aggregate on rather than as opaque JSON.
_LLM_KEYS = ("_model", "_input_tokens", "_output_tokens", "_cost_usd")


class SpanProxy:
    """Handle yielded to the caller for attaching data to the open span."""

    def __init__(self, attrs: dict) -> None:
        self._attrs = attrs

    def set(self, key: str, value: Any) -> None:
        """Attach an arbitrary key/value to this span's attributes."""
        self._attrs[key] = value

    def set_llm(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> None:
        """Record LLM call metadata on this span.

        These land under reserved `_`-prefixed keys and are promoted to
        top-level SpanCreate fields on emit — they do not stay in `attributes`.
        """
        self._attrs["_model"] = model
        self._attrs["_input_tokens"] = input_tokens
        self._attrs["_output_tokens"] = output_tokens
        self._attrs["_cost_usd"] = cost_usd


@contextmanager
def span(name: str, attributes: Optional[dict] = None):
    """Open a span, yielding a proxy for attaching data to it.

    The try/finally is what makes this safe to wrap around real code: the span
    always closes and is always emitted, whether the body returns normally or
    raises. On an exception the span is marked `status="error"` and
    `error_message` captures the exception type and message, and then the
    exception is re-raised unchanged — we record what happened, we never
    swallow it. Instrumentation observes control flow; it must not alter it.
    """
    # No active trace means this is the root span of a new one.
    trace_id = current_trace_id.get() or uuid.uuid4()
    parent_span_id = current_span_id.get()
    this_span_id = uuid.uuid4()

    trace_token = current_trace_id.set(trace_id)
    span_token = current_span_id.set(this_span_id)

    start = datetime.now(timezone.utc)
    status = "ok"
    error_msg: Optional[str] = None
    collected_attrs = dict(attributes or {})
    proxy = SpanProxy(collected_attrs)

    try:
        yield proxy
    except Exception as e:
        status = "error"
        error_msg = f"{type(e).__name__}: {e}"
        raise
    finally:
        end = datetime.now(timezone.utc)

        # Emitting is guarded so that a failure here can never replace the
        # user's in-flight exception, and can never skip the context resets
        # below — a half-unwound context would silently reparent every
        # subsequent span in this task.
        try:
            client = get_client()
            if client is not None:
                model = collected_attrs.pop("_model", None)
                input_tokens = collected_attrs.pop("_input_tokens", None)
                output_tokens = collected_attrs.pop("_output_tokens", None)
                cost_usd = collected_attrs.pop("_cost_usd", None)
                client.submit(
                    SpanCreate(
                        span_id=this_span_id,
                        trace_id=trace_id,
                        parent_span_id=parent_span_id,
                        name=name,
                        start_time=start,
                        end_time=end,
                        attributes=collected_attrs,
                        status=status,
                        error_message=error_msg,
                        model=model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cost_usd=cost_usd,
                    )
                )
        except Exception:
            pass

        # Reset via Token rather than setting None, so nesting restores the
        # exact parent instead of flattening the tree.
        current_span_id.reset(span_token)
        current_trace_id.reset(trace_token)


def trace(name: Optional[str] = None) -> Callable:
    """Decorator factory wrapping a function call in a span.

    `inspect.iscoroutinefunction` detection means the same `@vantage.trace()`
    works transparently on `def` and `async def` alike. That matters for SDK
    ergonomics: users should never have to remember a separate `@atrace`, and
    agent code routinely mixes both in one module. Applying a sync wrapper to a
    coroutine function would be worse than useless — it would close the span the
    instant the coroutine object was created, recording a duration of
    microseconds for work that hasn't started.
    """

    def decorator(fn: Callable) -> Callable:
        span_name = name or f"{fn.__module__}.{fn.__qualname__}"

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with span(span_name):
                    return await fn(*args, **kwargs)

            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with span(span_name):
                return fn(*args, **kwargs)

        return sync_wrapper

    return decorator
