"""One-off: select 120 candidate rows for Week 5's human-labeled gold set.

Pulls real eval_results from Postgres, classifies each of the 39
non-known-failing orchestrator_v1 scenarios as stable_failure / stable_pass /
one_off using the three canonical 2026-09-28 baseline samples (matching
docs/baselines/orchestrator_v1_baseline_20260928.md's own analysis exactly),
then pulls candidate ROWS for those scenarios from every real-Vesper run with
trajectory data persisted (for variety beyond just those three runs), dedupes
byte-identical trajectories, and round-robins across scenarios within each
bucket so labels cover diverse scenarios and trajectories rather than
clustering on whichever scenario happened to produce the most rows.

    python scripts/select_gold_candidates.py

See docs/gold_set_methodology.md for the sampling rationale and label schema.
"""
import asyncio
import json
import random
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select
from vantage_api.database import SessionLocal, engine
from vantage_api.models import EvalResult, EvalScenario

OUTPUT = Path("packages/eval_engine/data/gold_candidates.jsonl")

#: The 3 canonical baseline samples (2026-09-28) — used ONLY to classify each
#: scenario as stable_failure / stable_pass / one_off. Never used for
#: anything else here, so a future baseline collection doesn't need to
#: change this script's classification, only re-run it deliberately.
CLASSIFICATION_RUN_IDS = [
    "a41a1d77-ce26-4860-99fe-2650a0fa7d1e",
    "28c70401-f2e7-48f4-83db-7e536c0f08ab",
    "5124cac4-0dbe-4f70-8838-89dcb9cad4a0",
]

#: Every real-Vesper run with trajectory data actually persisted
#: (tool_sequence/final_reply non-null — see the has_trajectory check this
#: was derived from). The candidate ROWS are drawn from this wider pool for
#: variety; a40fa9eb (mock adapter, fully deterministic, fictional agents) is
#: deliberately excluded — useless for judging real routing quality.
CANDIDATE_ROW_RUN_IDS = CLASSIFICATION_RUN_IDS + [
    "a53862ef-85f8-4f7a-8a72-2a92a7b329d8",  # 2026-09-28 fallback-fix verification run
]

TARGETS = {"stable_failure": 60, "stable_pass": 30, "one_off": 30}
#: Explicit per-scenario cap for stable_pass only, per the task's own spec
#: ("sample 2 per scenario"). stable_failure and one_off have no stated cap;
#: round-robin still spreads them evenly, it just doesn't hard-stop early.
MAX_PER_SCENARIO = {"stable_pass": 2}

RANDOM_SEED = 42

ROUTER_FAILURE_MESSAGE = "Sir, I'm having trouble thinking right now."


def is_infra_sentinel(result: EvalResult) -> bool:
    """Proxy for routed_agent in {PLANNER_FAILURE, ADAPTER_ERROR} — that field
    itself was never persisted as its own column (docs/deferred_for_week5.md
    item 1). Two independent signals, either sufficient: the router's fixed
    RouterError.user_message reply text, or the judge's own reasoning naming
    the sentinel (its rendered prompt shows routed_agent directly, so this
    shows up whenever the judge actually saw one).

    An earlier, weaker version of this proxy (empty tool_sequence + null
    final_reply) was WRONG — a PLANNER_FAILURE's final_reply is this fixed
    message, not null — caught while building this script. Re-verified it
    didn't change any already-reported conclusion: re-running
    scripts/recompute_baseline_under_new_methodology.py's query with this
    corrected proxy against the three canonical runs still finds zero
    infra-sentinel rows."""
    if result.final_reply == ROUTER_FAILURE_MESSAGE:
        return True
    reasoning = result.llm_judge_reasoning or ""
    return "PLANNER_FAILURE" in reasoning or "ADAPTER_ERROR" in reasoning


def is_judge_error(result: EvalResult) -> bool:
    reasoning = result.llm_judge_reasoning
    return bool(reasoning and reasoning.startswith("judge_error:"))


async def classify_scenarios(db) -> dict[str, str]:
    """external_id -> "stable_failure" | "stable_pass" | "one_off".

    Matches docs/baselines/orchestrator_v1_baseline_20260928.md's own
    definition exactly: >=2 failures among real data points is
    "stable_failure" (a minority 1-of-3 pass doesn't rescue it — that's still
    a real, reproducible gap). "stable_pass" is the mirror case — ZERO
    failures, not merely >=2 passes: a scenario with 2 passes and 1 failure
    genuinely flipped once and belongs in one_off, not stable_pass. Exactly
    one failure among real data points (with the rest passing) is precisely
    the "ambiguous middle" one_off exists to capture; so does the rare case
    of fewer than 2 real data points at all (can't establish stability either
    way). An earlier version of this function required UNANIMOUS agreement
    among >=2 data points for BOTH directions, which silently found only
    7/11 stable scenarios instead of 14/(zero-failure count) and miscounted
    "2 pass + 1 fail" as stable_pass instead of one_off — caught by
    cross-checking stable_failure against the already-published 14-scenario
    list before trusting this script's selection.

    known_failing scenarios (clear_010) are excluded entirely — already
    tracked separately, not part of this analysis.
    """
    rows = (
        await db.execute(
            select(EvalResult, EvalScenario.external_id, EvalScenario.known_failing)
            .join(EvalScenario, EvalResult.scenario_id == EvalScenario.scenario_id)
            .where(EvalResult.run_id.in_(CLASSIFICATION_RUN_IDS))
        )
    ).all()

    outcomes: dict[str, list[bool]] = defaultdict(list)
    for result, external_id, known_failing in rows:
        if known_failing or is_judge_error(result) or is_infra_sentinel(result):
            continue  # no real data point from this sample
        outcomes[external_id].append(result.passed)

    classification: dict[str, str] = {}
    for external_id, passes in outcomes.items():
        num_pass = sum(passes)
        num_fail = len(passes) - num_pass
        if num_fail >= 2:
            classification[external_id] = "stable_failure"
        elif num_fail == 0 and num_pass >= 1:
            classification[external_id] = "stable_pass"
        else:
            # num_fail == 1 (a real flip), or fewer than 2 total real data
            # points either way — both are genuinely under-determined.
            classification[external_id] = "one_off"
    return classification


async def fetch_candidate_rows(db, external_ids: set[str]) -> list[tuple[EvalResult, EvalScenario]]:
    rows = (
        await db.execute(
            select(EvalResult, EvalScenario)
            .join(EvalScenario, EvalResult.scenario_id == EvalScenario.scenario_id)
            .where(EvalResult.run_id.in_(CANDIDATE_ROW_RUN_IDS))
            .where(EvalScenario.external_id.in_(external_ids))
        )
    ).all()
    return [(r, s) for r, s in rows if not is_infra_sentinel(r)]


def dedup(rows: list[tuple[EvalResult, EvalScenario]]) -> list[tuple[EvalResult, EvalScenario]]:
    """Keep only the first occurrence per (scenario, tool_sequence, final_reply)
    — a byte-identical trajectory across samples (common at temp 0.3 for
    simple scenarios) wastes a label slot on something already judged."""
    seen = set()
    unique = []
    for result, scenario in rows:
        key = (
            str(scenario.scenario_id),
            json.dumps(result.tool_sequence, sort_keys=True),
            result.final_reply,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append((result, scenario))
    return unique


def round_robin_sample(
    rows: list[tuple[EvalResult, EvalScenario]],
    target: int,
    rng: random.Random,
    max_per_scenario: int | None,
) -> list[tuple[EvalResult, EvalScenario]]:
    """Spread selection evenly across scenarios instead of pure random
    pooling, which would let a scenario with many unique trajectories crowd
    out one with few. One pass per scenario per round; scenario order is
    shuffled once so no scenario systematically goes first every round."""
    by_scenario: dict[str, list[tuple[EvalResult, EvalScenario]]] = defaultdict(list)
    for result, scenario in rows:
        by_scenario[scenario.external_id].append((result, scenario))
    for pool in by_scenario.values():
        rng.shuffle(pool)

    scenario_ids = list(by_scenario.keys())
    rng.shuffle(scenario_ids)

    selected: list[tuple[EvalResult, EvalScenario]] = []
    taken_per_scenario: dict[str, int] = defaultdict(int)
    progressed = True
    while len(selected) < target and progressed:
        progressed = False
        for sid in scenario_ids:
            if len(selected) >= target:
                break
            if max_per_scenario and taken_per_scenario[sid] >= max_per_scenario:
                continue
            pool = by_scenario[sid]
            if not pool:
                continue
            selected.append(pool.pop())
            taken_per_scenario[sid] += 1
            progressed = True
    return selected


def to_record(result: EvalResult, scenario: EvalScenario, bucket: str) -> dict:
    return {
        "result_id": str(result.result_id),
        "run_id": str(result.run_id),
        "scenario_id": str(scenario.scenario_id),
        "external_id": scenario.external_id,
        "category": scenario.category,
        "input": scenario.input,
        "expected": scenario.expected,
        "rubric_hard_checks": (scenario.rubric or {}).get("hard_checks"),
        "tool_sequence": result.tool_sequence,
        "tool_calls": result.tool_calls,
        "final_reply": result.final_reply,
        "llm_judge_score": result.llm_judge_score,
        "llm_judge_reasoning": result.llm_judge_reasoning,
        "deterministic_scores": result.deterministic_scores,
        "bucket": bucket,
    }


async def main() -> None:
    rng = random.Random(RANDOM_SEED)
    async with SessionLocal() as db:
        classification = await classify_scenarios(db)

        buckets_scenarios: dict[str, list[str]] = defaultdict(list)
        for external_id, cls in classification.items():
            buckets_scenarios[cls].append(external_id)

        print("Scenario classification (from the 3 canonical baseline samples):")
        for cls in ("stable_failure", "stable_pass", "one_off"):
            ids = sorted(buckets_scenarios[cls])
            print(f"  {cls}: {len(ids)} scenarios — {ids}")

        raw_rows = await fetch_candidate_rows(db, set(classification.keys()))
        deduped = dedup(raw_rows)

        rows_by_bucket: dict[str, list[tuple[EvalResult, EvalScenario]]] = defaultdict(list)
        for result, scenario in deduped:
            cls = classification.get(scenario.external_id)
            if cls:
                rows_by_bucket[cls].append((result, scenario))

        selected: list[tuple[EvalResult, EvalScenario, str]] = []
        print("\nSelection:")
        for cls, target in TARGETS.items():
            available = rows_by_bucket[cls]
            chosen = round_robin_sample(available, target, rng, MAX_PER_SCENARIO.get(cls))
            print(
                f"  {cls}: {len(chosen)}/{target} selected "
                f"(from {len(available)} unique candidate rows across "
                f"{len(buckets_scenarios[cls])} scenarios)"
            )
            selected.extend((r, s, cls) for r, s in chosen)

    await engine.dispose()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w") as f:
        for result, scenario, cls in selected:
            f.write(json.dumps(to_record(result, scenario, cls), default=str) + "\n")

    print(f"\nWrote {len(selected)} candidates to {OUTPUT}")
    if len(selected) < sum(TARGETS.values()):
        print(
            f"NOTE: {sum(TARGETS.values())} was the target; only {len(selected)} unique "
            "candidates existed in the real data available. This is an honest shortfall, "
            "not padded — more real runs (or a wider CANDIDATE_ROW_RUN_IDS list) would "
            "close the gap."
        )


if __name__ == "__main__":
    asyncio.run(main())
