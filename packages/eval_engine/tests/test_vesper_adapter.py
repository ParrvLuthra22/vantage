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
import asyncio
import contextvars

import pytest
from vantage_eval.agents.vesper import VesperAdapter, _is_transient_error


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
    planner = _wire_fake_planner(
        adapter, [_router_exhausted, _router_exhausted, _router_exhausted]
    )

    out = adapter.invoke("test input", {})

    assert planner.call_count == 3, "MAX_RETRIES=2 means three attempts in total"
    assert out.routed_agent == "PLANNER_FAILURE"
    assert "router" in (out.reasoning or "").lower()


def test_adapter_records_the_whole_turn_not_just_the_first_tool():
    """routed_agent/extracted_entities describe only the first call; the rest
    of the turn (and the reply) must survive for the judge and hard checks."""

    async def _two_tools_then_reply(event_bus):
        await event_bus.fire_tool_call("get_volume", {})
        await event_bus.fire_tool_call("set_volume", {"level": 60})
        return _FakePlannerResult(text="Volume increased to 60%, Sir.", aborted=False)

    adapter = VesperAdapter()
    _wire_fake_planner(adapter, [_two_tools_then_reply])

    out = adapter.invoke("turn it up a bit more", {})

    assert out.routed_agent == "get_volume", "stays the first tool, for hard_check back-compat"
    assert out.extracted_entities == {}
    assert out.tool_sequence == ["get_volume", "set_volume"]
    assert out.tool_calls == [
        {"name": "get_volume", "arguments": {}},
        {"name": "set_volume", "arguments": {"level": 60}},
    ]
    assert out.final_reply == "Volume increased to 60%, Sir."


def test_adapter_records_a_text_only_reply():
    """No tool call -> chat_agent, but the reply is what a judge needs to see
    (reasoning is deliberately still None on a non-aborted turn)."""

    async def _text_only(event_bus):
        return _FakePlannerResult(text="Sir, I can't send text messages.", aborted=False)

    adapter = VesperAdapter()
    _wire_fake_planner(adapter, [_text_only])

    out = adapter.invoke("text nisha", {})

    assert out.routed_agent == "chat_agent"
    assert out.tool_sequence == [] and out.tool_calls == []
    assert out.final_reply == "Sir, I can't send text messages."
    assert out.reasoning is None


def test_adapter_reply_is_none_when_the_agent_said_nothing():
    async def _silent(event_bus):
        await event_bus.fire_tool_call("open_app", {"name": "Notes"})
        return _FakePlannerResult(text="", aborted=False)

    adapter = VesperAdapter()
    _wire_fake_planner(adapter, [_silent])

    assert adapter.invoke("open notes", {}).final_reply is None


def test_adapter_returns_adapter_error_on_exception(monkeypatch):
    """A genuine crash in this adapter's own code (not Vesper's LLM) must
    stay distinctly ADAPTER_ERROR, never PLANNER_FAILURE — and, being
    deterministic, must fail fast instead of being retried."""
    monkeypatch.setattr(VesperAdapter, "RETRY_BACKOFF_S", 0)

    adapter = VesperAdapter()
    planner = _wire_fake_planner(
        adapter, [RuntimeError("import blew up"), RuntimeError("import blew up")]
    )

    out = adapter.invoke("test input", {})

    assert planner.call_count == 1, "a non-transient exception must not be retried"
    assert out.routed_agent == "ADAPTER_ERROR"
    assert "RuntimeError" in (out.reasoning or "")


def test_adapter_retries_on_transient_groq_error(monkeypatch):
    """The stand-in for Groq's 'network error' escaping Planner.run() as an
    exception: retried, and the second attempt's result is what's reported."""
    monkeypatch.setattr(VesperAdapter, "RETRY_BACKOFF_S", 0)

    async def _reply_only(event_bus):
        return _FakePlannerResult(text="hi", aborted=False)

    adapter = VesperAdapter()
    planner = _wire_fake_planner(
        adapter, [RuntimeError("Groq network error: Connection error."), _reply_only]
    )

    out = adapter.invoke("test", {})

    assert out.routed_agent == "chat_agent"
    assert planner.call_count == 2


def test_transient_exception_that_never_clears_is_planner_failure(monkeypatch):
    """Retries are capped (three attempts). A transient error that outlasts
    them is infrastructure failing, so it shares PLANNER_FAILURE's label —
    not ADAPTER_ERROR, which is reserved for bugs in this adapter."""
    monkeypatch.setattr(VesperAdapter, "RETRY_BACKOFF_S", 0)
    boom = RuntimeError("Error code: 503 - Service Unavailable")

    adapter = VesperAdapter()
    planner = _wire_fake_planner(adapter, [boom, boom, boom, boom])

    out = adapter.invoke("test", {})

    assert planner.call_count == 3
    assert out.routed_agent == "PLANNER_FAILURE"
    assert "503" in (out.reasoning or "")


def test_backoff_doubles_on_each_retry():
    adapter = VesperAdapter()
    assert [adapter._backoff_s(n) for n in range(3)] == [2.0, 4.0, 8.0]


@pytest.mark.parametrize(
    "exc, expected",
    [
        (RuntimeError("Groq network error: Connection error."), True),
        (RuntimeError("Groq network error: Request timed out."), True),
        (RuntimeError("Error code: 429 - Too Many Requests"), True),
        (RuntimeError("Error code: 503"), True),
        (RuntimeError("502 Bad Gateway"), True),
        (ConnectionResetError(), True),
        (asyncio.TimeoutError(), True),
        (RuntimeError("processed 4290 rows"), False),  # digits inside a longer number
        (ImportError("No module named 'orchestrator'"), False),
        (KeyError("tool"), False),
        (ValueError("bad input"), False),
    ],
)
def test_transient_error_classification(exc, expected):
    assert _is_transient_error(exc) is expected


class _LoopAffineClient:
    """Stands in for the Groq/Ollama async clients Vesper's ModelRouter caches
    across calls: usable only on the event loop it was first used on, exactly
    like a pooled httpx connection. On any other loop it fails with the same
    message the real one produced in the P32 run."""

    def __init__(self):
        self._loop = None

    def use(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif loop is not self._loop:
            raise RuntimeError("Groq network error: Connection error.")


def test_adapter_reuses_one_event_loop_across_invocations():
    """Regression for the P32 first-attempt failures: `asyncio.run` per call
    made every scenario after the first hit a client bound to a dead loop
    (36 of 40 first attempts in the 42.5% run). With one long-lived loop, N
    invocations take N attempts — none need the retry to recover."""
    client = _LoopAffineClient()

    async def _uses_cached_client(event_bus):
        client.use()
        return _FakePlannerResult(text="ok", aborted=False)

    adapter = VesperAdapter()
    planner = _wire_fake_planner(adapter, [_uses_cached_client] * 4)
    try:
        outs = [adapter.invoke(f"scenario {i}", {}) for i in range(4)]
    finally:
        adapter.close()

    assert planner.call_count == 4, "a call needed a retry: the loop was not reused"
    assert [o.routed_agent for o in outs] == ["chat_agent"] * 4


def test_adapter_isolates_scenarios_despite_sharing_a_loop():
    """Sharing a loop must not share state: a task left running by one scenario
    is cancelled before the next, and contextvars set during one scenario
    (e.g. Vantage's current trace id) don't leak into the next."""
    marker = contextvars.ContextVar("marker", default="unset")
    stray: list[asyncio.Task] = []
    seen: list[str] = []

    async def _first(event_bus):
        marker.set("leaked")
        stray.append(asyncio.create_task(asyncio.sleep(3600)))
        return _FakePlannerResult(text="ok", aborted=False)

    async def _second(event_bus):
        seen.append(marker.get())
        return _FakePlannerResult(text="ok", aborted=False)

    adapter = VesperAdapter()
    _wire_fake_planner(adapter, [_first, _second])
    try:
        adapter.invoke("one", {})
        assert stray[0].cancelled(), "a stray task outlived its scenario"
        adapter.invoke("two", {})
    finally:
        adapter.close()

    assert seen == ["unset"], "context set in one scenario leaked into the next"


def test_adapter_close_is_idempotent_and_reopenable():
    async def _ok(event_bus):
        return _FakePlannerResult(text="ok", aborted=False)

    adapter = VesperAdapter()
    _wire_fake_planner(adapter, [_ok, _ok])
    adapter.invoke("a", {})
    adapter.close()
    adapter.close()  # second close is a no-op
    assert adapter._runner is None
    assert adapter.invoke("b", {}).routed_agent == "chat_agent"  # lazily reopens
    adapter.close()


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
