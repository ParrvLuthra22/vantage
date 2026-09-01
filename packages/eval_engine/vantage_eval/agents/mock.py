"""Deterministic mock agent used by CI and unit tests."""
from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from vantage_eval.agents.base import AgentAdapter
from vantage_eval.models import AgentOutput


class MockAdapter(AgentAdapter):
    """
    Fake orchestrator that routes based on simple string matching.
    Used in CI to exercise the eval engine without needing a real agent.
    """

    def __init__(self, latency_ms: int = 50) -> None:
        self.latency_ms = latency_ms

    def invoke(self, input: str, context: dict[str, Any]) -> AgentOutput:
        time.sleep(self.latency_ms / 1000)

        lower = input.lower()

        if "meeting" in lower or "schedule" in lower or "calendar" in lower:
            return AgentOutput(
                routed_agent="calendar_agent",
                extracted_entities={"subject": "extracted", "action": "create"},
                latency_ms=self.latency_ms,
                trace_id=uuid4(),
            )
        if "email" in lower:
            return AgentOutput(
                routed_agent="email_agent",
                extracted_entities={"recipient": "extracted"},
                latency_ms=self.latency_ms,
                trace_id=uuid4(),
            )
        if "ignore" in lower and ("instruction" in lower or "prompt" in lower):
            return AgentOutput(
                routed_agent="REFUSE",
                extracted_entities={},
                latency_ms=self.latency_ms,
                trace_id=uuid4(),
                reasoning="Detected prompt injection attempt.",
            )

        return AgentOutput(
            routed_agent="chat_agent",
            extracted_entities={},
            latency_ms=self.latency_ms,
            trace_id=uuid4(),
        )
