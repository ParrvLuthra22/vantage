"""Accumulate LLM judge training data for the Week 5 judge fine-tune.

Runs an eval suite N times, appending every clean judge call to one JSONL file
(schema and purpose: data/README.md). Re-running appends more; nothing is ever
overwritten.

What varies between iterations: mainly the AGENT's output. The judge runs at
temperature 0, so an identical prompt almost always (not always) gets the same
judgment, and an iteration only adds a *new* training input when the agent
under test answered differently than before. Vesper's ModelRouter samples at
temperature 0.3 by default, which is what makes iterations differ at all -- this
script does not set it. In practice that is not much: a 2-iteration pilot of the
10-scenario smoke suite produced 19 lines but only 13 unique prompts.

The mock adapter has no randomness whatsoever, so N mock iterations are N copies
of the same examples; it is allowed (to smoke-test this pipeline) but warned
about. The final report counts unique prompts, so the raw line count can't
overstate how much data you have.

Usage (from the repo root, with the eval env active):

    set -a; source ~/Documents/vesper/.env; set +a      # the agent's GROQ_API_KEY
    python scripts/collect_judge_traces.py \\
        --suite packages/eval_engine/suites/orchestrator_v1 \\
        --iterations 40 \\
        --output data/judge_traces.jsonl \\
        --judge-provider groq

Runs are NOT written to Postgres unless --persist is given: a collection run
would otherwise add one near-duplicate eval run per iteration to the dashboard.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path


def resolve_vantage_executable() -> str:
    """The `vantage` console script next to this interpreter, else on PATH.

    Not just `shutil.which`: invoked as `.venv/bin/python scripts/...` the venv's
    bin/ usually isn't on PATH, and picking up a different `vantage` from
    somewhere else would silently test a different install.
    """
    sibling = Path(sys.executable).with_name("vantage")
    if sibling.exists():
        return str(sibling)
    found = shutil.which("vantage")
    if found:
        return found
    raise SystemExit("Could not find the `vantage` command; activate the eval environment first.")


def build_command(
    vantage: str,
    suite: str,
    adapter: str,
    judge_provider: str,
    output: Path,
    persist: bool,
    caffeinate: bool,
) -> list[str]:
    cmd = [
        vantage,
        "eval",
        "run",
        suite,
        "--adapter",
        adapter,
        "--judge-provider",
        judge_provider,
        "--collect-judge-traces",
        str(output),
    ]
    if not persist:
        cmd.append("--no-persist")
    if caffeinate and sys.platform == "darwin" and shutil.which("caffeinate"):
        # A run once stalled ~2.5h when the Mac slept mid-collection; hence on by default.
        cmd = ["caffeinate", "-i", *cmd]
    return cmd


def summarize(path: Path) -> dict:
    """Read the JSONL and report what's actually in it (never trusts line count).

    A line that isn't valid JSON (e.g. one torn by a killed run) is counted, not fatal.
    """
    total = invalid = 0
    prompts: set[str] = set()
    examples: set[tuple[str, str]] = set()
    scores: Counter = Counter()
    categories: Counter = Counter()
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                total += 1
                try:
                    entry = json.loads(line)
                    prompt, raw = entry["input_prompt"], entry["raw_response"]
                    scores[entry["parsed_score"]] += 1
                    categories[entry["category"]] += 1
                except (json.JSONDecodeError, KeyError, TypeError):
                    invalid += 1
                    continue
                prompts.add(prompt)
                examples.add((prompt, raw))
    return {
        "lines": total,
        "invalid": invalid,
        "unique_prompts": len(prompts),
        "unique_examples": len(examples),
        "scores": dict(sorted(scores.items())),
        "categories": dict(sorted(categories.items())),
    }


def print_report(path: Path, started: float) -> dict:
    s = summarize(path)
    print(f"\n=== {path} ===")
    print(f"lines:            {s['lines']}  ({s['invalid']} not valid JSON)")
    print(f"unique prompts:   {s['unique_prompts']}   <- distinct inputs = effective dataset size")
    print(f"unique examples:  {s['unique_examples']}   (distinct prompt + response pairs)")
    print(f"score histogram:  {s['scores']}")
    print(f"by category:      {s['categories']}")
    print(f"wall time:        {(time.monotonic() - started) / 60:.1f} min")
    if s["scores"] and s["lines"]:
        top_score, top_n = max(s["scores"].items(), key=lambda kv: kv[1])
        if top_n / s["lines"] > 0.6:
            print(
                f"\nNote: {top_n / s['lines']:.0%} of judgments are a {top_score}. The agent is "
                "mostly judged correct, so the failure examples a judge most needs are scarce."
            )
    if s["lines"] and s["unique_prompts"] < s["lines"] * 0.5:
        print(
            "\nNote: fewer than half the lines are distinct prompts. Expected when the agent "
            "answers the same way each time (always, with the mock adapter); dedupe on "
            "input_prompt before training."
        )
    return s


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--suite", required=True)
    parser.add_argument("--iterations", type=int, default=40, help="Runs to append (default 40)")
    parser.add_argument("--output", required=True, help="JSONL file to append to")
    parser.add_argument(
        "--adapter",
        default="vesper",
        choices=["vesper", "mock"],
        help="Agent under test. 'mock' is deterministic: every iteration repeats itself.",
    )
    parser.add_argument(
        "--judge-provider",
        default="openai",
        choices=["openai", "groq", "openrouter"],
        help="Judge provider (default: the CLI's own, openai). Its API-key env var must be set.",
    )
    parser.add_argument("--persist", action="store_true", help="Also write each run to Postgres")
    parser.add_argument(
        "--no-caffeinate", action="store_true", help="Don't prevent idle sleep (macOS)"
    )
    parser.add_argument(
        "--max-zero-yield",
        type=int,
        default=2,
        help="Abort after this many consecutive iterations that add no lines (default 2)",
    )
    parser.add_argument(
        "--iteration-timeout-min",
        type=float,
        default=180,
        help="Kill any single iteration that runs longer than this (default 180)",
    )
    args = parser.parse_args()

    # Fail before hours of agent runs, not after the first judge call.
    from vantage_eval.scorers.llm_judge import PROVIDER_PRESETS

    key_env = PROVIDER_PRESETS[args.judge_provider]["api_key_env"]
    if not os.environ.get(key_env):
        print(
            f"{key_env} is not set (needed by --judge-provider {args.judge_provider}).",
            file=sys.stderr,
        )
        return 2
    if args.adapter == "mock":
        print(
            "WARNING: the mock adapter is deterministic -- every iteration will log the same "
            "examples again. Fine for testing this pipeline; useless as training data.",
            file=sys.stderr,
        )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    log_path = output.with_suffix(".collector.log")
    vantage = resolve_vantage_executable()
    cmd = build_command(
        vantage,
        args.suite,
        args.adapter,
        args.judge_provider,
        output,
        args.persist,
        caffeinate=not args.no_caffeinate,
    )

    started = time.monotonic()
    before = summarize(output)
    print(f"Appending to {output} ({before['lines']} lines already there)")
    print(f"Per-iteration output: {log_path}")

    zero_yield = 0
    try:
        for i in range(1, args.iterations + 1):
            lines_before = summarize(output)["lines"]
            t0 = time.monotonic()
            with log_path.open("a", encoding="utf-8") as log:
                log.write(f"\n===== iteration {i}/{args.iterations} =====\n")
                log.flush()
                offset = log.tell()
                try:
                    rc = subprocess.run(
                        cmd,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=args.iteration_timeout_min * 60,
                    ).returncode
                except subprocess.TimeoutExpired:
                    rc = None

            s = summarize(output)
            added = s["lines"] - lines_before
            elapsed = time.monotonic() - t0
            status = "TIMED OUT" if rc is None else f"exit {rc}"
            print(
                f"Iteration {i}/{args.iterations}: {status}, {elapsed:.0f}s, "
                f"+{added} lines (total {s['lines']}, {s['unique_prompts']} unique prompts)",
                flush=True,
            )

            # `vantage eval run` exits 0 (all scenarios passed) or 1 (some failed) on a normal
            # run, but an uncaught crash is *also* exit 1 -- so the exit code alone can't say
            # whether the run worked. Whether it appended anything can.
            if rc not in (0, 1, None):
                print(f"Unexpected exit code {rc}; see {log_path}", file=sys.stderr)
                return 1
            zero_yield = zero_yield + 1 if added == 0 else 0
            if zero_yield >= args.max_zero_yield:
                print(
                    f"\n{zero_yield} consecutive iterations logged nothing -- stopping instead of "
                    f"burning the remaining {args.iterations - i}. Last output:",
                    file=sys.stderr,
                )
                with log_path.open(encoding="utf-8") as log:
                    log.seek(offset)
                    print("".join(log.readlines()[-15:]), file=sys.stderr)
                print_report(output, started)
                return 1
    except KeyboardInterrupt:
        print("\nInterrupted. Everything logged so far is kept; re-run to append more.")
        print_report(output, started)
        return 130

    print_report(output, started)
    return 0


if __name__ == "__main__":
    sys.exit(main())
