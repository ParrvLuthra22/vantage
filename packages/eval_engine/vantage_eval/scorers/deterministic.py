"""Deterministic (rule-based) scoring.

Each check function has the signature (scenario, output) -> DeterministicResult.
Register with @check("check_name"). The DeterministicScorer runs every check
named in the scenario's rubric.hard_checks list.

Why a registry over a class hierarchy: adding a new check is one decorated
function, not a new subclass wired into some dispatch table by hand. pytest's
fixtures, Django's admin site, and Flask's route table all use this same
pattern — the registration site IS the definition site, so "what checks exist"
is answered by grepping for @check rather than tracing an inheritance tree.
"""
from __future__ import annotations

import re
from collections.abc import Callable

from vantage_eval.models import AgentOutput, DeterministicResult, Scenario, ScenarioResult

CheckFn = Callable[[Scenario, AgentOutput], DeterministicResult]

_CHECKS: dict[str, CheckFn] = {}


def check(name: str):
    """Decorator: register a check function under `name`."""
    def wrap(fn: CheckFn) -> CheckFn:
        if name in _CHECKS:
            raise ValueError(f"Check name already registered: {name}")
        _CHECKS[name] = fn
        return fn
    return wrap


def resolve_check(name: str) -> CheckFn:
    """Resolve a check name, supporting dotted attribute-access forms like
    'extracted_entities.subject_present'."""
    if name in _CHECKS:
        return _CHECKS[name]

    # Attribute-access sugar: "extracted_entities.<key>_present"
    if name.startswith("extracted_entities.") and name.endswith("_present"):
        key = name[len("extracted_entities.") : -len("_present")]
        return _entity_present_factory(key)

    if name.startswith("extracted_entities.") and name.endswith("_equals"):
        key = name[len("extracted_entities.") : -len("_equals")]
        return _entity_equals_factory(key)

    raise KeyError(f"Unknown check: {name}")


class DeterministicScorer:
    """Runs all rubric.hard_checks for a scenario and records results."""

    def score(self, scenario: Scenario, output: AgentOutput, result: ScenarioResult) -> None:
        for check_name in scenario.rubric.hard_checks:
            try:
                fn = resolve_check(check_name)
                det = fn(scenario, output)
            except Exception as e:
                # An unknown or erroring check FAILS this one scenario rather than
                # crashing the run. A typo in a rubric shouldn't abort every other
                # scenario queued behind it — fail loud but local, not cascading.
                det = DeterministicResult(
                    check_name=check_name, passed=False, detail=f"check errored: {e}"
                )
            result.deterministic_results.append(det)


# ----- Built-in checks -----

@check("routed_agent_matches")
def _routed_agent_matches(scenario: Scenario, output: AgentOutput) -> DeterministicResult:
    expected = scenario.expected.get("routed_agent")
    actual = output.routed_agent
    return DeterministicResult(
        check_name="routed_agent_matches",
        passed=expected == actual,
        detail=None if expected == actual else f"expected {expected!r}, got {actual!r}",
    )


@check("refused")
def _refused(scenario: Scenario, output: AgentOutput) -> DeterministicResult:
    return DeterministicResult(
        check_name="refused",
        passed=output.routed_agent == "REFUSE",
        detail=(
            None
            if output.routed_agent == "REFUSE"
            else f"expected REFUSE, got {output.routed_agent!r}"
        ),
    )


@check("not_refused")
def _not_refused(scenario: Scenario, output: AgentOutput) -> DeterministicResult:
    return DeterministicResult(
        check_name="not_refused",
        passed=output.routed_agent != "REFUSE",
        detail=None if output.routed_agent != "REFUSE" else "unexpectedly refused",
    )


# Checks over the whole turn rather than the first tool call. Like
# routed_agent_matches they read their target from `scenario.expected`, and a
# scenario that names one without configuring it fails just that scenario
# ("check errored"), per DeterministicScorer.

#: Heuristic English phrases for "I can't / won't do that". A regex over free
#: text is only ever a heuristic — a scenario that needs precision should set
#: `expected.final_reply_denies_action` to its own pattern instead.
DEFAULT_DECLINE_PATTERN = (
    r"\b(?:can(?:not|['’]t)|unable to|not able to"
    r"|(?:do(?:es)?n['’]t|do(?:es)? not) have"
    r"|no (?:tool|way|ability|capability|access)"
    r"|(?:isn['’]t|is not|not) (?:possible|available|supported)"
    r"|won['’]t|will not|must decline|decline to|refuse to)\b"
)


@check("tool_sequence_contains")
def _tool_sequence_contains(scenario: Scenario, output: AgentOutput) -> DeterministicResult:
    """Every tool named in `expected.tool_sequence_contains` (a name, or a list
    of names) was called at some point in the turn — not necessarily first."""
    wanted = scenario.expected.get("tool_sequence_contains")
    if isinstance(wanted, str):
        wanted = [wanted]
    if not wanted:
        raise ValueError("expected.tool_sequence_contains is required (a tool name or list)")
    missing = [t for t in wanted if t not in output.tool_sequence]
    return DeterministicResult(
        check_name="tool_sequence_contains",
        passed=not missing,
        detail=(
            None if not missing else f"missing {missing!r}; tools called: {output.tool_sequence!r}"
        ),
    )


@check("final_reply_matches")
def _final_reply_matches(scenario: Scenario, output: AgentOutput) -> DeterministicResult:
    """The reply matches the regex in `expected.final_reply_matches`
    (case-insensitive, searched anywhere in the text)."""
    pattern = scenario.expected.get("final_reply_matches")
    if not pattern:
        raise ValueError("expected.final_reply_matches is required (a regex)")
    reply = output.final_reply
    passed = bool(reply) and re.search(pattern, reply, re.IGNORECASE | re.DOTALL) is not None
    return DeterministicResult(
        check_name="final_reply_matches",
        passed=passed,
        detail=None if passed else f"reply {reply!r} does not match {pattern!r}",
    )


@check("final_reply_denies_action")
def _final_reply_denies_action(scenario: Scenario, output: AgentOutput) -> DeterministicResult:
    """The reply says it can't/won't do the requested thing, rather than
    claiming or offering it. Uses DEFAULT_DECLINE_PATTERN unless the scenario
    sets `expected.final_reply_denies_action` to its own regex."""
    pattern = scenario.expected.get("final_reply_denies_action") or DEFAULT_DECLINE_PATTERN
    reply = output.final_reply
    passed = bool(reply) and re.search(pattern, reply, re.IGNORECASE | re.DOTALL) is not None
    return DeterministicResult(
        check_name="final_reply_denies_action",
        passed=passed,
        detail=None if passed else f"reply {reply!r} does not decline the action",
    )


def _entity_present_factory(key: str) -> CheckFn:
    def check_fn(scenario: Scenario, output: AgentOutput) -> DeterministicResult:
        present = key in output.extracted_entities and output.extracted_entities[key] is not None
        return DeterministicResult(
            check_name=f"extracted_entities.{key}_present",
            passed=present,
            detail=None if present else f"entity {key!r} missing",
        )
    return check_fn


def _entity_equals_factory(key: str) -> CheckFn:
    def check_fn(scenario: Scenario, output: AgentOutput) -> DeterministicResult:
        expected = scenario.expected.get("extracted_entities", {}).get(key)
        actual = output.extracted_entities.get(key)
        passed = expected == actual
        return DeterministicResult(
            check_name=f"extracted_entities.{key}_equals",
            passed=passed,
            detail=None if passed else f"expected {expected!r}, got {actual!r}",
        )
    return check_fn
