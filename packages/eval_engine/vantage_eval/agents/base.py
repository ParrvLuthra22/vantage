"""Adapter protocol for invoking agents under test.

Agent adapters decouple the eval engine from any specific agent implementation.
Real usage plugs in VesperAdapter (calls Vesper's orchestrator in-process).
CI plugs in MockAdapter (deterministic outputs, no external dependencies).
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from vantage_eval.models import AgentOutput


@runtime_checkable
class AgentAdapter(Protocol):
    """Any class implementing invoke(input, context) -> AgentOutput."""

    def invoke(self, input: str, context: dict[str, Any]) -> AgentOutput:
        """Run the agent once. Must be synchronous for CLI simplicity."""
        ...
