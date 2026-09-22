# Deferred to Week 5

Real findings from the P.repair.3 measurement-layer fix (2026-09-21/22, commit `1948256`)
and its validation samples, deliberately not acted on now — scoped out to keep that fix to
"measurement layer only," per an explicit instruction not to re-tune anything else while
establishing the baseline. Each item names the evidence that surfaced it.

## 1. Trajectory persistence in Postgres

`AgentOutput.tool_sequence` / `.tool_calls` / `.final_reply` (added in `1948256`) only reach
Postgres via a `SuiteRun`'s `--output` JSON dump — `vantage_eval/persistence.py`'s `eval_results`
write does not carry them. That means the dashboard and `vantage eval compare` can't show the
full trajectory the judge now sees, only the first-tool `routed_agent` it always could. Needs
a column (likely JSONB, mirroring `deterministic_scores`) plus a migration. Low risk, not
urgent — the JSON output already gives us this for analysis; it's the *dashboard* that's
behind.

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

## 4. Diagnose Vesper's Ollama fallback failing under load

`ModelRouter`'s Ollama fallback (`qwen3.5:latest`) has failed **53 of 53** observed attempts
across the two full runs on 2026-09-21 night and 2026-09-22 (49 during a Groq
tokens-per-day exhaustion, 4 more the next day under an ordinary tokens-per-minute 429) —
always "Ollama network error", no other message. This is not Ollama being unreachable:
`curl localhost:11434/api/tags` answers fine standalone, every time checked. Something about
*how the router invokes Ollama* — under load, or specifically right after a Groq 429 — is
failing. Effect: every Groq rate-limit hit currently costs the full ~60s Ollama timeout with
no real rescue, which is why `context_dependent_004` took 75.6s in sample 1 despite a 20s
budget that assumed a working fallback. This matters for eval-gate CI reliability once that's
turned on for real — a flaky fallback under load is exactly the condition CI runs will hit.
Out of scope for the eval harness itself (this is Vesper-side, in `llm/router.py`'s Ollama
client path, not anything in `packages/eval_engine`), but worth a dedicated look before
leaning on CI gating in production.
