"""Suite-level aggregation (_summarize): who counts toward the denominator.

known_failing, judge_error, and infra_error are all excluded from
total/passed/failed, but for different reasons — a documented product bug,
the judge's own infrastructure failing, and the agent-under-test's own
infrastructure failing, respectively — so each needs its own count and none
should double-count a scenario that happens to be more than one.
"""

from unittest.mock import MagicMock

from vantage_eval.models import AgentOutput, Rubric, Scenario, ScenarioResult
from vantage_eval.runner import _run_one, _summarize


def _result(
    external_id,
    passed,
    known_failing=False,
    judge_error=False,
    infra_error=False,
    llm_judge_score=None,
):
    return ScenarioResult(
        external_id=external_id,
        output=AgentOutput(routed_agent="chat_agent", latency_ms=10),
        passed=passed,
        known_failing=known_failing,
        judge_error=judge_error,
        infra_error=infra_error,
        llm_judge_score=llm_judge_score,
    )


def test_judge_error_excluded_from_denominator_not_counted_as_failure():
    results = [
        _result("clear_001", passed=True, llm_judge_score=5.0),
        # would drag the pass rate down if counted:
        _result("clear_002", passed=False, judge_error=True),
        _result("clear_003", passed=False, llm_judge_score=1.0),
    ]

    summary = _summarize(results, duration_s=1.0, judge_cost=0.0)

    assert summary.total_scenarios == 3
    assert summary.judge_error == 1
    assert summary.total == 2  # judge_error scenario excluded
    assert summary.passed == 1
    assert summary.failed == 1
    assert summary.pass_rate == 0.5


def test_known_failing_and_judge_error_do_not_double_count():
    results = [
        _result("clear_001", passed=False, known_failing=True, judge_error=True),
        _result("clear_002", passed=True, llm_judge_score=5.0),
    ]

    summary = _summarize(results, duration_s=1.0, judge_cost=0.0)

    assert summary.known_failing == 1
    assert summary.judge_error == 0  # already excluded via known_failing; not double-counted
    assert summary.total == 1
    assert summary.total_scenarios == 2


def test_avg_llm_score_excludes_judge_error_scenarios():
    results = [
        _result("clear_001", passed=True, llm_judge_score=5.0),
        _result("clear_002", passed=False, judge_error=True, llm_judge_score=None),
    ]

    summary = _summarize(results, duration_s=1.0, judge_cost=0.0)

    assert summary.avg_llm_score == 5.0


def test_infra_error_excluded_from_denominator_not_counted_as_failure():
    """The exact distortion that motivated this: a Groq daily-quota outage
    drove 34/40 scenarios to PLANNER_FAILURE in a real run, and — before this
    fix — those got judged and counted as real failures (one, by coincidence,
    even scored well). infra_error must be excluded the same way judge_error
    is, not just for the sentinel scenarios but for the pass_rate math too."""
    results = [
        _result("clear_001", passed=True, llm_judge_score=5.0),
        _result("clear_002", passed=False, infra_error=True),
        _result("clear_003", passed=False, llm_judge_score=1.0),
    ]

    summary = _summarize(results, duration_s=1.0, judge_cost=0.0)

    assert summary.total_scenarios == 3
    assert summary.infra_error == 1
    assert summary.total == 2
    assert summary.passed == 1
    assert summary.failed == 1
    assert summary.pass_rate == 0.5


def test_known_failing_and_infra_error_do_not_double_count():
    results = [
        _result("clear_001", passed=False, known_failing=True, infra_error=True),
        _result("clear_002", passed=True, llm_judge_score=5.0),
    ]

    summary = _summarize(results, duration_s=1.0, judge_cost=0.0)

    assert summary.known_failing == 1
    assert summary.infra_error == 0  # already excluded via known_failing; not double-counted
    assert summary.total == 1


def test_judge_error_and_infra_error_are_independent_buckets():
    results = [
        _result("clear_001", passed=False, judge_error=True),
        _result("clear_002", passed=False, infra_error=True),
        _result("clear_003", passed=True, llm_judge_score=5.0),
    ]

    summary = _summarize(results, duration_s=1.0, judge_cost=0.0)

    assert summary.judge_error == 1
    assert summary.infra_error == 1
    assert summary.total == 1
    assert summary.passed == 1


# =============================================================================
# _run_one: infra sentinels skip the judge entirely
# =============================================================================


def _scenario_with_judge():
    return Scenario(
        external_id="t1",
        category="clear",
        complexity="single_step",
        input="x",
        expected={},
        rubric=Rubric(llm_judge_prompt="judge {{ input }}"),
    )


class _StubAdapter:
    def __init__(self, routed_agent):
        self._routed_agent = routed_agent

    def invoke(self, input, context):
        return AgentOutput(routed_agent=self._routed_agent, latency_ms=10)


def test_planner_failure_skips_the_judge_and_is_flagged_infra_error():
    judge = MagicMock()
    result = _run_one(_scenario_with_judge(), _StubAdapter("PLANNER_FAILURE"), MagicMock(), judge)

    assert result.infra_error is True
    judge.score.assert_not_called()


def test_adapter_error_skips_the_judge_and_is_flagged_infra_error():
    judge = MagicMock()
    result = _run_one(_scenario_with_judge(), _StubAdapter("ADAPTER_ERROR"), MagicMock(), judge)

    assert result.infra_error is True
    judge.score.assert_not_called()


def test_a_real_routing_decision_still_reaches_the_judge():
    judge = MagicMock()
    result = _run_one(_scenario_with_judge(), _StubAdapter("chat_agent"), MagicMock(), judge)

    assert result.infra_error is False
    judge.score.assert_called_once()
