"""Smoke tests for VesperAdapter.

VesperAdapter defers every `vesper`-package import to the first invoke()
call (see `_ensure_ready` in vantage_eval/agents/vesper.py), so it must be
constructible with no Vesper install present. Full behavior tests that
exercise a real Planner run live below, gated on vesper being importable;
deeper iteration on routing accuracy is P32's job.
"""
import pytest

from vantage_eval.agents.vesper import VesperAdapter


def test_adapter_constructs():
    """Adapter can be constructed without errors, without vesper installed."""
    adapter = VesperAdapter()
    assert adapter is not None


def test_adapter_defers_vesper_import_until_invoke():
    """No Planner is built at construction time — only on first invoke()."""
    adapter = VesperAdapter()
    assert adapter._planner is None


@pytest.mark.integration
def test_adapter_invokes_vesper():
    """Requires vesper to be installed. Runs one real invocation with every
    side-effecting tool call neutralized (Guardian auto-allows; ActionRequestEvent
    calls are answered by a mock responder instead of a real agent)."""
    pytest.importorskip("orchestrator.planner")
    adapter = VesperAdapter()
    output = adapter.invoke(
        "book a meeting with priya at 3pm tomorrow",
        {"prior_turns": [], "active_apps": ["Calendar"]},
    )
    assert output.routed_agent
    assert output.routed_agent != "ADAPTER_ERROR", f"Adapter errored: {output.reasoning}"
    assert output.latency_ms > 0
