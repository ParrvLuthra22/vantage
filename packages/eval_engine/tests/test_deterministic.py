from vantage_eval.models import AgentOutput, Rubric, Scenario, ScenarioResult
from vantage_eval.scorers.deterministic import DeterministicScorer


def _make_scenario(hard_checks: list[str], expected: dict) -> Scenario:
    return Scenario(
        external_id="test",
        category="clear",
        complexity="single_step",
        input="test",
        expected=expected,
        rubric=Rubric(hard_checks=hard_checks),
    )


def test_routed_agent_matches_pass():
    s = _make_scenario(["routed_agent_matches"], {"routed_agent": "calendar_agent"})
    o = AgentOutput(routed_agent="calendar_agent", latency_ms=10)
    r = ScenarioResult(external_id="test", output=o)
    DeterministicScorer().score(s, o, r)
    assert len(r.deterministic_results) == 1
    assert r.deterministic_results[0].passed is True


def test_routed_agent_matches_fail_shows_diff():
    s = _make_scenario(["routed_agent_matches"], {"routed_agent": "calendar_agent"})
    o = AgentOutput(routed_agent="email_agent", latency_ms=10)
    r = ScenarioResult(external_id="test", output=o)
    DeterministicScorer().score(s, o, r)
    assert r.deterministic_results[0].passed is False
    assert "calendar_agent" in r.deterministic_results[0].detail
    assert "email_agent" in r.deterministic_results[0].detail


def test_entity_present_sugar():
    s = _make_scenario(["extracted_entities.subject_present"], {})
    o = AgentOutput(
        routed_agent="calendar_agent", extracted_entities={"subject": "priya"}, latency_ms=10
    )
    r = ScenarioResult(external_id="test", output=o)
    DeterministicScorer().score(s, o, r)
    assert r.deterministic_results[0].passed is True


def test_unknown_check_records_failure_not_crash():
    s = _make_scenario(["nonexistent_check"], {})
    o = AgentOutput(routed_agent="chat_agent", latency_ms=10)
    r = ScenarioResult(external_id="test", output=o)
    DeterministicScorer().score(s, o, r)
    assert r.deterministic_results[0].passed is False
    detail = r.deterministic_results[0].detail
    assert "Unknown check" in detail or "errored" in detail


def _score(check: str, expected: dict, **out):
    s = _make_scenario([check], expected)
    o = AgentOutput(latency_ms=10, **out)
    r = ScenarioResult(external_id="test", output=o)
    DeterministicScorer().score(s, o, r)
    return r.deterministic_results[0]


def test_tool_sequence_contains_finds_a_tool_that_was_not_first():
    """The point of the check: routed_agent only sees the first call."""
    res = _score(
        "tool_sequence_contains",
        {"tool_sequence_contains": "set_volume"},
        routed_agent="get_volume",
        tool_sequence=["get_volume", "set_volume"],
    )
    assert res.passed is True


def test_tool_sequence_contains_lists_what_is_missing():
    res = _score(
        "tool_sequence_contains",
        {"tool_sequence_contains": ["take_screenshot", "show_notification"]},
        routed_agent="show_notification",
        tool_sequence=["show_notification"],
    )
    assert res.passed is False
    assert "take_screenshot" in res.detail and "show_notification" not in res.detail.split(";")[0]


def test_tool_sequence_contains_unconfigured_fails_only_that_scenario():
    res = _score("tool_sequence_contains", {}, routed_agent="chat_agent")
    assert res.passed is False
    assert "errored" in res.detail and "tool_sequence_contains" in res.detail


def test_final_reply_matches():
    reply = "Volume increased to 60%, Sir."
    assert _score(
        "final_reply_matches", {"final_reply_matches": r"increased to \d+%"},
        routed_agent="x", final_reply=reply,
    ).passed is True
    assert _score(
        "final_reply_matches", {"final_reply_matches": r"muted"},
        routed_agent="x", final_reply=reply,
    ).passed is False


def test_final_reply_checks_fail_when_there_is_no_reply():
    assert _score(
        "final_reply_matches", {"final_reply_matches": "."}, routed_agent="x"
    ).passed is False
    assert _score("final_reply_denies_action", {}, routed_agent="x").passed is False


def test_final_reply_denies_action_default_pattern():
    declines = [
        "Sorry, I can't send text messages.",
        "I'm unable to do that, Sir.",
        "I don’t have a tool for that.",  # curly apostrophe
        "That isn't possible from here.",
    ]
    for reply in declines:
        res = _score("final_reply_denies_action", {}, routed_agent="chat_agent", final_reply=reply)
        assert res.passed is True, reply

    # Claiming or OFFERING the action is not a denial (clear_010's real reply).
    not_declines = [
        "Volume increased to 60%, Sir.",
        "Sir, I can send the message via Messages. Shall I run this script?",
    ]
    for reply in not_declines:
        res = _score("final_reply_denies_action", {}, routed_agent="chat_agent", final_reply=reply)
        assert res.passed is False, reply


def test_final_reply_denies_action_scenario_can_override_the_pattern():
    res = _score(
        "final_reply_denies_action", {"final_reply_denies_action": r"\bnope\b"},
        routed_agent="chat_agent", final_reply="Nope.",
    )
    assert res.passed is True
