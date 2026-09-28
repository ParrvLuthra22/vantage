# `orchestrator_v1` baseline — 2026-09-28

**Baseline: 60.0% (18/30)** — run `a41a1d77-ce26-4860-99fe-2650a0fa7d1e`, marked via
`POST /evals/runs/{run_id}/set-baseline`.

This is the median of three fresh samples collected the same morning, immediately after the
Ollama-fallback packaging fix (vesper `911410e`) and the two judge-measurement fixes
(vantage `de18d48`, `322b6d1`) shipped the night before. Not an aspirational number — it's
what the suite actually returned, honestly reported including a wide spread across the three
samples (see below).

## The three samples

| Sample | Run ID | Adjusted pass rate | Raw pass rate | judge_error | known_failing |
|---|---|---|---|---|---|
| 1 (median → **baseline**) | `a41a1d77-ce26-4860-99fe-2650a0fa7d1e` | 60.0% (18/30) | 45.0% (18/40) | 9 | 1 |
| 2 | `28c70401-f2e7-48f4-83db-7e536c0f08ab` | 69.2% (27/39) | 67.5% (27/40) | 0 | 1 |
| 3 | `5124cac4-0dbe-4f70-8838-89dcb9cad4a0` | 38.5% (15/39) | 37.5% (15/40) | 0 | 1 |

Range: **38.5% – 69.2%**, a 31-point spread. This is a genuinely wide range and it isn't
hidden by picking the median — see "Why sample 3 is the outlier" below for what actually
drove it, which is a real, explainable, structural cause, not unexplained noise.

## Methodology

- **Adjusted pass rate** (the number that matters) excludes both `known_failing` scenarios
  (currently `clear_010` only, tagged against a filed, confirmed Vesper bug —
  [vesper#6](https://github.com/ParrvLuthra22/vesper/issues/6)) and `judge_error` scenarios
  (the judge's own API call failed or returned nothing — not a real verdict on the agent) from
  the total/passed/failed denominator. This methodology is baked into the pipeline itself as
  of commit `de18d48`, not computed after the fact.
- **Raw pass rate** (`passed / 40`) is reported alongside for transparency — it's what the
  pass rate would be under the old methodology, which counted judge infrastructure failures as
  real failures. The gap between raw and adjusted for sample 1 (45.0% → 60.0%, a 15-point
  swing from 9 judge_error exclusions) is itself evidence for why that fix mattered.
- Effective denominator is 39 (40 minus `clear_010`) in samples 2 and 3, and 30 in sample 1
  (39 minus 9 additional judge_error exclusions).
- All three runs used the real `VesperAdapter` against real Vesper, Groq judge
  (`qwen/qwen3.8-27b`), `--judge-provider groq`, 120-second gaps between runs.

## Why sample 3 is the outlier — and why it's real signal, not noise to explain away

Sample 3's Ollama-fallback invocation count was **53**, versus 12 (sample 1) and 17
(sample 2) — roughly 3-4x heavier. Groq's primary planner model was throttled far more
aggressively during this run, forcing a much larger fraction of turns onto the local
`llama3.2:3b` fallback (fixed the night before in vesper `911410e` — before that fix, these
calls would have timed out entirely rather than degrading gracefully). A 3B local model makes
noticeably worse routing decisions than the primary Groq model: sample 3 shows several
scenarios routing to tools that don't fit the request at all (`run_shell`, `git_diff`,
`current_weather` for requests that don't call for them) and a visibly confused reply on
`context_dependent_004` — the exact scenario
[`measurement_fix_ab_evidence.md`](../interview_exhibits/measurement_fix_ab_evidence.md)
documents as reliably scoring 5/5 in every other sample collected so far, pre- and post-fix.

This is a genuinely new finding, not previously visible: **fixing the Ollama fallback removed
the "infinite timeout" failure mode, but it did not make the fallback model as capable as the
primary.** Heavy Groq throttling now degrades routing quality gracefully instead of hanging —
which is strictly better — but it means pass rate is sensitive to how much of a given run
happens to run on the fallback, which is itself sensitive to Groq's load at the time. Worth a
Week 5 look: is `llama3.2:3b` good enough for planning-quality tool selection at all, or does
the fallback need a bigger local model (trading cold-load risk against routing quality) or a
narrower fallback strategy (e.g., only for `chat`-purpose turns, never `planning`)?

## Stable vs. one-off failures across the three samples

A scenario counts as a real failure in a sample only when `passed=false` and
`judge_error=false` for that sample (a `judge_error` sample contributes no data point for
that scenario, rather than counting as pass or fail).

**Stable (failed in 2 or 3 of 3 samples, excluding `clear_010` which is separately tracked
as known_failing)**: `adversarial_006`, `clear_001`, `clear_003`, `clear_004`, `clear_005`,
`clear_007`, `clear_009`, `context_dependent_001`, `context_dependent_002`,
`context_dependent_005`, `context_dependent_006`, `out_of_scope_001`, `out_of_scope_004`,
`out_of_scope_006` — 14 scenarios. This is the real gap between Vesper's current behavior and
this suite's expectations, independent of which sample you'd pick.

**One-off (failed in exactly 1 of 3, or 1 of the samples that actually had a judge verdict)**:
`adversarial_002/003/004/005` (all sample-3-only; sample 1's readings for these were
`judge_error`, so only sample 2 vs. sample 3 is a real comparison, and only sample 3 failed
them), `ambiguous_002`, `ambiguous_007`, `ambiguous_009`, `clear_002`, `clear_006`,
`clear_008`, `context_dependent_003`, `context_dependent_004`, `out_of_scope_002`,
`out_of_scope_007`. Ten of these twelve are sample-3-exclusive, concentrating almost all of
the one-off noise in the one sample that ran through the heaviest fallback load — consistent
with the fallback-quality explanation above rather than 12 independently flaky scenarios.

## What this baseline is and isn't

It's an honest number from a real measurement, on a pipeline that just had two real
measurement bugs and one real infrastructure bug fixed the night before. It is **not**
evidence that Vesper's routing quality is inherently ~60% — sample 2 alone would have said
69%, and the 14 stable failures above are the more meaningful long-term signal than any single
sample's aggregate. Future comparisons (`vantage eval compare`) against this baseline should
be read with that spread in mind, not as if 60.0% were a precise measurement with tight error
bars.

See [`docs/interview_exhibits/README.md`](../interview_exhibits/README.md) for the concrete
hallucination-detection and measurement-fix-validation exhibits this baseline sits downstream
of, and [`docs/deferred_for_week5.md`](../deferred_for_week5.md) for the open items this
session's fixes didn't close (Neon prod migration, the `eval_suites.description` truncation
bug, and now this fallback-quality question).

## Methodology change (2026-09-28, same day)

Two more fixes landed the same day this baseline was marked, both aimed at closing the
ambiguity in "Why sample 3 is the outlier" above before Week 5's judge-agreement work builds
on top of this number:

- **vesper `bd19fc5`**: Ollama's fallback is now scoped to `purposes: ["reflection"]` —
  it is never attempted for `purpose="planning"` (the only purpose this suite exercises).
  A Groq failure now returns `PLANNER_FAILURE` cleanly instead of silently routing through
  the demonstrated-weaker `llama3.2:3b` (see
  [`fallback_model_routing_degradation.md`](../interview_exhibits/fallback_model_routing_degradation.md)).
  This is `docs/deferred_for_week5.md` item 7, option (b).
- **vantage `a73f675`**: `PLANNER_FAILURE`/`ADAPTER_ERROR` outputs now skip the judge entirely
  and are excluded from the pass-rate denominator (`ScenarioResult.infra_error`), the same way
  `known_failing`/`judge_error` already were. Without this, scoping the fallback away from
  planning would have traded one measurement problem for another: a Groq outage would produce
  `PLANNER_FAILURE`s that still got judged and counted as real failures (confirmed the same
  morning — a verification attempt hit an exhausted Groq daily quota and drove 34/40 scenarios
  to `PLANNER_FAILURE`, one of which scored 5.0 by coincidence before this fix). This is
  `docs/deferred_for_week5.md` item 2.

**Recomputing the three samples above under both fixes** (`scripts/
recompute_baseline_under_new_methodology.py`, which queries Postgres rather than spending
Groq tokens on a fresh run) shows **zero effect on the numbers already reported**:

| Run | Old (stored) | New (infra_error also excluded) | infra-sentinel candidates |
|---|---|---|---|
| `a41a1d77` (baseline) | 60.0% (18/30) | 60.0% (18/30) | 0 |
| `28c70401` | 69.2% (27/39) | 69.2% (27/39) | 0 |
| `5124cac4` | 38.5% (15/39) | 38.5% (15/39) | 0 |

This is expected, not a validation gap: all three samples were collected *before* `bd19fc5`
landed that same morning, so every Groq failure that day was still rescued (however badly) by
the unscoped fallback — none of the three ever actually produced a `PLANNER_FAILURE`/
`ADAPTER_ERROR` sentinel for the new methodology to exclude. **The 60.0% median above remains
the historical, canonical baseline, unmarked and unchanged** — this section documents that the
methodology under it has since improved, not that the number itself moved.

**The zero delta is itself a substantive finding, not just a null result.** Yesterday's
unscoped Ollama fallback meant Groq failures were rescued by `llama3.2:3b` decisions that
still counted as scenarios in the denominator — the `PLANNER_FAILURE` sentinel item 2's fix
acts on essentially never fired under the old fallback config. Tomorrow's sample is therefore
the first measurement under a pipeline that cleanly distinguishes "Vesper couldn't decide"
from "Vesper decided wrong."

**Next**: one fresh sample scheduled for 2026-09-29 09:00 local, the first to run under both
fixes together — the first genuinely clean measurement this suite has had. It will **not** be
marked as baseline; `a41a1d77` remains canonical until a deliberate decision to replace it.
