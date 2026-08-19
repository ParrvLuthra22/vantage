# Architecture

How Vantage models agent execution, how spans get from a running process into
Postgres, and why each choice was made. This is the design rationale document — if
you want to know *what* the API does, read the [SDK reference](../packages/sdk/README.md).

---

## 1. The data model

Agent execution is a tree. One user request fans out into classification, routing,
retrieval, one or more model calls, tool invocations, and possibly sub-agents. Two
tables represent that.

### `traces` — one row per end-to-end run

| Column | Type | Notes |
| --- | --- | --- |
| `trace_id` | `uuid` PK | Generated client-side by the SDK. |
| `project` | `varchar(64)` | Indexed. The tenancy boundary. |
| `root_span_id` | `uuid` nullable | The parentless span, when one has arrived. |
| `start_time` | `timestamptz` | Indexed. Earliest span seen. |
| `end_time` | `timestamptz` nullable | Not yet populated — see [Known gaps](#7-known-gaps). |
| `status` | `varchar(16)` | Defaults `"ok"`. |
| `tags` | `jsonb` | Free-form trace-level labels. |
| `total_cost_usd` | `double precision` | Denormalized rollup. |
| `total_tokens` | `integer` | Denormalized rollup. |

### `spans` — one row per unit of work

| Column | Type | Notes |
| --- | --- | --- |
| `span_id` | `uuid` PK | Generated client-side. The idempotency key. |
| `trace_id` | `uuid` FK → `traces` | `ON DELETE CASCADE`, indexed. |
| `parent_span_id` | `uuid` nullable | **No FK constraint** — see below. |
| `name` | `varchar(256)` | e.g. `orchestrator.handle`. |
| `start_time` / `end_time` | `timestamptz` | End is nullable for in-flight spans. |
| `attributes` | `jsonb` | GIN indexed. User-defined keys. |
| `status` / `error_message` | `varchar(16)` / `text` | `"ok"` or `"error"` plus `"Type: message"`. |
| `model`, `input_tokens`, `output_tokens`, `cost_usd` | | LLM metadata. Null on non-model spans. |

### Why IDs are generated client-side

The SDK mints `trace_id` and `span_id` before any network call. A span therefore
knows its own identity and its parent's identity immediately, so a whole tree can be
built without waiting on a server round trip per span. This is what allows the
exporter to be fully asynchronous, and it is what makes retries idempotent — the key
already exists on the client before the first attempt.

### Why `parent_span_id` has no foreign key

Spans are exported in batches from a background thread, and a batch boundary or a
retry can deliver a child before its parent. A self-referential FK would reject those
inserts outright, forcing either client-side buffering until the parent is confirmed
or deferred constraints across the whole ingest transaction. Both add coupling to
solve a problem that doesn't need solving: a temporarily dangling `parent_span_id`
resolves itself as soon as the parent lands, and readers already tolerate partial
trees because traces are queried while still in flight.

The `trace_id` FK *is* enforced, because ingest creates the trace row in the same
transaction as its spans. That gives cascade deletes for free: dropping a trace
removes its spans in one statement, with no orphans even if the delete comes from
outside the ORM.

### Why `attributes` is JSONB with a GIN index

Attributes are a user-defined, arbitrary schema. Every app attaches its own keys, so
there is no column set to pin down in advance. JSONB keeps writes schemaless; the GIN
index keeps reads fast for the two operations that matter — key existence
(`attributes ? 'user_id'`) and containment (`attributes @> '{"env":"prod"}'`). A
B-tree cannot index arbitrary keys this way, and a key/value side table would turn
every trace view into a join fan-out.

### Why cost and tokens are denormalized onto `traces`

`total_cost_usd` and `total_tokens` are summed at write time. The alternative —
deriving them on read — means a `GROUP BY` over `spans` on every trace-list request,
which is the hottest query in the product, and whose cost grows with spans per trace.
Denormalizing makes list reads O(1) per trace. The tradeoff is that ingest owns
keeping them consistent, which is why the rollup is tied to the idempotency mechanism
described below. (Verified against ground truth: a `SUM` over child spans matches the
stored rollup for every trace in the reference integration.)

---

## 2. The write path

```
 @trace / span()
      │  builds SpanCreate, mints ids from ContextVars
      ▼
 queue.Queue(maxsize=10_000)          ← submit() returns here, always
      │  put_nowait; drop if full
      ▼
 worker thread (daemon)
      │  batch by size (50) OR time (5s), whichever first
      ▼
 POST /traces/spans                   ← chunked at 500 spans/request
      │
      ▼
 FastAPI ingest
      │  1 SELECT for touched traces
      │  create missing trace rows, flush
      │  INSERT ... ON CONFLICT (span_id) DO NOTHING RETURNING span_id
      │  roll up cost/tokens for RETURNED rows only
      ▼
 single COMMIT
```

### Context propagation

`current_trace_id` and `current_span_id` are `ContextVar`s, not `threading.local`.
Instrumented agent code is usually async: one OS thread interleaves many concurrent
requests, so thread-local state would be shared across all of them and spans would
attach to whichever trace last touched the thread. ContextVars are scoped to the
logical task — each asyncio Task gets a copy-on-write view — so a value set inside one
request stays visible across every `await` in that request and invisible to every
other. The same mechanism works unchanged in synchronous and threaded code.

Nesting uses the `Token` returned by `set()` and restores it with `reset()`. Setting
`None` on exit would flatten the tree, making sibling spans appear as roots.

### The three exporter guarantees

1. **Never block the app.** A bounded queue with `put_nowait`. Bounded rather than
   infinite because an unbounded queue doesn't remove backpressure, it converts it
   into unbounded memory growth and eventually an OOM kill of the host process.
   Dropping telemetry is acceptable; killing the app that produced it is not.
2. **Never crash the app.** All network I/O is on a background thread and every
   exception is swallowed. Users asked us to observe their agent, not to become a new
   source of outages in it.
3. **Never lose spans on clean shutdown.** The worker is a daemon thread so it can't
   keep a finished process alive; an `atexit` hook signals it and waits for a final
   drain. Without that, every span buffered since the last flush would vanish on
   exit — precisely the window containing the crash you most wanted to inspect.

Batching is by size **and** time. Size alone starves low-traffic apps, where a
half-full batch could sit for hours. Time alone gives no protection against bursts.
The pair bounds both delivery latency and maximum request size.

### Idempotency

The SDK retries on network failure, and the most common retry case is a request that
*succeeded* server-side but whose response was lost. Ingest therefore inserts spans
with `ON CONFLICT (span_id) DO NOTHING`, making replay a no-op.

That alone is not sufficient. The cost/token rollup is an increment, and increments
are not idempotent — replaying a batch would skip the span inserts but add the costs
again, silently inflating every number on the dashboard. So the insert uses
`RETURNING span_id`, which yields only rows actually inserted, and the rollup iterates
over that set. Delivering the same batch three times produces one set of spans and
one set of costs.

Batches are also de-duplicated by `span_id` in-process before the INSERT, since two
copies of a span within a single request would not conflict with anything already
committed and would otherwise be counted twice.

### Request size ceiling

`SpanBatch` caps `spans` at 500. Ingest writes a batch as one multi-row INSERT, and
Postgres has practical limits on statement size and bind parameters per statement; at
~13 columns per span, 500 rows stays comfortably inside them and keeps the JSON
payload near 1 MB. The SDK chunks at the same number, which matters most at shutdown,
when a drain can pull the entire queue — far more than `batch_size` — into a single
flush.

---

## 3. The read path

| Endpoint | Purpose |
| --- | --- |
| `POST /traces/spans` | Batch ingest. Returns 204. |
| `GET /traces?project=&limit=` | Trace summaries, newest first. Max `limit` 200. |
| `GET /traces/{trace_id}` | One trace with its full span list. |
| `GET /health` | Unauthenticated liveness probe. |

The list query is served by a composite index on `(project, start_time DESC)`,
declared descending so `WHERE project = ? ORDER BY start_time DESC` walks the index
forward with no sort step.

Trace detail uses `selectinload(Trace.spans)`: one query for the trace, one for all
its spans via `IN`. Without it, touching `trace.spans` would lazy-load — and in async
SQLAlchemy that lazy load happens outside an `await` and raises `MissingGreenlet`
rather than merely being slow.

---

## 4. API contract boundary

Pydantic schemas are kept strictly separate from ORM models, and routes never return
ORM objects directly. The ORM is then free to churn — new columns, renamed internals,
denormalization for performance — without any of it silently leaking into responses.
Serializing ORM objects directly makes every schema migration a potential undeclared
breaking change, invisible at review time because nothing in the diff mentions the
API.

The same boundary protects writes: an `In` schema pins exactly which fields a client
may set, so a new column can never become accidentally client-writable.

The SDK re-declares this contract in `vantage/models.py` rather than importing it.
The SDK ships to users' machines on its own release cycle and cannot depend on the
API package. The duplication is deliberate and the two must be changed together;
their field/type/constraint parity is worth asserting in a test.

---

## 5. Async database layer

- `pool_pre_ping=True` — critical for serverless Postgres such as Neon, which closes
  idle connections server-side and leaves dead sockets in the pool. Pre-ping validates
  on checkout and transparently replaces stale connections, instead of surfacing a
  "connection was closed" error mid-request.
- `expire_on_commit=False` — with the default, touching any attribute of a committed
  object triggers a lazy refresh; in async code that I/O happens outside an `await`
  and raises `MissingGreenlet`. Keeping attributes loaded after commit avoids it.
- `sqlalchemy[asyncio]` — the `asyncio` extra is required. SQLAlchemy no longer
  auto-installs `greenlet` on all platforms, and without it the engine constructs fine
  but every query fails at runtime.

---

## 6. Authentication

A single bearer token compared with `secrets.compare_digest`, so a wrong key can't be
recovered by timing the failure. This is deliberately minimal for Week 1: one shared
key per deployment, no per-project scoping, no rotation. Multi-tenant auth is a
roadmap item, not an oversight.

---

## 7. Known gaps

Honest list of what is not done yet.

- **Traces are never closed out.** `end_time` stays null and `status` stays `"ok"`
  even when child spans error. Ingest creates trace rows and rolls up cost/tokens but
  never finalizes them, so trace-level duration and "show me failed traces" are not
  answerable from the `traces` table. Span-level error data *is* recorded correctly.
- **Schema changes need migrations.** Startup uses `Base.metadata.create_all`, which
  only creates missing tables. It ignores column additions, type changes, and new
  indexes on existing tables, so once a table exists its shape is frozen. Alembic
  lands in Week 2.
- **Export failures are invisible.** The exporter swallows HTTP errors without
  inspecting status, so a bad API key or a rejected batch looks identical to success.
  Dropped-span and failed-request counters are needed before this runs anywhere real.
- **No retry or persistence.** A failed batch is gone. There is no disk spooling, so a
  backend outage loses everything buffered during it.
- **Single-key auth**, as above.
