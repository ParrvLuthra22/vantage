"""Suite-level aggregation (_summarize): who counts toward the denominator.

known_failing and judge_error are both excluded from total/passed/failed, but
for opposite reasons — a documented product bug vs. the judge's own
infrastructure failing — so each needs its own count and neither should
double-count a scenario that happens to be both.
"""

from vantage_eval.models import AgentOutput, ScenarioResult
from vantage_eval.runner import _summarize


def _result(external_id, passed, known_failing=False, judge_error=False, llm_judge_score=None):
    return ScenarioResult(
        external_id=external_id,
        output=AgentOutput(routed_agent="chat_agent", latency_ms=10),
        passed=passed,
        known_failing=known_failing,
        judge_error=judge_error,
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
