"""Adapter wrapping the real Vesper orchestrator for evaluation.

Vesper is NOT a LangGraph state graph with a discrete per-domain agent for
each intent (calendar_agent / email_agent / weather_agent / ...). Its real
architecture (orchestrator/brain.py, orchestrator/planner.py) is an LLM
tool-calling loop: `Planner.run(user_text, ...)` feeds the user's text plus
a JSON-schema tool catalog to the model, executes whatever tools it calls
(each gated by `guardian.gate.Guardian`), and loops until the model returns
plain text or `MAX_ITERATIONS` is hit. There is no "routed_agent" concept in
Vesper's own state — `orchestrator/langgraph_brain.py` does contain a
LangGraph StateGraph with that shape, but its module docstring marks it
DEPRECATED/UNUSED (superseded by the Planner); nothing in Vesper imports it.

This adapter therefore treats the *first tool the Planner calls* as the
routing decision (`routed_agent`) and that call's arguments as
`extracted_entities`. A turn that never calls a tool (the model just
answers) routes to the sentinel "chat_agent". This is a reasonable analogue
for the orchestrator_v1 golden scenarios, not an exact match — Vesper's
actual tool catalog (open_app, search_web, set_volume, ...) doesn't cover
calendar/email/notes/reminders domains yet, so most of those scenarios are
expected to score poorly until P32 either registers matching tools or the
scenario expectations are revised.

Design principles:
  1. NO side effects. Two mechanisms make every tool call inert, for the
     life of this adapter:
       - `Guardian.check` is replaced with a function that always returns
         ALLOW, so "confirm"/"dangerous"-tier tools (close_app, lock_screen,
         ...) never block on `ConfirmationRequestedEvent`/120s timeout
         waiting for a UI that doesn't exist in eval.
       - Every builtin tool is bus-routed (ToolSpec.target_agent/action —
         see tools/builtin.py), not backed by a local handler. With no
         SystemAgent/WebSearchAgent subscribed to run them, an `ActionRequestEvent`
         would otherwise time out after 30s with no result. Instead we
         subscribe a mock responder that immediately answers every
         `ActionRequestEvent` with a canned successful `ActionResultEvent` —
         so nothing ever touches AppleScript, a browser, or any other real
         side effect.
  2. Capture the routing decision by listening for the Planner's own
     `ToolCallStartedEvent` (tool_name + arguments) during the call, rather
     than inspecting a state dict that doesn't exist in this architecture.
  3. Include a Vantage trace_id in the output so we can jump from eval
     result to full trace in the dashboard.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import UUID

import vantage
from vantage import span

from vantage_eval.agents.base import AgentAdapter
from vantage_eval.models import AgentOutput


class VesperAdapter(AgentAdapter):
    """
    Invokes Vesper's Planner (the LLM tool-calling loop that replaced the
    old rule-based/LangGraph routing) with every side-effecting tool call
    neutralized, then reports the first tool it called as the routing
    decision.

    All `vesper`-package imports are deferred to the first `invoke()` call
    (via `_ensure_ready`) so constructing this adapter never requires
    `vesper` to be installed — only running an eval against it does.
    """

    def __init__(
        self,
        vantage_api_url: str = "http://localhost:8000",
        vantage_api_key: str = "dev-key-change-me",
    ) -> None:
        # Initialize the Vantage SDK once so Vesper's own spans get captured
        # into the same trace as our eval run.
        if vantage.get_client() is None:
            vantage.init(api_key=vantage_api_key, base_url=vantage_api_url, project="vesper-eval")

        self._planner: Any = None
        self._event_bus: Any = None
        self._ToolCallStartedEvent: Any = None

    def _ensure_ready(self) -> None:
        """Build the Planner + supporting objects once, lazily.

        Deliberately builds a bare Planner instead of the full `Brain`:
        `Brain.start()` registers SystemAgent/MacOSControlAgent/WebSearchAgent,
        binds a health-check HTTP server, connects to configured MCP servers
        (Gmail/Notion/apple_pim — real OAuth/EventKit access), starts sensors,
        and immediately calls the LLM for a greeting. None of that is needed
        (or safe) for a single stateless eval turn; `Planner.run()` is
        Vesper's actual per-turn entry point (see orchestrator/brain.py's
        `handle_user_text`, which just forwards to it).
        """
        if self._planner is not None:
            return

        from bus.event_bus import get_event_bus
        from config.settings import load_config_dict
        from guardian.gate import Guardian, Verdict, VerdictType
        from llm.router import ModelRouter
        from orchestrator.planner import Planner
        from schemas.events import ActionRequestEvent, ActionResultEvent, ToolCallStartedEvent
        from tasks.queue import TaskQueue
        from tracing.tracer import Tracer

        event_bus = get_event_bus()
        guardian = Guardian(event_bus=event_bus)

        async def _auto_allow_check(tool_spec: Any, arguments: dict, context: Any = None) -> Any:
            return Verdict(VerdictType.ALLOW, reason="vantage-eval: guardian bypassed, execution mocked")

        # Instance-attribute assignment shadows the bound method without
        # touching the Guardian class — every other Guardian in this process
        # (there are none here, but future callers) keeps normal behavior.
        guardian.check = _auto_allow_check  # type: ignore[method-assign]

        async def _mock_action_handler(event: ActionRequestEvent) -> None:
            await event_bus.emit(
                ActionResultEvent(
                    action=event.action,
                    success=True,
                    result={
                        "mocked": True,
                        "target_agent": event.target_agent,
                        "action": event.action,
                        "parameters": dict(event.parameters),
                    },
                    correlation_id=event.correlation_id,
                    source="vantage-eval-mock",
                )
            )

        # EventBus is a process-wide singleton (bus/event_bus.py) — subscribe
        # once for this adapter's lifetime rather than per-invocation.
        event_bus.subscribe(ActionRequestEvent, _mock_action_handler)

        config = load_config_dict()
        router = ModelRouter(config=config)
        tracer = Tracer(config=config)
        task_queue = TaskQueue(event_bus=event_bus)

        self._planner = Planner(
            router=router,
            guardian=guardian,
            event_bus=event_bus,
            tracer=tracer,
            task_queue=task_queue,
            config=config,
        )
        self._event_bus = event_bus
        self._ToolCallStartedEvent = ToolCallStartedEvent

    def invoke(self, input: str, context: dict[str, Any]) -> AgentOutput:
        start = time.monotonic()
        try:
            self._ensure_ready()
            return asyncio.run(self._invoke_async(input, context, start))
        except Exception as e:
            latency_ms = int((time.monotonic() - start) * 1000)
            return AgentOutput(
                routed_agent="ADAPTER_ERROR",
                latency_ms=latency_ms,
                reasoning=f"{type(e).__name__}: {e}",
            )

    async def _invoke_async(self, input: str, context: dict[str, Any], start: float) -> AgentOutput:
        tool_calls: list[dict[str, Any]] = []

        async def _collect(event: Any) -> None:
            tool_calls.append({"name": event.tool_name, "arguments": dict(event.arguments)})

        token = self._event_bus.subscribe(self._ToolCallStartedEvent, _collect)
        try:
            with span("eval.vesper_invocation", attributes={"input": input, "context": context}):
                result = await self._planner.run(
                    user_text=input,
                    recent_context=context.get("prior_turns") or [],
                    active_app=self._first_active_app(context),
                )
        finally:
            token.unsubscribe()

        latency_ms = int((time.monotonic() - start) * 1000)
        trace_id = self._current_trace_id()

        if tool_calls:
            routed_agent = tool_calls[0]["name"]
            extracted_entities = tool_calls[0]["arguments"]
        elif result.aborted:
            # aborted with zero tool calls only happens when the very first
            # ModelRouter.complete() call itself failed (RouterError) — e.g.
            # a missing GROQ_API_KEY — never from a legitimate "gave up after
            # MAX_ITERATIONS" abort, which always has at least one tool call
            # in its trace.
            routed_agent = "ADAPTER_ERROR"
            extracted_entities = {}
        else:
            routed_agent = "chat_agent"
            extracted_entities = {}

        return AgentOutput(
            routed_agent=routed_agent,
            extracted_entities=extracted_entities,
            reasoning=result.text if result.aborted else None,
            latency_ms=latency_ms,
            trace_id=trace_id,
            raw_output=str(
                {"reply": result.text, "aborted": result.aborted, "tool_calls": tool_calls}
            )[:2000],
        )

    @staticmethod
    def _first_active_app(context: dict[str, Any]) -> str | None:
        apps = context.get("active_apps") or []
        return apps[0] if apps else None

    @staticmethod
    def _current_trace_id() -> UUID | None:
        from vantage.context import current_trace_id

        return current_trace_id.get()
