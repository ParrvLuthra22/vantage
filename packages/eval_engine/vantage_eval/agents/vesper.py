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
  1. NO side effects. Three mechanisms make every tool call inert, for the
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
         `ActionRequestEvent` with a canned successful `ActionResultEvent`.
       - Every DIRECT-HANDLER tool in the registry (ToolSpec.handler is not
         None) has that handler replaced with a no-op. This is NOT limited to
         tools.builtin: importing `tools` (which `orchestrator.planner` does
         transitively) also imports tools.devtools/tools.creator/tools.weather
         for their own registration side effects (see tools/__init__.py), and
         tools.creator registers run_shell/run_applescript with REAL direct
         handlers — dangerous-tier, but still executed with no confirmation
         once Guardian is bypassed above. This was discovered the hard way:
         an early P32 run's `ambiguous_007` ("add another one") actually
         created a blank note in the real Notes app via run_applescript
         before this patch existed. The bus-routed mock above does not cover
         this class of tool at all, since a direct-handler tool never emits
         an ActionRequestEvent — hence this separate, registry-wide patch.
  2. Capture the routing decision by listening for the Planner's own
     `ToolCallStartedEvent` (tool_name + arguments) during the call, rather
     than inspecting a state dict that doesn't exist in this architecture.
  3. Distinguish PLANNER_FAILURE from ADAPTER_ERROR. Vesper's
     `llm.router.ModelRouter.complete()` never raises when both the primary
     (Groq) and fallback (local Ollama) tiers fail — it returns a
     `RouterError` (llm/types.py), which `Planner.run()` folds into
     `PlannerResult(aborted=True, text=<user-facing message>)` with zero
     tool calls made yet (a legitimate MAX_ITERATIONS abort always has at
     least one tool call, since every iteration up to that point needed a
     successful completion to keep the loop going). That "aborted with no
     tool calls" shape is therefore Vesper's own LLM failing, not a bug in
     this file — see `_is_planner_failure`. It is retried MAX_RETRIES times
     with exponential backoff. An exception out of `Planner.run()` gets the
     same treatment only if `_is_transient_error` says it looks transient
     (and lands on PLANNER_FAILURE if it outlasts the retries); anything
     else is a bug in this file and fails fast as ADAPTER_ERROR, since a
     retry would just repeat it.
  4. Include a Vantage trace_id in the output so we can jump from eval
     result to full trace in the dashboard.
  5. ONE event loop for the adapter's whole life (see `_run`). Vesper's
     ModelRouter caches its Groq/Ollama async HTTP clients across calls; an
     async client is only valid on the loop that created it, so running each
     scenario under its own `asyncio.run()` made nearly every call after the
     first start by reusing a dead pooled connection ("Groq network error:
     Connection error."), then wait out the local fallback's 60s timeout.
"""
from __future__ import annotations

import asyncio
import atexit
import contextvars
import logging
import re
import time
from typing import Any
from uuid import UUID

import vantage
from vantage import span

from vantage_eval.agents.base import AgentAdapter
from vantage_eval.models import AgentOutput

log = logging.getLogger(__name__)

#: Substrings (matched case-insensitively against "ExcType: message") that mark
#: an exception as a transient network/provider hiccup worth retrying rather
#: than a bug in this adapter. Bare status codes are matched as whole numbers,
#: so "processed 4290 rows" doesn't count as an HTTP 429.
TRANSIENT_GROQ_ERROR_PATTERNS = (
    "network error",
    "connection error",
    "too many requests",
    "429",
    "503",
    "bad gateway",
    "timeout",
    "timed out",
)


def _is_transient_error(e: Exception) -> bool:
    """True if `e` looks like a transient network/provider failure (worth a
    retry) rather than a deterministic bug (a retry would only repeat it)."""
    if isinstance(e, (ConnectionError, TimeoutError)):
        return True
    msg = f"{type(e).__name__}: {e}".lower()
    return any(
        re.search(rf"\b{p}\b", msg) if p.isdigit() else p in msg
        for p in TRANSIENT_GROQ_ERROR_PATTERNS
    )


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

    #: Vesper's own ModelRouter exhausted both its primary (Groq) and
    #: fallback (local Ollama) tiers for this turn — Vesper's problem or
    #: infra flakiness, not a bug in this adapter's code.
    PLANNER_FAILURE = "PLANNER_FAILURE"
    #: This adapter's own code broke (import error, event-bus wiring, a bug
    #: in this file) — distinct from Vesper's LLM failing.
    ADAPTER_ERROR = "ADAPTER_ERROR"

    #: Retries after the first attempt (so three attempts in total). Groq
    #: does have real transient failures (429s, timeouts, 5xx) that a fresh
    #: attempt usually clears; Vesper's own router doesn't retry them
    #: (`AsyncGroq(max_retries=0)`) and its local Ollama fallback has never
    #: rescued a turn in this environment (it read-times-out at 60s).
    MAX_RETRIES = 2
    #: Doubled on each retry: 2s before the 2nd attempt, 4s before the 3rd.
    RETRY_BACKOFF_S = 2.0

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
        self._runner: asyncio.Runner | None = None

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
        from tools.registry import get_registry
        from tracing.tracer import Tracer

        # Neutralize every direct-handler tool in the registry BEFORE
        # constructing the Planner. Importing orchestrator.planner above
        # already imported tools.builtin, which (via tools/__init__.py)
        # transitively registered tools.devtools/tools.creator/tools.weather
        # too — including tools.creator's run_shell/run_applescript, both
        # DANGEROUS-tier tools with real handlers. Bus-routed tools
        # (target_agent/action, handler=None) are unaffected here; those are
        # covered by the ActionRequestEvent mock below instead.
        registry = get_registry()
        for tool_spec in registry.list_all(enabled_only=False):
            if tool_spec.handler is not None:
                tool_spec.handler = self._make_mock_handler(tool_spec.name)

        event_bus = get_event_bus()
        guardian = Guardian(event_bus=event_bus)

        async def _auto_allow_check(tool_spec: Any, arguments: dict, context: Any = None) -> Any:
            return Verdict(
                VerdictType.ALLOW, reason="vantage-eval: guardian bypassed, execution mocked"
            )

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

    def _run(self, coro: Any) -> Any:
        """Run `coro` on this adapter's single long-lived event loop.

        Deliberately not `asyncio.run(coro)`, which builds and *closes* a new
        loop on every call. The Planner's ModelRouter (built once, in
        `_ensure_ready`) caches its Groq/Ollama async HTTP clients, and a
        pooled connection belongs to the loop that opened it — so after the
        first scenario every call began by reusing a dead connection and
        failing with "Connection error". In the 42.5% P32 run that was 36 of
        40 first attempts, each followed by a ~60s wait on the Ollama
        fallback. A `Runner` keeps one loop (and so one valid connection
        pool) alive across invocations.

        Each call still gets a fresh copy of the context, matching
        `asyncio.run`'s isolation, so nothing set during one scenario (e.g.
        Vantage's current trace id) can leak into the next.
        """
        if self._runner is None:
            self._runner = asyncio.Runner()
            atexit.register(self.close)
        return self._runner.run(coro, context=contextvars.copy_context())

    def close(self) -> None:
        """Close the adapter's event loop. Safe to call more than once."""
        runner, self._runner = self._runner, None
        if runner is not None:
            runner.close()

    async def _invoke_isolated(self, input: str, context: dict[str, Any]) -> AgentOutput:
        """`_invoke_async`, then cancel anything it left running.

        `asyncio.run` cancelled leftover tasks when it tore its loop down; a
        loop that lives across scenarios has to do that explicitly, or a
        stray task from one scenario could keep running (and emit events)
        during the next.
        """
        try:
            return await self._invoke_async(input, context)
        finally:
            current = asyncio.current_task()
            stray = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
            for task in stray:
                task.cancel()
            if stray:
                await asyncio.gather(*stray, return_exceptions=True)

    def invoke(self, input: str, context: dict[str, Any]) -> AgentOutput:
        try:
            return self._run(self._invoke_isolated(input, context))
        except Exception as e:
            # Last-resort net: _invoke_async's own retry loop below already
            # converts the expected failure surface (a Planner exception, or
            # a RouterError-shaped abort) into ADAPTER_ERROR/PLANNER_FAILURE
            # outputs. This only fires if something outside that loop's own
            # try breaks — invoke() must never raise and crash the suite
            # runner, matching runner._run_one's philosophy for adapters.
            log.error(f"Unhandled exception escaped the retry loop: {type(e).__name__}: {e}")
            return AgentOutput(
                routed_agent=self.ADAPTER_ERROR,
                latency_ms=0,
                reasoning=f"{type(e).__name__}: {e}",
            )

    async def _invoke_async(self, input: str, context: dict[str, Any]) -> AgentOutput:
        for attempt in range(self.MAX_RETRIES + 1):
            start = time.monotonic()
            try:
                self._ensure_ready()
                result, tool_calls = await self._run_planner_once(input, context)
            except Exception as e:
                latency_ms = int((time.monotonic() - start) * 1000)
                transient = _is_transient_error(e)
                log.warning(
                    f"invoke exception on attempt {attempt} (transient={transient}): "
                    f"{type(e).__name__}: {e}"
                )
                if transient and attempt < self.MAX_RETRIES:
                    await asyncio.sleep(self._backoff_s(attempt))
                    continue
                return AgentOutput(
                    # A transient error that outlasts every retry is the same
                    # kind of failure as router exhaustion — infrastructure,
                    # not this adapter — so it shares that label.
                    routed_agent=self.PLANNER_FAILURE if transient else self.ADAPTER_ERROR,
                    latency_ms=latency_ms,
                    reasoning=f"{type(e).__name__}: {e}",
                    trace_id=self._current_trace_id(),
                )

            latency_ms = int((time.monotonic() - start) * 1000)
            trace_id = self._current_trace_id()

            if self._is_planner_failure(result, tool_calls):
                log.info(f"PLANNER_FAILURE on attempt {attempt} (input={input[:60]!r})")
                if attempt < self.MAX_RETRIES:
                    await asyncio.sleep(self._backoff_s(attempt))
                    continue
                return AgentOutput(
                    routed_agent=self.PLANNER_FAILURE,
                    latency_ms=latency_ms,
                    reasoning=(
                        "Vesper's model router exhausted its primary and fallback "
                        "LLM tiers for this turn"
                    ),
                    trace_id=trace_id,
                    raw_output=str({"reply": result.text, "aborted": result.aborted})[:2000],
                    final_reply=result.text or None,
                )

            if tool_calls:
                routed_agent = tool_calls[0]["name"]
                extracted_entities = tool_calls[0]["arguments"]
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
                # routed_agent/extracted_entities above describe only the FIRST
                # tool call; these carry the whole turn (see AgentOutput).
                tool_sequence=[c["name"] for c in tool_calls],
                tool_calls=tool_calls,
                final_reply=result.text or None,
            )

        # Unreachable: every branch above returns by the final attempt
        # (attempt == MAX_RETRIES never takes a `continue` path). Present
        # only to make the function's control flow explicit to type checkers.
        raise AssertionError("retry loop exited without returning")

    async def _run_planner_once(
        self, input: str, context: dict[str, Any]
    ) -> tuple[Any, list[dict[str, Any]]]:
        """Run the Planner exactly once, returning its `PlannerResult`
        alongside every tool call observed via `ToolCallStartedEvent` during
        that single call."""
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
        return result, tool_calls

    def _backoff_s(self, attempt: int) -> float:
        """Seconds to wait after a failed `attempt` (0-based): 2s, 4s, ..."""
        return self.RETRY_BACKOFF_S * (2**attempt)

    @staticmethod
    def _is_planner_failure(result: Any, tool_calls: list[dict[str, Any]]) -> bool:
        """True iff this `PlannerResult` reflects Vesper's ModelRouter
        exhausting both tiers (a `RouterError`), not a legitimate
        MAX_ITERATIONS abort.

        Vesper's `orchestrator.planner.Planner.run()` never raises or
        exposes a distinct exception for router exhaustion: internally,
        `ModelRouter.complete()` *returns* (never raises) a `RouterError`
        when both the primary (Groq) and fallback (Ollama) tiers fail
        (llm/router.py, llm/types.py), and `Planner.run()` folds that
        straight into `PlannerResult(aborted=True, text=response.user_message)`
        with no tool calls made yet. A MAX_ITERATIONS abort, by contrast, is
        only reachable after every iteration up to the limit produced tool
        calls from a *successful* completion — so it always has at least one
        tool call in its trace. "Aborted with zero tool calls" is therefore
        the only externally-visible signal that distinguishes a router
        failure from a normal (if unresolved) run, since `RouterError` itself
        never escapes `Planner.run()`.
        """
        return bool(result.aborted) and not tool_calls

    @staticmethod
    def _make_mock_handler(tool_name: str):
        """A no-op replacement for one ToolSpec's direct handler.

        Returns a plausible-looking mocked string (matching the
        `_mock_action_handler` bus-routed mock's spirit) rather than raising
        or returning None, so the model's next planning iteration sees a
        normal tool result and can keep reasoning coherently instead of
        hitting an unexplained error.
        """

        async def _mock(arguments: dict, context: dict) -> str:
            return f"[eval-mock] {tool_name} was not actually executed (arguments={arguments!r})"

        return _mock

    @staticmethod
    def _first_active_app(context: dict[str, Any]) -> str | None:
        apps = context.get("active_apps") or []
        return apps[0] if apps else None

    @staticmethod
    def _current_trace_id() -> UUID | None:
        from vantage.context import current_trace_id

        return current_trace_id.get()
