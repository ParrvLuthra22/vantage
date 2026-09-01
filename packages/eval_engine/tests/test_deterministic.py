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
