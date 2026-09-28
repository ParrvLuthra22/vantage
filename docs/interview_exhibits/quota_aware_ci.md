# Exhibit: a quota probe that passes and still wastes the run

**Finding date:** 2026-09-28, while verifying the purpose-scoped Ollama fallback fix
(vesper `bd19fc5`). **Follow-up correction:** the threshold this exhibit originally proposed
was itself an unverified guess, refined below once real measurements existed — a small
second instance of the same "measure, don't guess" discipline the finding is about.

## The problem

Before launching a real 40-scenario `orchestrator_v1` run, the standing precaution was a
single cheap probe call to the planner model (`openai/gpt-oss-120b`, 10 tokens) — if it
returned 200 OK, the run proceeded. It did. The run then failed almost entirely: 34 of 40
scenarios hit `PLANNER_FAILURE`.

The log's own first 429 explains why: Groq's daily quota was at

```
Used 199133/200000 (867 tokens remaining)
```

*before the run's first real scenario even started.* The probe's 10-token request fit
inside that 867-token gap and returned success — proving only that the endpoint was
reachable and not *currently* rate-limited, saying nothing about whether ~40 more calls
behind it had anywhere to land. The probe passed; the run was already doomed.

## What a real run actually needs (measured, not assumed)

The original write-up of this finding (`docs/deferred_for_week5.md` item 8) proposed an
abort threshold of "~40k tokens" for the same reason it's flagging here — a plausible-sounding
number nobody had actually measured yet. Once three clean, non-quota-blocked
`orchestrator_v1` runs existed (the 2026-09-28 baseline samples), the real number was
available by summing every successful planner call's `tokens_in + tokens_out`:

| Run | Planner calls | Total tokens used |
|---|---|---|
| `a41a1d77` | 53 | 75,645 |
| `28c70401` | 55 | 78,926 |
| `5124cac4` | 56 | 59,772 |

**A real full run costs roughly 60,000-79,000 planner tokens**, not the ~40k originally
guessed — that guess would have let a run start with barely half the headroom it actually
needs, reproducing this exact failure mode at a smaller scale. The corrected threshold:
**abort below ~85,000 remaining tokens**, giving real runs their measured worst case
(~79k) plus a margin for the retry traffic a partially-degraded run generates.

## The fix

A single small probe request can't answer "is there enough quota left," because success is
binary and quota remaining is continuous. The fix is to read it directly: Groq (like other
OpenAI-compatible APIs) returns rate-limit state in response headers on every request —
`x-ratelimit-remaining-tokens` is the candidate (header names should be confirmed against a
live response before this is implemented; not yet done). A pre-run check becomes:

```
make one minimal request to the primary planner model
read x-ratelimit-remaining-tokens from the response headers
if remaining < 85_000: abort before launching the real run, report the exact number
```

## Why this matters for eval-gate CI specifically

A probe that can pass with almost no real headroom left is a **false-negative generator**
once CI gating is live, not just a wasted local run. A pull request opened while the day's
quota is low would see its eval-gate job run, mostly fail via `PLANNER_FAILURE`, and —
before `docs/deferred_for_week5.md` item 2's fix — get scored as a real regression. Even with
item 2's fix (infra failures excluded from the denominator), a run that's 85% infra-failed
produces a `total` denominator of ~6 scenarios instead of 39, too thin to say anything
reliable about the PR. Either way, the PR looks broken because of exhausted quota, not
because of anything in the diff — exactly the kind of flaky-CI signal that trains people to
ignore the gate. Aborting before the run starts, with the exact remaining-token count in the
failure message, turns that into an honest "couldn't measure this today" instead of a false
"this PR regressed."
