"""Scorer protocol for grading agent outputs against a scenario's rubric.

Mirrors the AgentAdapter split in agents/base.py: a Protocol, not an ABC, so any
class with the right method signature counts without explicit inheritance.
Concrete scorers arrive in later prompts — DeterministicScorer runs
rubric.hard_checks against a fixed registry of check functions, LLMJudgeScorer
renders rubric.llm_judge_prompt and calls a judge model — and both conform to
this Protocol so the runner can invoke them uniformly.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from vantage_eval.models import AgentOutput, Scenario


@runtime_checkable
class Scorer(Protocol):
    """Any class implementing score(scenario, output) -> partial result fields."""

    def score(self, scenario: Scenario, output: AgentOutput) -> dict[str, Any]:
        """Score one scenario's output. Returns fields to merge into a ScenarioResult."""
        ...
