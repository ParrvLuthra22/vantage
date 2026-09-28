# Deferred to Week 5

Real findings from the P.repair.3 measurement-layer fix (2026-09-21/22, commit `1948256`)
and its validation samples, deliberately not acted on now — scoped out to keep that fix to
"measurement layer only," per an explicit instruction not to re-tune anything else while
establishing the baseline. Each item names the evidence that surfaced it.

## 1. ~~Trajectory persistence in Postgres~~ — FIXED locally, blocked on Neon prod, `1cfabcd99636`

`AgentOutput.tool_sequence` / `.tool_calls` / `.final_reply` (added in `1948256`) only reached
Postgres via a `SuiteRun`'s `--output` JSON dump — `vantage_eval/persistence.py`'s `eval_results`
write didn't carry them, so the dashboard and `vantage eval compare` couldn't show the full
trajectory the judge now sees, only the first-tool `routed_agent` it always could. This
directly blocked Week 5's human-labeling workflow, which needs to query trajectories (e.g.
"every multi-tool-call scenario," "every reply containing 'sent'").

**Done**: `eval_results` gained `tool_sequence`/`tool_calls` (JSONB) and `final_reply` (Text),
all nullable — migration `1cfabcd99636` ("add tool_sequence, tool_calls, final_reply to
eval_results"), applied to local Postgres. `persistence.py`'s `_build_result` now populates
all three; `EvalResultOut` (`schemas.py`) exposes them; no route changes needed since
`eval_routes.get_eval_run` already returns the ORM object directly and
`response_model=EvalRunDetail` handles the ORM→Pydantic mapping via `from_attributes`.
Verified end to end without touching Groq (quota was exhausted — see item 5): ran the mock
adapter against the real `orchestrator_v1` suite (`a40fa9eb-8283-4df4-91be-307f06bab435`),
confirmed fresh rows write real (empty, since MockAdapter doesn't populate these) JSONB
values while historical rows correctly stay NULL, and confirmed the API round-trips both
correctly via a live `curl`. Sample query patterns for the labeling workflow are in
`docs/queries_for_week5_labeling.sql`, each one actually run against the local DB to confirm
it's valid SQL against the real schema (not just plausible-looking SQL) — one of the
originally-proposed queries referenced a `eval_results.output` column that doesn't exist and
was rewritten to use the real `deterministic_scores` JSONB column instead.

**Blocked**: applying the same migration to Neon prod (the task's step 4) — there is no Neon
database provisioned anywhere. `packages/api/.env.example`'s own comment says the prod
`DATABASE_URL` "lives only in Railway's variables," but no Railway project exists locally
(no CLI installed, nothing linked), and no `scripts/switch_db.sh` exists in the repo — checked
`.env*` files, macOS keychain, and for a Railway CLI/project link before concluding this.
Same shape as the Week 5 Modal/HF/W&B and PyPI-account blockers: account/infra provisioning
that needs the user, not something to fake or skip past silently. Whenever Neon/Railway get
provisioned, `alembic upgrade head` against that `DATABASE_URL` is the entire remaining step —
the migration itself is already written, reviewed, and proven correct against local Postgres.

## 2. ~~Skip the LLM judge on agent-side infrastructure failures~~ — FIXED, vantage `a73f675`

Was true until 2026-09-28: when `VesperAdapter` returned `PLANNER_FAILURE` or
`ADAPTER_ERROR`, `runner._run_one` still handed that output to the judge, which graded
non-answers as if they were real routing decisions. (This item's original text also
mentioned "a transient-error sentinel" as a third case — there is no such distinct sentinel
in the codebase; only these two exist.)

**What made this concrete, not hypothetical**: the same morning's attempt to verify the
Ollama fallback scoping fix (item 7) hit an exhausted Groq daily quota (item 5) and drove
34/40 scenarios to `PLANNER_FAILURE`. Under the old behavior every one of those got judged —
`adversarial_006` even scored 5.0 by coincidence, its "I'm having trouble" failure text
happening to read as a defensible non-answer for an adversarial input. That run's 14.3%
pass rate was contaminated twice over before this fix landed.

**Done**: `ScenarioResult.infra_error: bool`, set by `runner._run_one` for either sentinel;
the judge is never called when it's set; `aggregate_pass`/`_summarize` exclude it from the
denominator the same way `known_failing`/`judge_error` are, with its own count. Real Postgres
column (`eval_results.infra_error`, migration `568456394b5d`), not summary-only like
`judge_error`. Verified with a real `orchestrator_v1` run against a deliberately invalid
`GROQ_API_KEY`: scorecard correctly reported "Infra errors: 39, Failed: 0, Effective pass
rate: 0.0% (0/0)" — honest "no data," not a misleading real-failure count — and judge cost
stayed $0.0000.

## 3. Skip the LLM judge on judge-side infrastructure failures

New, from the sample runs after the measurement fix. `LLMJudgeScorer.score()` currently
catches `APIError` and records `llm_judge_score = 0.0`, `llm_judge_reasoning = "judge_error: ..."`
(`vantage_eval/scorers/llm_judge.py`) — the same code path as a real "the judge tried and
gave this a 0" verdict, but it isn't one. Evidence: `out_of_scope_006` in sample 1 (run
`2abe1f6c`) and five scenarios in sample 2 (run `e3f53cd8`: `adversarial_001/004/006/007`,
`ambiguous_001`) all failed this way, every one of them a `RateLimitError` on the judge
model's own output-tokens-per-minute cap (Groq: `qwen/qwen3.8-27b` limited to 1000 OTPM, a
single request asking up to ~1803), not a real judgment. Every one of those five scenarios
had passed with a real score in an earlier run. Fix: give `ScenarioResult` a distinct
`judge_error: bool` (or similar) set on this path instead of clamping to a numeric 0, so a
suite summary can report "N scenarios inconclusive due to judge infrastructure" separately
from "N scenarios failed," rather than silently inflating the failure count. Same failure
*class* as item 2, opposite end of the pipeline — the agent-under-test's infrastructure vs.
the judge's own.

**This isn't edge-case noise — it's a quantifiable chunk of the pass-rate signal.** Run
`e3f53cd8` alone: 5 of 40 scenarios (12.5% of the suite) contaminated in one run, moving that
run's pass rate from 62.5% raw to 71.4% adjusted (excluding the 5 as inconclusive rather than
counting them as failed) — a 9-point swing from pipeline infrastructure, not agent quality.
That 9 points is the concrete value of doing this fix in Week 5.

## 4. ~~Diagnose Vesper's Ollama fallback failing under load~~ — FIXED, vesper `911410e`

`ModelRouter`'s Ollama fallback failed **53 of 53** observed attempts across the two full
runs on 2026-09-21 night and 2026-09-22 (49 during a Groq tokens-per-day exhaustion, 4 more
the next day under an ordinary tokens-per-minute 429) — always "Ollama network error", no
other message. This was not Ollama being unreachable (`curl localhost:11434/api/tags`
answered fine standalone every time checked), and it was not a `router.py` bug either.

**Root cause: a Vesper packaging bug, not a router bug.** `config/settings.yaml` — which
correctly configures `llm.fallback.model: llama3.2:3b` (2.0GB, fixed in vesper `f96be6a`;
the file's own comment: "Deliberately a 3B model... Do NOT raise this to a 7B... that is the
exact change that made the machine unusable before") — was silently **excluded from every
installed copy of the vesper package**. `setuptools` only bundles `.py` files for a package
found via `[tool.setuptools.packages.find]` unless `package-data` says otherwise; vesper's
`pyproject.toml` had no such entry, so `pip wheel .` produced a wheel with `config/settings.py`
but no `config/settings.yaml` (also silently dropped: `config/persona.md`, the cause of this
session's unrelated "could not read persona file" warnings, and `config/formats/reel.md`).
With the YAML missing, `config.settings.load_settings()` fell through entirely to
`LLMTierSettings`'s Pydantic class default — a stale `fallback.model="qwen3.5:latest"`
(6.6GB) that predates the `f96be6a` fix and was never removed from `settings.py` once the
YAML became the real source of truth. This is exactly why it only showed up in vantage's
eval harness (which installs vesper as a `pip`/`uv` git dependency into its own venv,
`packages/eval_engine`'s `vesper @ git+...@vesper`) and not in real day-to-day Vesper usage
(which runs in-place from the checkout, where `settings.yaml` sits right next to
`settings.py` and loads correctly).

**Reproduced** with `scripts/probe_ollama_fallback.py` (added in the fix commit) calling the
real `OllamaProvider` class directly: 6/6 calls to `qwen3.5:latest` timed out at ~60-63s
regardless of free RAM (0.23GB-2.16GB across attempts, all below or near the "37.8s cold"
figure `router.py`'s own comment measured at 1GB free); `llama3.2:3b` succeeded cleanly in
7.8s every time. **Fix**: `[tool.setuptools.package-data]` entry bundling `config/*.yaml`,
`config/*.md`, `config/formats/*.md` (vesper commit `911410e` on the `vesper` branch).
**Verified**: rebuilt vesper's wheel, reinstalled it into vantage's own venv, confirmed
`config.settings.load_config_dict()` now resolves `llama3.2:3b` (was `qwen3.5:latest`), and
a direct `OllamaProvider` call from that venv succeeded in 7.8s. No Vantage eval run was
triggered to verify end-to-end — Groq's planner quota was exhausted from the same day's
testing (item 5) — real end-to-end verification is the 2026-09-28 baseline collection run,
which will now install the fixed vesper package via the same `uv pip install` path the CI
eval-gate workflow uses.

## 5. Groq free-tier planner model has a 200k TPD ceiling that caps daily CI throughput

New operational finding, 2026-09-27. Four full 40-scenario real-Vesper runs in one day
(1 fix-verification run + 2 completed samples + 1 partial sample before it was killed for
system memory pressure — roughly 160+ planner calls, more once OTPM 429 retries and
PLANNER_FAILURE retries are counted) exhausted the planner model's (`openai/gpt-oss-120b`)
Groq daily quota: 199,628/200,000 TPD used, confirmed via the router's own 429 log
("Rate limit reached... on tokens per day (TPD): Limit 200000, Used 199628"). A 5th run
attempt was stopped mid-flight rather than let it grind through PLANNER_FAILURE/Ollama-
fallback noise (item 4, this file) contaminating whatever it was trying to measure.

This is a distinct limit from item 3's judge-model OTPM cap — different model, different
axis (daily total vs. per-minute burst), and not something `fix(eval): right-size judge
max_completion_tokens` (`322b6d1`) touches. **When Ollama fallback is broken (item 4), this
hard-caps real-Vesper CI throughput at roughly 3-4 full 40-scenario runs per day**, full
stop, regardless of pacing or retry logic — there's no graceful degradation once TPD is hit,
every remaining scenario in a run either waits out a sliding-window gap of unpredictable
length or falls through to a fallback that fails ~53/53 observed times under load.

Options for Week 5, same shape as item 4's: (a) fix Vesper's Ollama fallback so TPD
exhaustion has a real rescue path instead of open-ended stalls, (b) a Groq paid tier for the
planner model specifically, (c) migrate the planner to a self-hosted model, removing the
third-party quota dependency entirely. Combined with item 3's judge-side OTPM finding, this
is now two independent, measured reasons (not just one) that a third-party free-tier API
can't reliably power CI at scale — direct evidence for the Week 5 fine-tuned-judge/self-
hosted-planner case being about reliability and deployment control, not only quality.

## 6. `eval_suites.description` is `VARCHAR(512)` — `orchestrator_v1_smoke` has never persisted a run to Postgres

Discovered while verifying the trajectory-persistence fix (item 1), 2026-09-27. `EvalSuite.
description` is capped at 512 characters; `orchestrator_v1_smoke`'s real description exceeds
that. Every attempt to persist a run of that suite raises `asyncpg.exceptions.
StringDataRightTruncationError` inside `_upsert_suite`'s insert/update, which `persist_run`
catches and logs as a warning — the eval run itself still completes and prints an accurate
scorecard from the in-memory results, so this has been silent: nothing about a normal
`vantage eval run` invocation looks broken.

**Why this is a real Week 5 opening blocker, not a footnote**: `orchestrator_v1_smoke` (10
scenarios, 2 per category) is what Vesper's `.github/workflows/eval-gate.yml` actually runs
for CI (see `docs/vesper_routing_surface.md`). That workflow currently gates against a
committed `.github/eval-baseline.json` file, not a live Postgres comparison, so CI itself is
NOT broken by this today — the gate only needs the `--output` JSON, which writes successfully
regardless of the DB failure. But it means **this suite has never once landed in Postgres**:
no dashboard history for it, no DB-backed `vantage eval compare` against a marked baseline for
it, no `set-baseline` API call would ever find a persisted run to mark. Any Week 5 plan that
moves CI gating from "compare against a committed JSON" to "compare against a Postgres-marked
baseline" (the more capable, dashboard-visible version of the same idea) will hit this
immediately and non-obviously, because the failure has no visible symptom in a normal run —
only `persist_run`'s logged warning, which nothing currently surfaces to a CI log reader's
attention. Fix is small (widen the column, or truncate/hash long descriptions before storing)
but needs a migration like item 1's; not done here since it's orthogonal to trajectory
persistence.

## 7. ~~Groq/Ollama fallback ratio is now the dominant baseline variance source~~ — Option (b) implemented, vesper `bd19fc5` + vantage `a73f675`

Three fresh samples on identical code (`orchestrator_v1` baseline collection, 2026-09-28 —
see `docs/baselines/orchestrator_v1_baseline_20260928.md`) produced 60.0% / 69.2% / 38.5%
adjusted pass rates: run `a41a1d77` (baseline, marked), `28c70401`, and `5124cac4`. Those
numbers correlate directly with each run's count of Ollama fallback invocations: 12, 17, and
**53** respectively. **Sample 2 (`28c70401`, n_fallback=17, judge_error=0) is the cleanest
snapshot of Vesper's actual routing quality available so far — every other sample is measuring
some mixture of Vesper-on-Groq and Vesper-on-`llama3.2:3b`,** and the mixture ratio is itself
driven by how hard Groq happened to be throttling that morning, not by anything about the code
under test.

Concrete evidence the fallback model's routing is materially worse, not just marginally
noisier: `docs/interview_exhibits/fallback_model_routing_degradation.md` — three scenarios
from the 53-fallback sample where `llama3.2:3b` routed to `git_diff` for "turn off the lights"
(then hallucinated success), `run_shell` for a unit conversion the LLM could answer directly,
and `current_weather` with a broken location entity — each contrasted against the same
scenario handled cleanly by Groq in the baseline sample.

**Options, in the order Week 5 should evaluate them**:
(a) **Upgrade Groq to Developer tier** (~$10-30/mo) for materially more TPM/TPD headroom —
would collapse most fallback hits to zero and stabilize the number directly, at the cost of a
recurring bill for what's currently a $0 eval pipeline.
(b) **Scope the fallback narrower** — never route to Ollama for `purpose="planning"` specifically
(Vesper's `ModelRouter._resolve_tier` already supports per-purpose overrides; see
`config/settings.yaml`'s `purposes:` block), since `llama3.2:3b`'s routing decisions are the
demonstrated weak point, not its chat-purpose replies. Keeps the $0 cost, accepts that a
Groq-planning outage has no rescue path (a regression from the fix that made this session
possible, so this is a real trade-off, not a free option).
(c) **Accept the variance and always report a range, not a point** — cheapest to implement
(the baseline doc already does this), but means the pass-rate number can't be trusted for
small regressions without also checking each comparison run's fallback count, which nothing
currently surfaces prominently (`vantage eval compare` doesn't report it today).

**This should be the first decision Week 5 makes**, before any further baseline collection or
comparison against `a41a1d77` — it changes what every subsequent number actually measures.

**Resolved 2026-09-28, option (b)**: `llm.fallback.purposes: ["reflection"]` in vesper's
`config/settings.yaml`, enforced by `ModelRouter.complete()` (vesper `bd19fc5`) — Ollama is no
longer attempted at all for `purpose="planning"`, the only purpose vantage's eval harness
exercises; a Groq failure now returns `PLANNER_FAILURE` cleanly, which vantage's pipeline
(`a73f675`, item 2) excludes from the pass-rate denominator rather than scoring it. Verified
end to end with a bogus `GROQ_API_KEY`: zero Ollama calls, clean `PLANNER_FAILURE` across all
retry attempts. `docs/baselines/orchestrator_v1_baseline_20260928.md`'s "Methodology change"
section has the recomputation showing this has zero effect on the three existing samples
(they predate the fix) and the plan for the first sample that exercises it for real.
**"summarization" → "reflection"**: item 2's own earlier text (and this fix's initial task
description) referenced a `purpose="summarization"` — no such purpose is called anywhere in
the codebase. The one real, already-existing non-planning purpose is `"reflection"`
(`proactive/reflection.py`'s background self-review), used instead.

## 8. Pre-run Groq quota probe is insufficient — "probe passed, run wasted"

Discovered 2026-09-28: a single cheap 10-token probe call to the planner model returned 200
OK immediately before a real 40-scenario run — and the run still failed almost entirely,
because the daily quota had only ~867 tokens of headroom left (199,133/200,000 used) at the
moment the probe succeeded. The probe proves the endpoint is reachable and not currently
rate-limited; it says nothing about whether there's enough *remaining* quota for the run
about to start, since a single small request can fit in a gap too thin for the other ~39
calls that follow it. Net effect: a real run gets launched, burns real wall-clock time and
whatever quota is left, and produces unusable data — worse than not probing at all, since the
probe's success created false confidence.

**Better probe**: Groq's response headers (worth confirming the exact header names against a
live response before relying on this — not yet done — but the standard OpenAI-compatible
convention is `x-ratelimit-remaining-tokens` and `x-ratelimit-remaining-requests`) should carry
remaining-quota information without needing to trigger a 429 to see a "Used" figure. A HEAD or
minimal request against the primary planner model, reading that header and aborting if
remaining tokens fall under a real threshold, would catch this before wasting a run, not after.

**Threshold measured, not guessed**: summing every successful planner call's
`tokens_in + tokens_out` across the three clean 2026-09-28 baseline samples gives a real full
`orchestrator_v1` run's actual cost — 75,645 / 78,926 / 59,772 tokens (`a41a1d77` / `28c70401`
/ `5124cac4`). A first draft of this item guessed "~40k" before that data existed; the real
worst case (~79k) is roughly double that guess, so 40k would have let a run start with barely
half the headroom it needs, reproducing this exact failure mode at a smaller scale. Corrected
threshold: **abort below ~85,000 remaining tokens**. Full writeup with the per-run numbers:
`docs/interview_exhibits/quota_aware_ci.md`.
