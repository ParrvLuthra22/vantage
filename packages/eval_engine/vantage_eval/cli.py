"""Vantage CLI: `vantage eval run|list|show`."""
from __future__ import annotations

import asyncio
import subprocess

import click
from rich.console import Console
from rich.table import Table

from vantage_eval import persistence
from vantage_eval.agents.mock import MockAdapter
from vantage_eval.loader import load_suite
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
def eval_run(
    suite_path: str,
    adapter: str,
    no_judge: bool,
    judge_provider: str,
    verbose: bool,
    min_llm_pass: float,
    mark_baseline: bool,
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

    run_id = asyncio.run(persistence.persist_run(suite, run, mark_baseline=mark_baseline))
    if run_id is not None:
        baseline_note = " [bold yellow](marked as baseline)[/bold yellow]" if mark_baseline else ""
        console.print(f"[dim]Persisted as eval run {run_id}{baseline_note}[/dim]")
    else:
        console.print("[dim]Could not persist this run to Postgres (see warnings above) — scorecard below is still accurate[/dim]")

    _print_scorecard(run, console)

    # Exit code = number of failed scenarios (useful for CI gating)
    raise SystemExit(0 if run.summary.failed == 0 else 1)


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
