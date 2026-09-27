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

Discovered along the way, unrelated to this fix and NOT touched: `eval_suites.description` is
`VARCHAR(512)`, and `orchestrator_v1_smoke`'s real description exceeds that — every attempt to
persist a run of that suite has been silently failing (`persist_run` catches and logs the
error, so the eval run itself completes and reports its scorecard normally; only the DB write
is lost). Worth a real look, but out of scope for this task.

## 2. Skip the LLM judge on agent-side infrastructure failures

Already true today, noted here so it isn't lost alongside the new item below: when
`VesperAdapter` returns `PLANNER_FAILURE`, `ADAPTER_ERROR`, or a transient-error sentinel,
`runner._run_one` still hands that output to the judge, which grades non-answers as if they
were real routing decisions. A structural failure (router exhaustion, a crash in this
adapter) isn't a quality question and shouldn't consume a judge call or produce a quality
score at all — it should short-circuit straight to `passed=False` with a reason that says
"infrastructure," distinguishable in reports from "the agent tried and was wrong."

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
