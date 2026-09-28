"""scripts/label_gold_set.py: resumability and Ctrl-C safety are the whole
point of an interactive tool a human runs across multiple sessions — a
labeler that silently loses work, or re-prompts for something already
labeled, would poison the gold set with duplicates or gaps rather than catch
them. Runs the real CLI via click.testing.CliRunner with fake stdin, against
temp candidate/label files, so no real gold-set file is ever touched here.
"""
import importlib.util
import json
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

SCRIPT = Path(__file__).parents[3] / "scripts" / "label_gold_set.py"


@pytest.fixture
def labeler(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("label_gold_set", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "CANDIDATES", tmp_path / "candidates.jsonl")
    monkeypatch.setattr(module, "LABELS", tmp_path / "labels.jsonl")
    return module


def _write_candidates(labeler, *externals):
    lines = [
        json.dumps(
            {
                "result_id": f"11111111-0000-0000-0000-{i:012d}",
                "external_id": ext,
                "category": "clear",
                "bucket": "stable_failure",
                "input": f"input for {ext}",
                "expected": {"routed_agent": "chat_agent"},
                "tool_sequence": [],
                "tool_calls": [],
                "final_reply": "some reply",
                "llm_judge_score": 1.0,
                "llm_judge_reasoning": "judge said so",
            }
        )
        for i, ext in enumerate(externals)
    ]
    labeler.CANDIDATES.write_text("\n".join(lines) + "\n")


def _label_input(score=1, fc_idx=0, reasoning="looks like a hallucination", confidence=2, notes=""):
    return f"l\n{score}\n{fc_idx}\n{reasoning}\n{confidence}\n{notes}\n"


def test_labeling_one_candidate_writes_expected_schema(labeler):
    _write_candidates(labeler, "clear_001")
    result = CliRunner().invoke(labeler.main, [], input=_label_input(score=1, fc_idx=0))

    assert result.exit_code == 0, result.output
    (line,) = labeler.LABELS.read_text().splitlines()
    entry = json.loads(line)
    assert entry["result_id"] == "11111111-0000-0000-0000-000000000000"
    assert entry["scenario_external_id"] == "clear_001"
    assert entry["score"] == 1
    assert entry["failure_class"] == "hallucination"
    assert entry["reasoning"] == "looks like a hallucination"
    assert entry["confidence"] == 2
    assert entry["notes"] is None  # empty string normalized to None
    assert entry["labeler"] == "parrv"
    assert "labeled_at" in entry


def test_skip_does_not_write_a_label_but_moves_on(labeler):
    _write_candidates(labeler, "clear_001", "clear_002")
    result = CliRunner().invoke(labeler.main, [], input="s\n" + _label_input())

    assert result.exit_code == 0, result.output
    (line,) = labeler.LABELS.read_text().splitlines()
    assert json.loads(line)["scenario_external_id"] == "clear_002"


def test_resumable_skips_already_labeled_ids(labeler):
    _write_candidates(labeler, "clear_001", "clear_002")
    CliRunner().invoke(labeler.main, [], input=_label_input())  # labels clear_001 (seen first)

    # Second invocation: clear_001 already labeled, only clear_002 should be prompted.
    result = CliRunner().invoke(labeler.main, [], input=_label_input(score=3, fc_idx=6))

    assert "already labeled: 1" in result.output
    assert "remaining: 1" in result.output
    lines = labeler.LABELS.read_text().splitlines()
    assert len(lines) == 2
    external_ids = {json.loads(line)["scenario_external_id"] for line in lines}
    assert external_ids == {"clear_001", "clear_002"}


def test_quit_preserves_already_written_labels(labeler):
    """The functional equivalent of Ctrl-C: whatever was flushed before the
    session ended must survive, and a fresh run must not re-prompt for it."""
    _write_candidates(labeler, "clear_001", "clear_002", "clear_003")
    result = CliRunner().invoke(labeler.main, [], input=_label_input() + "q\n")

    assert result.exit_code == 0, result.output
    lines = labeler.LABELS.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["scenario_external_id"] == "clear_001"

    # Resuming sees exactly the 2 unlabeled candidates remaining.
    result2 = CliRunner().invoke(labeler.main, [], input="q\n")
    assert "already labeled: 1" in result2.output
    assert "remaining: 2" in result2.output


def test_a_real_keyboard_interrupt_leaves_prior_labels_on_disk(labeler):
    """Simulates an actual Ctrl-C (KeyboardInterrupt) rather than the "q"
    menu option, to prove the `with LABELS.open("a")` block's flush-per-write
    discipline survives an interrupt that isn't the script's own exit path."""
    _write_candidates(labeler, "clear_001", "clear_002")
    call_count = 0
    real_collect_label = labeler.collect_label

    def flaky_collect_label(candidate):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise KeyboardInterrupt
        return real_collect_label(candidate)

    labeler.collect_label = flaky_collect_label
    try:
        # click's own BaseCommand.main() catches KeyboardInterrupt and
        # re-raises it as click.exceptions.Abort — the label write already
        # happened and was flushed before either exception unwound the loop,
        # which is the actual property under test.
        with pytest.raises(click.exceptions.Abort):
            CliRunner().invoke(
                labeler.main,
                [],
                input=_label_input(),
                catch_exceptions=False,
                standalone_mode=False,
            )
    finally:
        labeler.collect_label = real_collect_label

    lines = labeler.LABELS.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["scenario_external_id"] == "clear_001"


def test_limit_option_stops_after_n_labels_without_prompting_further(labeler):
    _write_candidates(labeler, "clear_001", "clear_002")
    result = CliRunner().invoke(labeler.main, ["--limit", "1"], input=_label_input())

    assert result.exit_code == 0, result.output
    assert "Session limit 1 reached" in result.output
    (line,) = labeler.LABELS.read_text().splitlines()
    assert json.loads(line)["scenario_external_id"] == "clear_001"


def test_missing_candidates_file_gives_a_clear_error(labeler):
    result = CliRunner().invoke(labeler.main, [])
    assert result.exit_code != 0
    assert "select_gold_candidates.py" in result.output
