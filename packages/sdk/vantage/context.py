"""Ambient trace/span context for the current logical task.

These are ContextVars, not `threading.local`, and the difference is the whole
point. Instrumented agent code is typically async: a single OS thread
interleaves many concurrent requests, so thread-local state would be shared
between all of them and spans would attach to whichever trace happened to touch
the thread last. ContextVars are scoped to the logical task instead — each
asyncio Task gets a copy-on-write view of the context at creation, so a value
set inside one request stays visible across every `await` in that request and
invisible to every other. `contextvars` is the canonical Python 3.7+ mechanism
for this, and it works unchanged in plain synchronous and threaded code too.

A span reads `current_span_id` to find its parent, then sets itself as the
current span for the duration of its body. Because assignment returns a Token,
the previous value can be restored exactly on exit, which is what keeps sibling
spans from nesting under each other by accident.
"""

from contextvars import ContextVar
from typing import Optional
from uuid import UUID

# The trace this task belongs to; None when no trace is active.
current_trace_id: ContextVar[Optional[UUID]] = ContextVar(
    "vantage_trace_id", default=None
)

# The innermost open span; becomes the parent_span_id of the next span opened.
current_span_id: ContextVar[Optional[UUID]] = ContextVar(
    "vantage_span_id", default=None
)
