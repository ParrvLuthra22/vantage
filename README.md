# Vantage

**Self-hosted evaluation and observability for LLM agents.**

[![PyPI](https://img.shields.io/pypi/v/vantage-sdk.svg)](https://pypi.org/project/vantage-sdk/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

Vantage gives you a trace tree, cost tracking, and evaluation pipeline for any Python
LLM agent. Built for teams that want LangSmith-style observability without sending
traces to a third party.

> **Status:** Week 1 of build. The trace pipeline below works end to end today; the
> evaluation pipeline and dashboard are on the roadmap. See [Roadmap](#roadmap).

## Quick start

### 1. Start the backend

```bash
git clone https://github.com/ParrvLuthra22/vantage.git
cd vantage
docker compose up -d                      # Postgres 16 on :5432

cd packages/api
uv pip install -e .
alembic upgrade head                      # apply schema migrations
uvicorn vantage_api.main:app --port 8000  # API on :8000
```

The schema is owned by Alembic — run `alembic upgrade head` before starting the
app, and as the first step of any deploy. Check it's alive:

```bash
curl http://localhost:8000/health
# {"status":"ok","version":"0.1.0"}
```

### 2. Instrument your agent

```bash
pip install vantage-sdk
```

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
        intent = classify(user_input)
        sp.set("classified_intent", intent)

    with span("agent_execution", attributes={"agent": intent}) as sp:
        result, usage = call_model(user_input)
        sp.set_llm(
            model="gemini-2.0-flash",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=usage.cost,
        )
        return result
```

Spans nest automatically — no context object to thread through your call stack.
The exporter batches in the background and never blocks or crashes your agent.

### 3. View results

```bash
# List traces for a project
curl -H "Authorization: Bearer dev-key-change-me" \
  "http://localhost:8000/traces?project=my-agent"

# Full span tree for one trace
curl -H "Authorization: Bearer dev-key-change-me" \
  "http://localhost:8000/traces/<trace_id>"
```

```
- orchestrator.handle      386.6ms
  - intent_classification   56.4ms  {"classified_intent": "chat"}
  - agent_selection         22.5ms  {"selected_agent": "chat_agent"}
  - agent_execution        307.1ms  [gemini-2.0-flash in=234 out=125 $0.00609]
```

There is a runnable reference agent in
[`examples/vesper_integration/`](examples/vesper_integration/) and a verification
script at `scripts/verify_week1.sh`.

## Architecture

```
   your agent process                       vantage backend
 ┌───────────────────────┐               ┌──────────────────────┐
 │  @trace / with span() │               │  FastAPI             │
 │          │            │               │   POST /traces/spans │
 │          ▼            │               │   GET  /traces       │
 │   ContextVars         │               │   GET  /traces/{id}  │
 │  (trace + parent id)  │               └──────────┬───────────┘
 │          │            │                          │
 │          ▼            │   HTTPS, batched         │ async SQLAlchemy
 │  bounded Queue────────┼──────────────────────────┤
 │          │  drop when │   ON CONFLICT DO NOTHING │
 │          ▼  full      │   (idempotent retries)   ▼
 │  worker thread        │               ┌──────────────────────┐
 │  batch by size + time │               │  Postgres 16         │
 └───────────────────────┘               │  traces / spans      │
                                         │  JSONB + GIN index   │
                                         └──────────────────────┘
```

Instrumentation is a guest in someone else's process, so the SDK is built to fail
quietly: a bounded queue that drops rather than blocks, a background worker that
swallows every network error, and an `atexit` hook so a clean exit never loses
buffered spans.

Full write-up — data model, ingest flow, and the reasoning behind each choice — in
[`docs/architecture.md`](docs/architecture.md).

## Roadmap

- [x] **Week 1** — Trace pipeline. Postgres schema, FastAPI ingest and query API,
      batched SDK exporter, `@trace`/`span()` instrumentation, reference integration.
- [ ] **Week 2** — Alembic migrations, replacing `create_all`. Trace completion
      (`end_time`, error status) and retention.
- [ ] **Week 3** — Evaluation pipeline: scorers, datasets, and offline runs.
- [ ] **Week 4** — Dashboard: trace waterfall, cost breakdowns, project views.
- [ ] **Week 5** — Search and filtering over JSONB attributes; latency percentiles.
- [ ] **Week 6** — Full documentation site and deployment guides.

## Contributing

Early days, and the design is still moving — issues are the most useful thing right
now. Bug reports, questions about the data model, and "this broke on my agent"
reports are all welcome. Open an issue before a large PR so we can agree on the shape.

## License

MIT — see [LICENSE](LICENSE).
