# `data/`

Local, untracked working data. Only this README is committed.

## `judge_traces.jsonl` — training data for the Week 5 judge fine-tune

Every clean call the LLM judge makes during an eval run, one JSON object per
line. The Week 5 plan is to fine-tune a small in-house judge on these
(input prompt → judgment), so this file is the raw material for that.

It is gitignored (large, and regenerable). If you need it and it isn't here, generate it:

### How it's generated

```bash
set -a; source ~/Documents/vesper/.env; set +a        # the agent's GROQ_API_KEY
python scripts/collect_judge_traces.py \
    --suite packages/eval_engine/suites/orchestrator_v1 \
    --iterations 40 \
    --output data/judge_traces.jsonl \
    --judge-provider groq
```

That loops `vantage eval run … --collect-judge-traces data/judge_traces.jsonl`
N times (`LLMJudgeScorer` appends each clean judgment as it happens). Re-running
**appends**; nothing is overwritten, and an interrupted run keeps everything so
far. Collection runs are not written to Postgres (`--no-persist`) so they don't
flood the dashboard with near-identical runs; pass `--persist` to override.

The final report prints the numbers that matter, and they are not the line count:
**unique prompts** is the effective dataset size (see *Duplicates* below).

### Schema (`schema_version` 1)

| field | type | meaning |
| --- | --- | --- |
| `schema_version` | int | Layout version of this line |
| `timestamp` | string | UTC ISO-8601, when the judgment was made |
| `judge_provider` / `judge_model` | string | Which judge produced the label, e.g. `groq` / `qwen/qwen3.8-27b` |
| `judge_temperature` | float | Judge sampling temperature (0.0) |
| `scenario_id` / `category` | string | e.g. `clear_002` / `clear` |
| `system_prompt` | string | The judge's system prompt, incl. the generic score guide |
| `input_prompt` | string | The rendered per-scenario user prompt (scenario's rubric template with the agent's output filled in) |
| `raw_response` | string | Exactly what the judge returned (a JSON string) |
| `parsed_score` | int/float | Score parsed from `raw_response`, always within 1–5 |
| `parsed_reasoning` | string | Reasoning parsed from `raw_response` |
| `actual_output` | object | `{routed_agent, extracted_entities}` — what the agent under test did |
| `expected` | object | The scenario's `expected` block |

A training example is `system_prompt` + `input_prompt` → `raw_response`.

### What is deliberately *not* here

Judge calls that failed (API error), returned unparseable output, or returned a
score outside 1–5 are **skipped, not logged**. The eval itself scores those as
`0.0` placeholders; a placeholder is not a label, and logging one would teach the
fine-tuned judge to emit malformed output. (A raw `"score": 7` is skipped rather
than clamped to 5 for the same reason: the label would contradict `raw_response`.)
The CLI prints `N logged, M skipped` per run.

### Caveats for whoever trains on this

- **Duplicates: the line count is not the dataset size.** An iteration only adds a
  new *input* when the agent under test answered differently than before (Vesper
  samples at 0.3, so some scenarios vary; scenarios that answer `chat_agent` the
  same way each time repeat exactly). The judge's temperature 0 makes an
  identical `input_prompt` *usually* return the same judgment, but not always —
  so `unique examples` can exceed `unique prompts`. Count and dedupe on
  `input_prompt`:
  ```bash
  jq -r '.input_prompt' data/judge_traces.jsonl | sort -u | wc -l
  ```
  Measured in a 2-iteration pilot (10-scenario smoke suite, real Vesper, Groq
  judge): 19 lines but only **13 unique prompts**, with the second iteration
  contributing 3 new ones. The mock adapter is fully deterministic, so mock runs
  are pure duplicates.
- **Scores skew high.** Most of the agent's real outputs on these scenarios are
  judged correct, so failures — the examples a judge most needs to see — are the
  minority. In the same pilot 14 of 19 scores were 5. Iterating the same agent
  harder doesn't fix that; it needs more scenarios or deliberately varied
  (including wrong) agent outputs.
- **One judge, one prompt version.** Labels come from whatever judge
  `judge_model` names, not necessarily a model you'd want as the teacher. The
  system prompt has been edited during the project (an injection-resistance
  paragraph was added), so split on `system_prompt` if you need a single version.
- **Two rubrics in play.** The system prompt gives a generic 1–5 routing-quality
  guide, but each scenario's `input_prompt` carries its own scoring anchors
  (often 5/3/1 for "honest decline"/"vague"/"claims success"). Scores follow
  whichever the judge weighed; expect clumping on the scenario anchors.
- **Content.** Includes the adversarial scenarios' input text (fake "reveal your
  API keys" prompts and the like). No real secrets — but it's local for a reason.
- **A torn line is possible** if a run was killed mid-write. The logger
  terminates it so it can't swallow the next entry; skip lines that don't parse
  (`jq -c . file >/dev/null` reports the first bad one).

```bash
wc -l data/judge_traces.jsonl                                          # raw lines
jq -c '.parsed_score' data/judge_traces.jsonl | sort | uniq -c         # score distribution
```
