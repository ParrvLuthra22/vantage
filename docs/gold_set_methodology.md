# Gold set methodology

Week 5's ground truth for measuring judge quality: a human-labeled sample of real
`orchestrator_v1` scenario results, independent of the LLM judge (Qwen `qwen/qwen3.8-27b`)
whose agreement with it is what's actually being measured. See
[`docs/deferred_for_week5.md`](deferred_for_week5.md) for why this exists — comparing a
fine-tuned judge to its Groq-Qwen teacher measures imitation, not correctness; only a
genuinely independent human label can measure correctness.

## Label schema

One label per `(result_id)` — a specific scenario's outcome in a specific real eval run,
not just the scenario in the abstract, since the same scenario can behave differently run
to run (temperature 0.3).

| field | type | meaning |
|---|---|---|
| `result_id` | string (UUID) | The `eval_results.result_id` this label is for. |
| `scenario_external_id` | string | e.g. `clear_005` — for readability without a DB join. |
| `score` | int, 1-5 | Same scale the judge produces, so scores are directly comparable. |
| `failure_class` | enum, see below | The richer signal beyond the score: decomposes disagreement into "disagreed on severity" vs. "disagreed on what kind of thing happened." |
| `reasoning` | string | 3-5 sentences explaining the score. |
| `confidence` | int, 1-3 | 1 = hard call, 3 = easy call. Low-confidence labels are exactly the ones worth a second labeler's opinion before trusting them as ground truth. |
| `notes` | string or null | Anything that doesn't fit the other fields — edge cases, ambiguity in the rubric itself, disagreement with the *scenario's* expected value rather than the judge. |
| `labeler` | string | Always `"parrv"` for this pass. A second labeler on the same rows later would let inter-rater agreement be measured, which single-labeler ground truth can't do. |
| `labeled_at` | string (ISO 8601, UTC) | When the label was recorded. |

### `failure_class` values

`hallucination`, `wrong_agent`, `wrong_entities`, `latency_over_budget`,
`refused_correctly`, `refused_incorrectly`, `correct`, `defensible_alternative`,
`judge_confused`.

This list is the Step-1 design, not yet validated against real labels — Step 4 of the
original task plan calls for checking, after the first 10, whether these classes actually
cover what a labeler sees in practice (e.g. if none of the first 10 need
`defensible_alternative`, or half need a class that isn't here at all) and adjusting before
scaling to the full set. **That validation has not happened yet — see "Status" below.**

## Sampling strategy: 3 buckets, not a flat random sample

Scenario × sample variety matters more than raw count: a gold set that's 120 near-identical
"chat_agent gave a reasonable-sounding reply" rows would calibrate nothing. Three buckets,
classified from the three canonical 2026-09-28 baseline samples
(`docs/baselines/orchestrator_v1_baseline_20260928.md`), each requiring ≥2 real (non-judge_error,
non-infra_error) data points to be classified as stable in either direction — a symmetric bar:

- **stable_failure** (target 60): scenarios that failed in ≥2 of the 3 samples. These are
  the 14 scenarios documented in the baseline doc's own "stable vs. one-off" breakdown —
  real, reproducible gaps, not sample noise. Highest-value bucket for judge-quality
  measurement: if the judge can't correctly grade a *consistent* failure, that's a real
  judge defect, not a coin flip.
- **stable_pass** (target 30, 2 per scenario): scenarios with zero failures across every
  real data point. Calibration coverage — confirms the judge isn't just bad at hard cases,
  it also needs to correctly recognize when Vesper got it right.
- **one_off** (target 30): scenarios with exactly one failure among real data points (a
  genuine flip between samples), or too few real data points to call either way. The
  "ambiguous middle" — this is where a labeler's own judgment is most likely to be a
  genuine coin flip too, and where judge-vs-human disagreement is most informative about
  *why* the judge and a human see it differently, not just *whether*.

Candidate rows are pulled from every real-Vesper `orchestrator_v1` run with trajectory data
actually persisted (`tool_sequence`/`final_reply` non-null) — not just the 3 classification
runs — for trajectory diversity, then deduplicated by
`(scenario_id, tool_sequence, final_reply)` so a byte-identical trajectory across samples
(common at temp 0.3 for simple scenarios) doesn't waste a label slot. Within each bucket,
selection round-robins across scenarios rather than pooling and sampling flatly, so a
scenario with many unique trajectories can't crowd out one with few.

Infra-sentinel rows (`PLANNER_FAILURE`/`ADAPTER_ERROR` — see
[`deferred_for_week5.md`](deferred_for_week5.md) item 2) are excluded from candidates
entirely: there's no routing decision to label a score against. Detected via a proxy, since
`routed_agent` itself was never persisted as its own column — see
`scripts/select_gold_candidates.py`'s `is_infra_sentinel` docstring for the exact signal and
a correction made while building it (an earlier, weaker version of the proxy was wrong).

### Real shortfall: 91 candidates, not 120

The target was 120 (60/30/30). Only **91** unique candidates exist in the data actually
available: **40/60 stable_failure, 21/30 stable_pass, 30/30 one_off**. This is an honest
count from real data, not padded to hit the target — only 4 real-Vesper runs have
trajectory data persisted at all (`a41a1d77`, `28c70401`, `5124cac4` from the 2026-09-28
baseline, plus `a53862ef`, a same-day fallback-fix verification run dominated by
`PLANNER_FAILURE` and mostly excluded by the infra-sentinel filter). `stable_pass` is short
because only 11 scenarios ever qualify (21 = 11 scenarios × up to 2 each, capped), and
`stable_failure` is short because 14 scenarios × up to 4 runs each, after dedup, tops out at
40. Closing the gap needs either more real baseline runs accumulating trajectory data over
time, or a deliberate decision to relax the dedup or widen `CANDIDATE_ROW_RUN_IDS`.

## Labeling tool

`scripts/label_gold_set.py` — an interactive CLI reading `gold_candidates.jsonl` and
appending to `gold_labels_v1.jsonl`. Resumable by design: each label is written and flushed
immediately, and a `result_id` already present in the labels file is skipped on the next
run, so a `[q]uit` or an actual Ctrl-C loses at most the label in progress, never anything
already recorded. Covered by `packages/eval_engine/tests/test_label_gold_set.py` (schema
correctness, skip behavior, resumability, a simulated real `KeyboardInterrupt`, the
`--limit` option, and the missing-candidates-file error path) — all against temporary
files, never the real gold-set data.

## Status

- [x] Schema designed (this doc).
- [x] 120-target candidate selection built and run: **91 real candidates** in
      `packages/eval_engine/data/gold_candidates.jsonl` (see shortfall note above).
- [x] Labeling CLI built and tested (mechanically — schema, resumability, Ctrl-C safety).
- [ ] **Pilot batch of 10 real labels — not done.** This requires an actual human
      labeler's judgment attributed as `parrv`; see the open question below before this
      proceeds.
- [ ] Failure-class enum validated/adjusted against real pilot labels.
- [ ] Reasoning-format decision (free 3-5 sentences vs. a structured template) — deferred
      to the same pilot.

### Who labels the pilot 10 — resolved

The gold set's entire value depends on `labeler: "parrv"` meaning a real, independent human
judgment — the whole point is to be uncorrelated with any LLM's judgment, including the one
that would otherwise be doing the labeling. An AI-generated label attributed to `parrv`
would not be that, regardless of how carefully reasoned, and would quietly undermine every
downstream measurement built on this gold set (judge-agreement scoring, the Phi-3.5 fine-tune
comparison). Raised explicitly rather than defaulted: **parrv labels the pilot 10 himself**,
using the CLI built here:

```bash
python scripts/label_gold_set.py --limit 10
```

Once that's done, the pilot-calibration questions from the original task plan are still
open and are for parrv to answer from the real labels, not something to pre-guess here:
do the 9 `failure_class` values actually cover what shows up in practice, is 3-5 sentences of
free-text reasoning the right format or would a structured template calibrate better, and are
any of the 10 genuine coin flips worth flagging as their own Week 5 exhibit.
