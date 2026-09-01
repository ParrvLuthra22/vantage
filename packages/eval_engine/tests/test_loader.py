from pathlib import Path

import pytest
from vantage_eval.loader import SuiteLoadError, load_suite

SUITE_PATH = Path(__file__).parent.parent / "suites" / "orchestrator_v1"


def test_load_orchestrator_v1_suite():
    suite = load_suite(SUITE_PATH)
    assert suite.name == "orchestrator_v1"
    assert len(suite.scenarios) >= 1
    first = suite.scenarios[0]
    assert first.rubric.hard_checks
    ids = {s.external_id for s in suite.scenarios}
    assert any(i.startswith("clear_") for i in ids)
    assert any(i.startswith("ambiguous_") for i in ids)


def test_missing_suite_yaml_raises(tmp_path):
    (tmp_path / "scenarios").mkdir()
    with pytest.raises(SuiteLoadError, match="Missing suite.yaml"):
        load_suite(tmp_path)


def test_duplicate_ids_raise(tmp_path):
    (tmp_path / "suite.yaml").write_text("name: t\ndescription: t\nagent_target: t\n")
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    same = (
        "id: dup_001\ncategory: clear\ncomplexity: single_step\ninput: hi\n"
        "expected: {routed_agent: x}\nrubric: {hard_checks: []}"
    )
    (scenarios / "a.yaml").write_text(same)
    (scenarios / "b.yaml").write_text(same)
    with pytest.raises(SuiteLoadError, match="Duplicate scenario id"):
        load_suite(tmp_path)
