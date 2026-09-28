# Vantage

**Self-hosted evaluation and observability for LLM agents.**

[![CI](https://github.com/ParrvLuthra22/vantage/actions/workflows/ci.yml/badge.svg)](https://github.com/ParrvLuthra22/vantage/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/vantage-observability.svg)](https://pypi.org/project/vantage-observability/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

Vantage gives you a trace tree, cost tracking, and evaluation pipeline for any Python
LLM agent. Built for teams that want LangSmith-style observability without sending
traces to a third party.

> **Status:** Trace pipeline, evaluation pipeline (deterministic + LLM-judge scoring,
> Postgres-backed run history, regression detection), and dashboard all run end to end
> today against a real agent (Vesper). Current focus is Week 5: judge/agent
> infrastructure-failure handling, a human-labeling workflow, and production deployment.
> See [Roadmap](#roadmap) and [Eval baseline](#eval-baseline).

## Quick start

### 1. Start the backend

```bash
git clone https://github.com/ParrvLuthra22/vantage.git
cd vantage
docker compose up -d                      # Postgres 16 on :5432

cd packages/api
uv pip install -e .
cp .env.example .env                      # local settings; .env is gitignored
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
pip install vantage-observability   # imported as `vantage` — see note below if this 404s
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

## Install (SDK)

```bash
pip install vantage-observability
```

*Note: PyPI publication pending as of 2026-09-27. Until published, install from source:*
`pip install git+https://github.com/ParrvLuthra22/vantage.git#subdirectory=packages/sdk`

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

## Eval baseline

Current `orchestrator_v1` baseline (real Vesper, Groq judge, 2026-09-28): **60.0% adjusted
pass rate** (18/30, after excluding judge-infrastructure and known-failing scenarios from the
denominator), with a 38.5%–69.2% range across the three samples collected that morning — the
median, not the best sample, is what's marked. See
[`docs/baselines/orchestrator_v1_baseline_20260928.md`](docs/baselines/orchestrator_v1_baseline_20260928.md)
for the full methodology, why the range is that wide (real, explained variance in how much of
a run falls back to a weaker local model under Groq load — not measurement noise), and
[`docs/interview_exhibits/README.md`](docs/interview_exhibits/README.md) for concrete
evidence of what the eval infrastructure has caught in real runs.

## Roadmap

- [x] **Week 1** — Trace pipeline. Postgres schema, FastAPI ingest and query API,
      batched SDK exporter, `@trace`/`span()` instrumentation, reference integration.
- [x] **Week 2** — Alembic migrations, replacing `create_all`. Trace completion
      (`end_time`, error status) via rollup logic.
- [x] **Week 3** — Evaluation pipeline: deterministic + LLM-judge scoring, Postgres-backed
      suite/run/result history, regression detection, `vantage eval run|compare` CLI —
      substantially past the original scope of this week once real-agent integration
      (Vesper) surfaced how much a real judge and a real adapter actually need.
- [x] **Week 4** — Dashboard: trace views, eval-run list and detail views (pass rate,
      known-failing and judge-error breakdowns), baseline management.
- [ ] **Week 5** (in progress) — Judge- and agent-side infrastructure-failure handling,
      real-Vesper integration hardening (Ollama fallback, Groq quota limits), trajectory
      persistence for a human-labeling workflow, and a first real baseline. See
      [`docs/deferred_for_week5.md`](docs/deferred_for_week5.md) for the open items.
- [ ] **Week 6** — PyPI publish, production deployment (Railway + Neon), full
      documentation site.

## Contributing

Early days, and the design is still moving — issues are the most useful thing right
now. Bug reports, questions about the data model, and "this broke on my agent"
reports are all welcome. Open an issue before a large PR so we can agree on the shape.

## License

MIT — see [LICENSE](LICENSE).
