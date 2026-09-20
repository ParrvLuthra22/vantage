"""Compare a new eval run against a baseline and classify changes.

Terminology:
  regression   - scenario passed in baseline, fails in new run
  improvement  - scenario failed in baseline, passes in new run
  stable       - same pass/fail in both
  new          - scenario didn't exist in baseline (added in this run)
  removed      - scenario existed in baseline but not in new run (deleted)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from vantage_eval.models import SuiteRun

ChangeType = Literal["regression", "improvement", "stable_pass", "stable_fail", "new", "removed"]


@dataclass
class ScenarioChange:
    external_id: str
    change_type: ChangeType
    baseline_passed: bool | None
    current_passed: bool | None
    baseline_llm_score: float | None = None
    current_llm_score: float | None = None
    baseline_deterministic_failures: list[str] = field(default_factory=list)
    current_deterministic_failures: list[str] = field(default_factory=list)


@dataclass
class RegressionReport:
    baseline_run_id: str
    current_run_id: str
    baseline_pass_rate: float
    current_pass_rate: float
    pass_rate_delta: float

    changes: list[ScenarioChange] = field(default_factory=list)

    @property
    def regressions(self) -> list[ScenarioChange]:
        return [c for c in self.changes if c.change_type == "regression"]

    @property
    def improvements(self) -> list[ScenarioChange]:
        return [c for c in self.changes if c.change_type == "improvement"]

    @property
    def has_regressions(self) -> bool:
        return len(self.regressions) > 0

    def category_deltas(self, scenarios_by_id: dict[str, str]) -> dict[str, float]:
        """Compute per-category pass-rate change. scenarios_by_id maps external_id -> category.

        A category present in only one of the two runs (e.g. a category
        introduced entirely by `new` scenarios) gets a pass rate of 0.0 for
        the side it's missing from, so its delta reads as the full swing
        rather than being silently dropped.
        """
        by_cat_baseline: dict[str, list[bool]] = {}
        by_cat_current: dict[str, list[bool]] = {}
        for change in self.changes:
            cat = scenarios_by_id.get(change.external_id, "unknown")
            if change.baseline_passed is not None:
                by_cat_baseline.setdefault(cat, []).append(change.baseline_passed)
            if change.current_passed is not None:
                by_cat_current.setdefault(cat, []).append(change.current_passed)

        deltas = {}
        for cat in set(by_cat_baseline) | set(by_cat_current):
            baseline_results = by_cat_baseline.get(cat, [])
            current_results = by_cat_current.get(cat, [])
            bpr = sum(baseline_results) / len(baseline_results) if baseline_results else 0.0
            cpr = sum(current_results) / len(current_results) if current_results else 0.0
            deltas[cat] = cpr - bpr
        return deltas


def detect_regressions(baseline: SuiteRun, current: SuiteRun) -> RegressionReport:
    """Compare two SuiteRuns and produce a RegressionReport."""
    baseline_by_id = {r.external_id: r for r in baseline.results}
    current_by_id = {r.external_id: r for r in current.results}

    all_ids = set(baseline_by_id) | set(current_by_id)
    changes: list[ScenarioChange] = []

    for ext_id in sorted(all_ids):
        b = baseline_by_id.get(ext_id)
        c = current_by_id.get(ext_id)

        if b is None:
            changes.append(
                ScenarioChange(
                    external_id=ext_id,
                    change_type="new",
                    baseline_passed=None,
                    current_passed=c.passed,
                    current_llm_score=c.llm_judge_score,
                    current_deterministic_failures=[
                        d.check_name for d in c.deterministic_results if not d.passed
                    ],
                )
            )
        elif c is None:
            changes.append(
                ScenarioChange(
                    external_id=ext_id,
                    change_type="removed",
                    baseline_passed=b.passed,
                    current_passed=None,
                    baseline_llm_score=b.llm_judge_score,
                    baseline_deterministic_failures=[
                        d.check_name for d in b.deterministic_results if not d.passed
                    ],
                )
            )
        else:
            if b.passed and not c.passed:
                ct: ChangeType = "regression"
            elif not b.passed and c.passed:
                ct = "improvement"
            elif b.passed and c.passed:
                ct = "stable_pass"
            else:
                ct = "stable_fail"
            changes.append(
                ScenarioChange(
                    external_id=ext_id,
                    change_type=ct,
                    baseline_passed=b.passed,
                    current_passed=c.passed,
                    baseline_llm_score=b.llm_judge_score,
                    current_llm_score=c.llm_judge_score,
                    baseline_deterministic_failures=[
                        d.check_name for d in b.deterministic_results if not d.passed
                    ],
                    current_deterministic_failures=[
                        d.check_name for d in c.deterministic_results if not d.passed
                    ],
                )
            )

    b_summary = baseline.summary
    c_summary = current.summary
    baseline_pr = b_summary.pass_rate if b_summary else 0.0
    current_pr = c_summary.pass_rate if c_summary else 0.0

    return RegressionReport(
        baseline_run_id=str(baseline.run_id),
        current_run_id=str(current.run_id),
        baseline_pass_rate=baseline_pr,
        current_pass_rate=current_pr,
        pass_rate_delta=current_pr - baseline_pr,
        changes=changes,
    )
