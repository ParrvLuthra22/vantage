"""Interactive labeler for the Week 5 gold set.

Reads gold_candidates.jsonl, presents each row to the user, records the label
to gold_labels_v1.jsonl. Resumable: labels are appended and flushed one at a
time, and load_labeled_ids() skips any result_id already in the labels file,
so a Ctrl-C (or any crash) mid-session loses at most the label in progress —
everything already written stays written. See docs/gold_set_methodology.md
for the label schema and sampling strategy.

    python scripts/label_gold_set.py
    python scripts/label_gold_set.py --limit 10   # stop after N labels this session
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import click

CANDIDATES = Path("packages/eval_engine/data/gold_candidates.jsonl")
LABELS = Path("packages/eval_engine/data/gold_labels_v1.jsonl")

FAILURE_CLASSES = [
    "hallucination", "wrong_agent", "wrong_entities", "latency_over_budget",
    "refused_correctly", "refused_incorrectly", "correct",
    "defensible_alternative", "judge_confused",
]


def load_labeled_ids() -> set[str]:
    if not LABELS.exists():
        return set()
    return {
        json.loads(line)["result_id"]
        for line in LABELS.read_text().splitlines()
        if line.strip()
    }


def present(candidate: dict) -> None:
    """Format a candidate for human review."""
    click.echo("\n" + "=" * 80)
    click.echo(f"Scenario: {candidate['external_id']}  (result_id: {candidate['result_id'][:8]})")
    click.echo(f"Category: {candidate.get('category', '?')}")
    click.echo(f"Bucket: {candidate.get('bucket', '?')}")
    click.echo(f"Input: {candidate['input']}")
    click.echo("\nActual behavior:")
    click.echo(f"  Tool sequence: {' -> '.join(candidate.get('tool_sequence') or []) or '(none)'}")
    click.echo(f"  Tool calls: {json.dumps(candidate.get('tool_calls') or [], indent=2)[:500]}")
    click.echo(f"  Final reply: {candidate.get('final_reply') or '(none)'}")
    click.echo(f"\nJudge (Qwen) said: {candidate.get('llm_judge_score')}/5")
    click.echo(f"Judge reasoning: {candidate.get('llm_judge_reasoning') or '(none)'}")
    expected = json.dumps(candidate.get("expected") or {}, indent=2)[:300]
    click.echo(f"\nExpected (from scenario rubric): {expected}")


def collect_label(candidate: dict) -> dict | str | None:
    """Prompt for one label. Returns None to skip, "quit" to stop the session."""
    action = click.prompt(
        "[l]abel, [s]kip, [q]uit", type=click.Choice(["l", "s", "q"]), default="l"
    )
    if action == "q":
        return "quit"
    if action == "s":
        return None

    score = click.prompt("Score (1-5)", type=click.IntRange(1, 5))
    options = ", ".join(f"[{i}]{c}" for i, c in enumerate(FAILURE_CLASSES))
    click.echo(f"Failure class options: {options}")
    fc_idx = click.prompt("Failure class", type=click.IntRange(0, len(FAILURE_CLASSES) - 1))
    reasoning = click.prompt("Reasoning (3-5 sentences)", type=str)
    confidence = click.prompt("Confidence (1=hard, 3=easy)", type=click.IntRange(1, 3), default=2)
    notes = click.prompt("Notes (optional, enter to skip)", type=str, default="")

    return {
        "result_id": candidate["result_id"],
        "scenario_external_id": candidate["external_id"],
        "score": score,
        "failure_class": FAILURE_CLASSES[fc_idx],
        "reasoning": reasoning,
        "confidence": confidence,
        "notes": notes or None,
        "labeler": "parrv",
        "labeled_at": datetime.now(timezone.utc).isoformat(),
    }


@click.command()
@click.option("--limit", default=None, type=int, help="Stop after N labels this session")
def main(limit: Optional[int]):
    if not CANDIDATES.exists():
        raise click.ClickException(
            f"{CANDIDATES} not found — run scripts/select_gold_candidates.py first."
        )
    candidates = [json.loads(line) for line in CANDIDATES.read_text().splitlines() if line.strip()]
    done = load_labeled_ids()
    remaining = [c for c in candidates if c["result_id"] not in done]
    click.echo(
        f"Total candidates: {len(candidates)}, already labeled: {len(done)}, "
        f"remaining: {len(remaining)}"
    )

    LABELS.parent.mkdir(parents=True, exist_ok=True)
    session_count = 0
    with LABELS.open("a") as f:
        for candidate in remaining:
            present(candidate)
            result = collect_label(candidate)
            if result == "quit":
                break
            if result is None:
                continue
            f.write(json.dumps(result) + "\n")
            f.flush()
            session_count += 1
            click.echo(f"[saved — {session_count} this session, {len(done) + session_count} total]")
            if limit and session_count >= limit:
                click.echo(f"Session limit {limit} reached.")
                break


if __name__ == "__main__":
    main()
