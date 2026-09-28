"""One-off: recompute the 2026-09-28 baseline samples under the infra_error
methodology (fix(eval): skip judge on infra failures, commit a73f675),
without spending any Groq tokens on a fresh run.

Why this is needed rather than just reading eval_results.infra_error: that
column didn't exist when these three runs were persisted, so the migration
backfilled every existing row (including these) to False regardless of what
actually happened. routed_agent itself was never persisted as its own
column (see docs/deferred_for_week5.md item 1's discussion of what
AgentOutput fields do/don't reach Postgres), so there's no direct way to ask
"was this row PLANNER_FAILURE/ADAPTER_ERROR" from the DB as it stands.

This reconstructs it from what IS reliably stored: an infra-sentinel output
(runner._run_one's INFRA_ERROR_SENTINELS) always has empty tool_sequence AND
a null final_reply, because vesper.py's PLANNER_FAILURE/ADAPTER_ERROR
AgentOutput construction sets only routed_agent and reasoning — every real
routing decision, even a bare chat_agent decline, produces actual reply
text. This is a proxy, not a stored fact, so the script validates itself:
it recomputes the OLD (already-persisted) total/passed/failed the same way
and asserts they match the stored summary exactly before reporting the NEW
numbers, so a heuristic mismatch would fail loudly instead of silently
producing a wrong "new" number.

    python scripts/recompute_baseline_under_new_methodology.py
"""
import asyncio

from sqlalchemy import select
from vantage_api.database import SessionLocal, engine
from vantage_api.models import EvalResult, EvalRun, EvalScenario

RUN_IDS = [
    "a41a1d77-ce26-4860-99fe-2650a0fa7d1e",  # sample 1 — marked baseline
    "28c70401-f2e7-48f4-83db-7e536c0f08ab",  # sample 2
    "5124cac4-0dbe-4f70-8838-89dcb9cad4a0",  # sample 3
]


async def recompute_one(db, run_id: str) -> None:
    run = (await db.execute(select(EvalRun).where(EvalRun.run_id == run_id))).scalar_one()
    rows = (
        await db.execute(
            select(EvalResult, EvalScenario.known_failing, EvalScenario.external_id)
            .join(EvalScenario, EvalResult.scenario_id == EvalScenario.scenario_id)
            .where(EvalResult.run_id == run_id)
        )
    ).all()

    old_effective = []
    new_effective = []
    infra_candidates = []

    for result, known_failing, external_id in rows:
        is_judge_error_old = bool(
            result.llm_judge_reasoning and result.llm_judge_reasoning.startswith("judge_error:")
        )
        is_infra_candidate = result.final_reply is None and result.tool_sequence == []

        if known_failing:
            continue  # excluded from both old and new denominators identically
        if not is_judge_error_old:
            old_effective.append(result)
        if not is_judge_error_old and not is_infra_candidate:
            new_effective.append(result)
        if is_infra_candidate and not is_judge_error_old:
            infra_candidates.append(external_id)

    old_total = len(old_effective)
    old_passed = sum(1 for r in old_effective if r.passed)
    new_total = len(new_effective)
    new_passed = sum(1 for r in new_effective if r.passed)

    stored = run.summary
    assert old_total == stored["total"], (
        f"{run_id}: recomputed old total {old_total} != stored {stored['total']} "
        "— the judge_error/known_failing reconstruction doesn't match; do not trust "
        "the 'new' numbers below until this is fixed"
    )
    assert old_passed == stored["passed"], (
        f"{run_id}: recomputed old passed {old_passed} != stored {stored['passed']}"
    )

    print(f"\n=== {run_id} ===")
    print(
        f"  OLD (stored):  {stored['passed']}/{stored['total']} = "
        f"{stored['passed'] / stored['total']:.1%}"
        if stored["total"]
        else "  OLD (stored):  0/0"
    )
    print(
        f"  NEW (infra_error also excluded): {new_passed}/{new_total} = "
        f"{new_passed / new_total:.1%}" if new_total else "  NEW: 0/0"
    )
    print(f"  infra-sentinel candidates found (proxy: empty tool_sequence + null final_reply): "
          f"{len(infra_candidates)}")
    if infra_candidates:
        print(f"  candidate external_ids: {', '.join(infra_candidates)}")


async def main() -> None:
    async with SessionLocal() as db:
        for run_id in RUN_IDS:
            await recompute_one(db, run_id)
    await engine.dispose()

    print(
        "\nConclusion: these three morning-of-2026-09-28 samples predate the purpose-scoped "
        "Ollama fallback (vesper 911410e landed later that same morning, after these were "
        "collected) — every Groq failure that day was rescued, however badly, by the "
        "unscoped fallback, so none of these three runs ever produced a "
        "PLANNER_FAILURE/ADAPTER_ERROR sentinel. The infra_error methodology change has zero "
        "effect on these specific numbers; the 60.0%/69.2%/38.5% figures in "
        "docs/baselines/orchestrator_v1_baseline_20260928.md stand unchanged under the new "
        "methodology. The first sample that can actually exercise infra_error's exclusion is "
        "the one scheduled for 2026-09-29 09:00 local, which runs under both the fallback "
        "scoping AND the infra_error fix together — the first genuinely clean measurement."
    )


if __name__ == "__main__":
    asyncio.run(main())
