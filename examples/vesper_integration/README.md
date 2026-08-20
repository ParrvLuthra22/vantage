# Vesper integration example

A minimal Vesper-shaped orchestrator instrumented with the Vantage SDK. It is the
reference for what "instrumented" looks like in practice: a decorated entry point,
nested spans for each pipeline stage, and LLM metadata captured on the span that
actually calls a model.

## What it demonstrates

- **Multi-span traces** — one trace per user request, four spans inside it.
- **Parent-child hierarchy** — `@trace()` opens the root span, and each `with span(...)`
  nests underneath it automatically via the SDK's ContextVars. Nothing is threaded
  through by hand.
- **LLM metadata capture** — `sp.set_llm(...)` records model, token counts, and cost.
  Those are promoted to real columns rather than staying inside the JSONB attributes.
- **Cost rollup** — the backend sums span costs and tokens onto the parent trace at
  write time, so a trace list shows spend without aggregating over spans.

## Prerequisites

Postgres and the API must both be running:

```bash
docker compose up -d
cd packages/api
alembic upgrade head
uvicorn vantage_api.main:app --port 8000
```

The example targets `http://localhost:8000` with the development key
`dev-key-change-me`, matching the defaults in `vantage_api/config.py`.

## Run it

```bash
python examples/vesper_integration/main.py
```

## What to expect

Four lines of orchestrator output, then a six-second pause while the exporter
flushes, then `Done. Check Postgres.`

The pause is not incidental. The SDK batches by size *and* time, and with the
default five-second flush interval these sixteen spans are well under the batch
threshold — so they leave on the timer. Exiting immediately would still deliver
them via the `atexit` hook, but the sleep makes the flush observable.

Server-side you should see:

- 4 traces under project `vesper`, each with `total_tokens > 0` and `total_cost_usd > 0`
- 4 spans per trace: `orchestrator.handle` as root, with `intent_classification`,
  `agent_selection`, and `agent_execution` as its children
- `agent_execution` carrying `model=gemini-2.0-flash` plus token and cost columns

Verify all of it with:

```bash
bash scripts/verify_week1.sh
```

## Routing note

Only inputs containing the word "meeting" route to `calendar_agent`; everything else
goes to `chat_agent`. With the four sample inputs that is a 1/3 split — the doctor
appointment and the email draft both classify as `chat`, since the classifier is a
deliberate one-line stand-in for a real model call.
