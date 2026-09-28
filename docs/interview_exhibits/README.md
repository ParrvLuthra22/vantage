# Interview Exhibits

Concrete evidence of what Vantage's evaluation infrastructure has caught in real Vesper runs.
These are not synthetic examples — every exhibit references a real Postgres `run_id` from a
real 40-scenario suite execution against real Vesper.

## Hallucination detection (3 instances of the same failure class)

The pre-measurement-fix judge input format made an entire class of failure invisible: agent-
hallucinated action completion. Three independent examples caught after `1948256`:

- [`hallucination_caught_by_measurement_fix.md`](hallucination_caught_by_measurement_fix.md) —
  `clear_001`. Vesper called `get_date()` then fabricated "Your meeting with Priya has been
  scheduled." Judge caught the hallucination once it could see the reply.
- [`hallucination_clear_005_email.md`](hallucination_clear_005_email.md) — `clear_005`.
  `chat_agent` asked for email details "so I can send the email promptly" when Vesper has no
  email tool.
- [`hallucination_clear_009_reminder.md`](hallucination_clear_009_reminder.md) — `clear_009`.
  `chat_agent` claimed "Your reminder to call Mom has been set" when Vesper has no reminders
  tool (a second sample of the same scenario shows the subtler, hedged version of the same
  failure — a question implying capability rather than a flat claim).

The generalization: measurement infrastructure that hides part of the agent's behavior from
the judge will systematically miss hallucination-class failures. The three exhibits are
separate concrete cases of that pattern, not one lucky catch.

## A/B evidence of the measurement fix (deterministic)

- [`measurement_fix_ab_evidence.md`](measurement_fix_ab_evidence.md) — `context_dependent_004`.
  Same scenario, same real Vesper behavior across four runs. Pre-fix judges scored it 1/5
  twice, with byte-identical reasoning. Post-fix judges scored it 5/5 twice. The judge input
  changed; the agent behavior didn't. The cleanest available proof the fix corrected a real
  measurement gap rather than just re-scaling noise — and that it corrects false negatives
  (this exhibit) as well as false positives (the three hallucination exhibits above).

## Fallback model quality (1 exhibit, 3 scenarios)

- [`fallback_model_routing_degradation.md`](fallback_model_routing_degradation.md) —
  `clear_002`, `clear_008`, `out_of_scope_006`. When Groq's primary planner is throttled hard
  enough to push a turn onto Vesper's local Ollama fallback, `llama3.2:3b`'s routing quality is
  materially worse, not just marginally noisier — including one case (`out_of_scope_006`)
  where it routed "turn off the lights" to `git_diff`, a version-control tool, then
  hallucinated success. Each scenario is contrasted against the same input handled cleanly by
  Groq in a different sample. Direct evidence behind `docs/deferred_for_week5.md` item 7: the
  Groq/fallback ratio, not code changes, is now the dominant source of variance between eval
  samples.

## What these support in an interview

- **"How do you know your eval infrastructure catches real bugs?"** → these five exhibits,
  three of them independent instances of the same hallucination failure class.
- **"How did you validate that a measurement change was correct, not just a re-scaling of
  noise?"** → the A/B evidence file — same behavior, same rubric, same judge model, opposite
  verdicts, explained entirely by what the judge could see.
- **"How did you diagnose the measurement problem in the first place?"** → the A/B file
  documents the byte-identical-judge-reasoning-across-runs observation that surfaced it: two
  separate real runs producing word-for-word identical scoring reasoning is a strong signal
  the judge is reasoning from incomplete, deterministic input rather than actually observing
  the agent.
- **"Why does your baseline number have a range instead of a point estimate?"** → the fallback
  model quality exhibit — the range isn't measurement noise, it's a real, explained mixture of
  two different models doing the routing depending on Groq's load that morning.

## What's NOT here (and why)

- **Screenshots of dashboards.** Raw text over screenshots for grep-ability and diff-ability.
  Add screenshots when a specific interview needs the visual.
- **The initial 20% first-real-run evidence.** That's `docs/baselines/
  orchestrator_v1_pre_iteration_20260915.log` — a separate exhibit for "how iteration and
  the baseline process work," not for "what the infrastructure catches."

## Checklist

All exhibits referenced above exist and are linked correctly as of this writing — nothing
below is a placeholder.

- [x] `hallucination_caught_by_measurement_fix.md` (`clear_001`)
- [x] `hallucination_clear_005_email.md` (`clear_005`)
- [x] `hallucination_clear_009_reminder.md` (`clear_009`)
- [x] `measurement_fix_ab_evidence.md` (`context_dependent_004`)
- [x] `fallback_model_routing_degradation.md` (`clear_002`, `clear_008`, `out_of_scope_006`)

No candidate exhibits currently pending. The 2026-09-28 baseline run referenced above is now
`docs/baselines/orchestrator_v1_baseline_20260928.md`, and its most interview-relevant finding
(the fallback-model quality gap) already has its own exhibit, linked above.
