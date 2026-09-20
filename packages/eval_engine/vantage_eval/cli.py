"""Vantage CLI: `vantage eval run|compare`."""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from uuid import UUID

import click
from rich.console import Console
from rich.table import Table

from vantage_eval import persistence
from vantage_eval.agents.mock import MockAdapter
from vantage_eval.loader import load_suite
from vantage_eval.regression.detector import detect_regressions
from vantage_eval.runner import run_suite
from vantage_eval.scorers.llm_judge import LLMJudgeScorer


@click.group()
def main():
    """Vantage — LLM agent evaluation and observability."""


@main.group()
def eval():
    """Run and inspect evaluation suites."""


@eval.command("run")
@click.argument("suite_path", type=click.Path(exists=True, file_okay=False))
@click.option("--adapter", default="mock", help="Adapter to use: mock | vesper")
@click.option("--no-judge", is_flag=True, help="Skip LLM judging (deterministic only)")
@click.option(
    "--judge-provider",
    default="openai",
    type=click.Choice(["openai", "groq", "openrouter"]),
    help="OpenAI-compatible provider for the LLM judge (see llm_judge.PROVIDER_PRESETS "
    "for model/cost/api-key-env defaults per provider)",
)
@click.option("--verbose", "-v", is_flag=True, help="Print per-scenario results")
@click.option(
    "--min-llm-pass", default=4.0, type=float, help="Minimum LLM score to count as passed"
)
@click.option(
    "--mark-baseline",
    is_flag=True,
    help="Mark this run as the current baseline for this suite (unsets any previous baseline)",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Write this run's SuiteRun as JSON to this path (works with or without Postgres -- "
    "e.g. for `vantage eval compare` in a CI job with no DB, see docs/vesper_routing_surface.md's "
    "eval-gate workflow)",
)
def eval_run(
    suite_path: str,
    adapter: str,
    no_judge: bool,
    judge_provider: str,
    verbose: bool,
    min_llm_pass: float,
    mark_baseline: bool,
    output_path: str | None,
):
    """Run an evaluation suite against an agent."""
    console = Console()
    suite = load_suite(suite_path)

    if adapter == "mock":
        adapter_impl = MockAdapter()
    elif adapter == "vesper":
        try:
            from vantage_eval.agents.vesper import VesperAdapter

            adapter_impl = VesperAdapter()
        except ImportError as e:
            console.print(
                "[red]vesper adapter not available — install vesper and add adapter file[/red]"
            )
            raise SystemExit(2) from e
    else:
        console.print(f"[red]Unknown adapter: {adapter}[/red]")
        raise SystemExit(2)

    judge = None if no_judge else LLMJudgeScorer(provider=judge_provider)

    agent_version = _current_git_sha()

    console.print(
        f"[bold]Running suite {suite.name}[/bold] against [cyan]{adapter}[/cyan] "
        f"adapter (agent version {agent_version[:8]})"
    )
    if judge:
        console.print(f"[dim]Judge: {judge.model}[/dim]")

    run = run_suite(
        suite,
        adapter_impl,
        agent_version=agent_version,
        judge=judge,
        min_llm_pass=min_llm_pass,
        verbose=verbose,
    )

    if output_path is not None:
        Path(output_path).write_text(run.model_dump_json(indent=2))
        console.print(f"[dim]Wrote run JSON to {output_path}[/dim]")

    run_id = asyncio.run(persistence.persist_run(suite, run, mark_baseline=mark_baseline))
    if run_id is not None:
        baseline_note = " [bold yellow](marked as baseline)[/bold yellow]" if mark_baseline else ""
        console.print(f"[dim]Persisted as eval run {run_id}{baseline_note}[/dim]")
    else:
        console.print("[dim]Could not persist this run to Postgres (see warnings above) — scorecard below is still accurate[/dim]")

    _print_scorecard(run, console)

    if run_id is not None:
        console.print()
        console.print(f"[dim]Run ID:[/dim] [cyan]{run_id}[/cyan]")
        console.print(f"[dim]Compare: vantage eval compare {run_id}[/dim]")

    # Exit code = number of failed scenarios (useful for CI gating)
    raise SystemExit(0 if run.summary.failed == 0 else 1)


@eval.command("compare")
@click.argument("current_run_id")
@click.option(
    "--baseline",
    "baseline_run_id",
    default=None,
    help="Baseline run ID (default: current marked baseline for the suite)",
)
@click.option(
    "--suite", default="orchestrator_v1", help="Suite name (used when --baseline not specified)"
)
@click.option(
    "--fail-on-regression", is_flag=True, help="Exit non-zero if any regressions detected (CI mode)"
)
def eval_compare(
    current_run_id: str, baseline_run_id: str | None, suite: str, fail_on_regression: bool
):
    """Compare a run against a baseline and print a regression report.

    CURRENT_RUN_ID and --baseline each accept either a Postgres run UUID or
    a path to a JSON file written by `vantage eval run --output <path>` --
    mix and match freely (e.g. a fresh CI run's JSON against a baseline.json
    committed to the agent's own repo, with no DB involved at all; or a live
    DB run_id against a committed baseline.json). DATABASE_URL is only
    required when at least one side is a UUID (or --baseline is omitted,
    which looks up the suite's DB-marked baseline).
    """
    console = Console()

    try:
        current = asyncio.run(_load_run_arg(current_run_id, suite))
        baseline = asyncio.run(_load_run_arg(baseline_run_id, suite))
    except _CompareLoadError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(2) from e

    if current is None or baseline is None:
        console.print(
            f"[red]Could not load baseline or current run[/red] "
            f"(no run {current_run_id!r}, and no baseline marked for suite {suite!r} "
            f"— run with --mark-baseline first, pass --baseline explicitly, or point at a "
            f"JSON file from `vantage eval run --output`)"
        )
        raise SystemExit(2)

    report = detect_regressions(baseline, current)
    _print_regression_report(report, console)

    if fail_on_regression and report.has_regressions:
        raise SystemExit(1)


class _CompareLoadError(Exception):
    """A run/baseline argument was malformed or DB access was needed but unavailable."""


async def _load_run_arg(token: str | None, suite_name: str) -> "SuiteRun | None":
    """Resolve one `compare` argument (a UUID, a JSON file path, or None
    for 'the suite's marked baseline') into a SuiteRun, or None if not found."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from vantage_eval.models import SuiteRun
    from vantage_eval.regression.loader import db_run_to_suite_run, load_baseline_run, load_run

    if token is not None and Path(token).is_file():
        return SuiteRun.model_validate_json(Path(token).read_text())

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        where = (
            f"run ID {token!r}" if token else "the suite's marked baseline (no --baseline given)"
        )
        raise _CompareLoadError(
            f"DATABASE_URL required to look up {where} (or pass a path to a JSON file instead)"
        )

    if token is not None:
        try:
            run_uuid = UUID(token)
        except ValueError as e:
            raise _CompareLoadError(
                f"{token!r} is neither an existing file nor a valid run ID"
            ) from e

    engine = persistence.get_engine(db_url)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            db_run = (
                await load_run(session, run_uuid)
                if token is not None
                else await load_baseline_run(session, suite_name)
            )
            return db_run_to_suite_run(db_run) if db_run is not None else None
    finally:
        await engine.dispose()


def _print_regression_report(report, console: Console):
    console.print()
    console.print("[bold]Regression Report[/bold]")
    console.print(f"Baseline: {report.baseline_run_id[:8]}  Current: {report.current_run_id[:8]}")
    delta_pct = report.pass_rate_delta * 100
    delta_style = "green" if delta_pct >= 0 else "red"
    console.print(
        f"Pass rate: {report.baseline_pass_rate:.1%} -> {report.current_pass_rate:.1%} "
        f"([{delta_style}]{delta_pct:+.1f}pp[/{delta_style}])"
    )
    console.print()

    counts: dict[str, int] = {}
    for c in report.changes:
        counts[c.change_type] = counts.get(c.change_type, 0) + 1

    summary = Table(show_header=False, box=None)
    summary.add_column(style="dim")
    summary.add_column()
    for k in ["regression", "improvement", "stable_pass", "stable_fail", "new", "removed"]:
        v = counts.get(k, 0)
        if k == "regression" and v > 0:
            color = "red"
        elif k == "improvement" and v > 0:
            color = "green"
        else:
            color = "dim"
        summary.add_row(k, f"[{color}]{v}[/{color}]")
    console.print(summary)

    if report.regressions:
        console.print()
        console.print("[bold red]Regressions:[/bold red]")
        for c in report.regressions:
            failures = ", ".join(c.current_deterministic_failures) or "llm judge"
            console.print(f"  [red]✗[/red] {c.external_id}: {failures}")

    if report.improvements:
        console.print()
        console.print("[bold green]Improvements:[/bold green]")
        for c in report.improvements:
            console.print(f"  [green]✓[/green] {c.external_id}")


def _current_git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "unknown"


def _print_scorecard(run, console: Console):
    s = run.summary
    console.print()
    console.print("[bold]Scorecard[/bold]")

    table = Table(show_header=False, box=None)
    table.add_column(style="dim")
    table.add_column()
    table.add_row("Total scenarios", str(s.total_scenarios))
    table.add_row("Known failing", f"[yellow]{s.known_failing}[/yellow]" if s.known_failing else "0")
    table.add_row("Passed", f"[green]{s.passed}[/green]")
    table.add_row("Failed", f"[red]{s.failed}[/red]" if s.failed else "0")
    table.add_row("Effective pass rate", f"{s.pass_rate:.1%} ({s.passed}/{s.total})")
    if s.avg_llm_score is not None:
        table.add_row("Avg LLM score", f"{s.avg_llm_score:.2f}")
    table.add_row("Judge cost", f"${s.total_judge_cost_usd:.4f}")
    table.add_row("Duration", f"{s.duration_seconds:.1f}s")
    console.print(table)

    if s.failed > 0:
        console.print()
        console.print("[bold red]Failed scenarios:[/bold red]")
        for r in run.results:
            if not r.known_failing and not r.passed:
                console.print(f"  [red]✗[/red] {r.external_id}: {'; '.join(_failure_reasons(r)) or 'unknown'}")

    if s.known_failing > 0:
        console.print()
        console.print("[bold yellow]Known failing (excluded from pass rate):[/bold yellow]")
        for r in run.results:
            if r.known_failing:
                status = "[green]now passing[/green]" if r.passed else "still failing"
                console.print(f"  [yellow]⚠[/yellow] {r.external_id} ({status}): {'; '.join(_failure_reasons(r)) or 'n/a'}")


def _failure_reasons(r) -> list[str]:
    reasons = [d.detail for d in r.deterministic_results if not d.passed and d.detail]
    if r.llm_judge_score is not None and r.llm_judge_score < 4:
        reasons.append(f"llm score {r.llm_judge_score}")
    if r.latency_within_budget is False:
        reasons.append("latency over budget")
    return reasons
