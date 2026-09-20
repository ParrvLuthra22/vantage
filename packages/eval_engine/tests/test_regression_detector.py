from datetime import datetime, timezone

from vantage_eval.models import AgentOutput, ScenarioResult, SuiteRun, SuiteRunSummary
from vantage_eval.regression.detector import detect_regressions


def _make_run(results):
    passed = sum(1 for r in results if r.passed)
    summary = SuiteRunSummary(
        total=len(results),
        passed=passed,
        failed=len(results) - passed,
        pass_rate=passed / len(results) if results else 0.0,
        total_scenarios=len(results),
    )
    return SuiteRun(
        suite_name="test",
        agent_version="abc",
        judge_model="none",
        started_at=datetime.now(timezone.utc),
        results=results,
        summary=summary,
    )


def _result(ext_id, passed):
    return ScenarioResult(
        external_id=ext_id, output=AgentOutput(routed_agent="x", latency_ms=10), passed=passed
    )


def test_detects_regression():
    baseline = _make_run([_result("s1", True), _result("s2", True)])
    current = _make_run([_result("s1", True), _result("s2", False)])
    report = detect_regressions(baseline, current)
    assert report.has_regressions
    assert len(report.regressions) == 1
    assert report.regressions[0].external_id == "s2"


def test_detects_improvement():
    baseline = _make_run([_result("s1", False), _result("s2", True)])
    current = _make_run([_result("s1", True), _result("s2", True)])
    report = detect_regressions(baseline, current)
    assert not report.has_regressions
    assert len(report.improvements) == 1
    assert report.improvements[0].external_id == "s1"


def test_handles_new_and_removed_scenarios():
    baseline = _make_run([_result("s1", True), _result("s_removed", True)])
    current = _make_run([_result("s1", True), _result("s_new", True)])
    report = detect_regressions(baseline, current)
    types = [c.change_type for c in report.changes]
    assert "new" in types
    assert "removed" in types
