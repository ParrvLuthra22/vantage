# vantage-sdk

Instrumentation for LLM agent applications.

[![PyPI](https://img.shields.io/pypi/v/vantage-sdk.svg)](https://pypi.org/project/vantage-sdk/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/ParrvLuthra22/vantage/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

The Python SDK for [Vantage](https://github.com/ParrvLuthra22/vantage) — self-hosted
evaluation and observability for LLM agents. Decorate a function, open a few spans,
and get a trace tree with per-call token and cost attribution in your own Postgres.

## Install

```bash
pip install vantage-sdk
```

Requires Python 3.10+.

## Quickstart

```python
import vantage
from vantage import span, trace

vantage.init(
    api_key="dev-key-change-me",
    base_url="http://localhost:8000",
    project="my-agent",
)


@trace(name="orchestrator.handle")
def handle_request(user_input: str) -> str:
    with span("intent_classification") as sp:
        sp.set("classified_intent", "schedule")

    with span("agent_execution", attributes={"agent": "calendar"}) as sp:
        sp.set_llm(
            model="gemini-2.0-flash",
            input_tokens=234,
            output_tokens=125,
            cost_usd=0.00609,
        )
        return "done"
```

Spans nest automatically through `contextvars`, so nothing has to be threaded through
your call stack. That works across `await` boundaries and keeps concurrent tasks
isolated from each other.

## How it behaves in your process

The SDK is designed to be a quiet guest:

- **It will not block you.** `submit()` puts spans on a bounded in-memory queue with
  `put_nowait`. When the queue is full, spans are dropped rather than backing up into
  your request path.
- **It will not crash you.** All network I/O happens on a background daemon thread,
  and every exception there is swallowed. A dead backend or a bad key costs you
  telemetry, never uptime.
- **It will not lose spans on a clean exit.** An `atexit` hook signals the worker and
  waits for a final drain.
- **It batches by size and by time**, whichever comes first, so low-traffic apps
  still flush promptly and bursty ones never build one enormous request.

## API reference

### `vantage.init(api_key, **kwargs) -> VantageClient`

Creates the process-wide client and installs it as the default. Call once at startup.

| Argument | Type | Default | Description |
| --- | --- | --- | --- |
| `api_key` | `str` | required | Bearer token sent to the backend. |
| `base_url` | `str` | `"https://api.vantage.dev"` | Backend root. Trailing slashes are stripped. |
| `project` | `str` | `"default"` | Project name every span is filed under. |
| `batch_size` | `int` | `50` | Flush once this many spans are buffered. |
| `flush_interval_s` | `float` | `5.0` | Flush at least this often when spans are pending. |
| `queue_size` | `int` | `10_000` | Bounded queue capacity. Spans are dropped when full. |

### `vantage.get_client() -> Optional[VantageClient]`

Returns the process-wide client, or `None` if `init()` was never called. When no
client is configured, `span()` and `@trace()` are harmless no-ops — safe to leave
instrumentation in code that runs without a backend.

### `vantage.trace(name=None)`

Decorator factory wrapping a whole function call in a span. Note the parentheses:
use `@trace()`, not `@trace`.

```python
@trace()                      # name defaults to "module.qualname"
def my_function(): ...

@trace(name="orchestrator.handle")
async def my_coroutine(): ...  # async is detected automatically
```

Works transparently on `def` and `async def` — the decorator inspects the function
and applies the matching wrapper, so an awaited coroutine is timed over the awaited
work rather than over coroutine creation.

### `vantage.span(name, attributes=None)`

Context manager opening a span nested under whatever span is currently active. If no
trace is in progress, this span becomes the root of a new one. Yields a `SpanProxy`.

```python
with span("retrieval", attributes={"index": "docs-v2"}) as sp:
    sp.set("hits", 12)
```

On an exception the span is recorded with `status="error"` and an `error_message` of
`"ExceptionType: message"`, and **the exception is re-raised unchanged**. Vantage
observes your control flow; it never alters it.

### `SpanProxy`

The handle yielded by `span()`.

| Method | Description |
| --- | --- |
| `set(key, value)` | Attach an arbitrary key/value to the span's `attributes` (stored as JSONB). |
| `set_llm(model, input_tokens, output_tokens, cost_usd)` | Record LLM call metadata. These are promoted to dedicated columns rather than kept in `attributes`, so the backend can aggregate on them. |

### `VantageClient`

The exporter itself. `init()` is the normal entry point; construct directly only if
you need more than one client in a process.

| Method | Description |
| --- | --- |
| `submit(span: SpanCreate)` | Queue a span. Never blocks, never raises. |
| `shutdown(timeout=5.0)` | Signal the worker to drain and wait for it. Registered via `atexit` automatically. |

### `vantage.SpanCreate`

The Pydantic v2 wire model posted to the backend. You rarely construct this directly —
`span()` builds it for you — but it defines the contract: `span_id`, `trace_id`,
`parent_span_id`, `name`, `start_time`, `end_time`, `attributes`, `status`,
`error_message`, `model`, `input_tokens`, `output_tokens`, `cost_usd`.

## Backend

The SDK posts to a Vantage backend you run yourself. See the
[main repository](https://github.com/ParrvLuthra22/vantage) for `docker compose up`
setup, the API, and the data model.

## License

MIT
