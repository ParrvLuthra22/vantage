"""Smoke tests for VesperAdapter.

VesperAdapter defers every `vesper`-package import to the first invoke()
call (see `_ensure_ready` in vantage_eval/agents/vesper.py), so it must be
constructible with no Vesper install present. Full behavior tests that
exercise a real Planner run live below, gated on vesper being importable;
deeper iteration on routing accuracy is P32's job.

The PLANNER_FAILURE/ADAPTER_ERROR/retry tests below don't need a real
Vesper install: they replace `adapter._ensure_ready`, `adapter._planner`,
`adapter._event_bus`, and `adapter._ToolCallStartedEvent` with fakes that
mimic just enough of `orchestrator.planner.Planner` and `bus.event_bus`'s
surface (an async `.run()` and a `.subscribe(event_type, handler)` an
attempt's fake Planner can push a fake `ToolCallStartedEvent` through) to
drive `VesperAdapter._invoke_async`'s real retry loop.
"""
import pytest
from vantage_eval.agents.vesper import VesperAdapter


class _FakePlannerResult:
    def __init__(self, text: str = "", aborted: bool = False):
        self.text = text
        self.aborted = aborted


class _FakeToolCallStartedEvent:
    def __init__(self, tool_name: str, arguments: dict):
        self.tool_name = tool_name
        self.arguments = arguments


class _FakeSubscriptionToken:
    def unsubscribe(self) -> None:
        pass


class _FakeEventBus:
    """Records the handler VesperAdapter._run_planner_once subscribes so a
    fake Planner.run() can fire a fake ToolCallStartedEvent through it,
    mimicking a real tool call happening mid-turn."""

    def __init__(self):
        self._handler = None

    def subscribe(self, event_type, handler):
        self._handler = handler
        return _FakeSubscriptionToken()

    async def fire_tool_call(self, tool_name: str, arguments: dict) -> None:
        assert self._handler is not None, "no handler subscribed yet"
        await self._handler(_FakeToolCallStartedEvent(tool_name, arguments))


class _FakePlanner:
    """Each call to .run() pops and applies the next entry in `outcomes`:
    an exception instance is raised, anything else is treated as an async
    callable `(event_bus) -> PlannerResult`."""

    def __init__(self, event_bus: _FakeEventBus, outcomes: list):
        self._event_bus = event_bus
        self._outcomes = list(outcomes)
        self.call_count = 0

    async def run(self, **kwargs):
        self.call_count += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return await outcome(self._event_bus)


def _wire_fake_planner(adapter: VesperAdapter, outcomes: list) -> _FakePlanner:
    """Bypass _ensure_ready (which imports the real vesper package) and
    install a fake Planner + event bus in its place."""
    adapter._ensure_ready = lambda: None
    event_bus = _FakeEventBus()
    planner = _FakePlanner(event_bus, outcomes)
    adapter._event_bus = event_bus
    adapter._ToolCallStartedEvent = _FakeToolCallStartedEvent
    adapter._planner = planner
    return planner


def test_adapter_constructs():
    """Adapter can be constructed without errors, without vesper installed."""
    adapter = VesperAdapter()
    assert adapter is not None


def test_adapter_defers_vesper_import_until_invoke():
    """No Planner is built at construction time — only on first invoke()."""
    adapter = VesperAdapter()
    assert adapter._planner is None


def test_adapter_retries_on_planner_failure(monkeypatch):
    """Adapter should retry once when the first attempt looks like Vesper's
    ModelRouter exhausting both tiers (aborted, zero tool calls), then
    recover on a successful second attempt."""
    monkeypatch.setattr(VesperAdapter, "RETRY_BACKOFF_S", 0)

    async def _first_attempt_router_exhausted(event_bus):
        return _FakePlannerResult(text="Sir, I'm having trouble thinking right now.", aborted=True)

    async def _second_attempt_succeeds(event_bus):
        await event_bus.fire_tool_call("search_web", {"query": "weather"})
        return _FakePlannerResult(text="", aborted=False)

    adapter = VesperAdapter()
    planner = _wire_fake_planner(
        adapter, [_first_attempt_router_exhausted, _second_attempt_succeeds]
    )

    out = adapter.invoke("test input", {})

    assert planner.call_count == 2, "should retry exactly once"
    assert out.routed_agent == "search_web", f"expected recovery, got {out.routed_agent}"


def test_adapter_returns_planner_failure_after_exhausted_retries(monkeypatch):
    """Adapter should surface PLANNER_FAILURE (not ADAPTER_ERROR) once every
    retry also looks like router exhaustion."""
    monkeypatch.setattr(VesperAdapter, "RETRY_BACKOFF_S", 0)

    async def _router_exhausted(event_bus):
        return _FakePlannerResult(text="Sir, I'm having trouble thinking right now.", aborted=True)

    adapter = VesperAdapter()
    planner = _wire_fake_planner(adapter, [_router_exhausted, _router_exhausted])

    out = adapter.invoke("test input", {})

    assert planner.call_count == 2
    assert out.routed_agent == "PLANNER_FAILURE"
    assert "router" in (out.reasoning or "").lower()


def test_adapter_returns_adapter_error_on_exception(monkeypatch):
    """A genuine crash in this adapter's own code (not Vesper's LLM) must
    stay distinctly ADAPTER_ERROR, never PLANNER_FAILURE."""
    monkeypatch.setattr(VesperAdapter, "RETRY_BACKOFF_S", 0)

    adapter = VesperAdapter()
    planner = _wire_fake_planner(
        adapter, [RuntimeError("import blew up"), RuntimeError("import blew up")]
    )

    out = adapter.invoke("test input", {})

    assert planner.call_count == 2, "adapter exceptions get the same one retry"
    assert out.routed_agent == "ADAPTER_ERROR"
    assert "RuntimeError" in (out.reasoning or "")


@pytest.mark.integration
def test_direct_handler_tools_are_neutralized():
    """Guards against a real regression: an early P32 run's ambiguous_007
    scenario actually created a note in the real macOS Notes app via
    run_applescript, because tools.creator's direct-handler tools
    (run_shell/run_applescript/research/write_script — registered
    transitively by importing orchestrator.planner, see tools/__init__.py)
    are NOT bus-routed and so were never covered by the ActionRequestEvent
    mock. Every ToolSpec with a direct handler must be neutralized."""
    pytest.importorskip("orchestrator.planner")
    from tools.registry import get_registry

    adapter = VesperAdapter()
    adapter._ensure_ready()

    registry = get_registry()
    direct_handler_tools = [
        t for t in registry.list_all(enabled_only=False) if t.handler is not None
    ]
    assert direct_handler_tools, "expected at least one direct-handler tool to check against"

    for tool_spec in direct_handler_tools:
        import asyncio

        result = asyncio.run(tool_spec.handler({"probe": "test"}, {}))
        assert "eval-mock" in str(result), (
            f"{tool_spec.name}'s handler was not neutralized — it would execute for real"
        )


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
