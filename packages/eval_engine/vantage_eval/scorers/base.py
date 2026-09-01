"""Scorer protocol."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from vantage_eval.models import AgentOutput, Scenario, ScenarioResult


@runtime_checkable
class Scorer(Protocol):
    def score(self, scenario: Scenario, output: AgentOutput, result: ScenarioResult) -> None:
        """Mutate `result` in place with this scorer's findings."""
        ...
