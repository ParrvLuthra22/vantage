"""Judge trace logging (--collect-judge-traces): what gets logged, and what must not.

The log is fine-tuning data, so the interesting failures are quiet ones: a
placeholder score logged as a label, a torn line corrupting its neighbour, an
"unlogged" run that silently produced an empty file.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from click.testing import CliRunner
from openai import APIStatusError
from vantage_eval.cli import main
from vantage_eval.models import AgentOutput, Rubric, Scenario, ScenarioResult
from vantage_eval.scorers.llm_judge import SYSTEM_PROMPT, TRACE_SCHEMA_VERSION, LLMJudgeScorer

SMOKE_SUITE = Path(__file__).parent.parent / "suites" / "orchestrator_v1_smoke"

REQUIRED_FIELDS = {
    "timestamp",
    "judge_model",
    "scenario_id",
    "category",
    "input_prompt",
    "raw_response",
    "parsed_score",
    "parsed_reasoning",
    "actual_output",
    "expected",
}


def _fake_response(content: str):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.usage = MagicMock()
    resp.usage.prompt_tokens = 300
    resp.usage.completion_tokens = 100
    return resp


def _scenario(ext_id="t1"):
    return Scenario(
        external_id=ext_id,
        category="clear",
        complexity="single_step",
        input="what's the weather",
        expected={"routed_agent": "search_web"},
        rubric=Rubric(llm_judge_prompt="Judge {{ input }} routed to {{ actual.routed_agent }}"),
    )


def _scorer(log_path, response):
    scorer = LLMJudgeScorer(api_key="fake", trace_log_path=log_path)
    scorer.client = MagicMock()
    scorer.client.chat.completions.create.return_value = response
    return scorer


def _judge(scorer, scenario=None, entities=None):
    scenario = scenario or _scenario()
    output = AgentOutput(
        routed_agent="search_web",
        extracted_entities=entities or {"query": "weather"},
        latency_ms=10,
    )
    result = ScenarioResult(external_id=scenario.external_id, output=output)
    scorer.score(scenario, output, result)
    return result


def _lines(path):
    return path.read_text(encoding="utf-8").splitlines()


def test_clean_judgment_is_logged_with_the_full_schema(tmp_path):
    log = tmp_path / "traces.jsonl"
    scorer = _scorer(log, _fake_response('{"reasoning": "right tool", "score": 5}'))

    _judge(scorer)

    (line,) = _lines(log)
    entry = json.loads(line)
    assert REQUIRED_FIELDS <= entry.keys()
    assert entry["schema_version"] == TRACE_SCHEMA_VERSION == 2  # v1 labels were reply-blind
    assert entry["scenario_id"] == "t1"
    assert entry["category"] == "clear"
    assert entry["judge_model"] == scorer.model
    assert entry["parsed_score"] == 5
    assert isinstance(entry["parsed_score"], int)
    assert entry["parsed_reasoning"] == "right tool"
    assert entry["raw_response"] == '{"reasoning": "right tool", "score": 5}'
    # The scenario's own rendered prompt, then the standard whole-turn block.
    assert entry["input_prompt"].startswith("Judge what's the weather routed to search_web\n\n")
    assert "Complete observed behavior" in entry["input_prompt"]
    assert entry["actual_output"] == {
        "routed_agent": "search_web",
        "extracted_entities": {"query": "weather"},
        "tool_sequence": [],
        "final_reply": None,
    }
    assert entry["expected"] == {"routed_agent": "search_web"}
    # The score guide lives in the system prompt — the example isn't reproducible without it.
    assert entry["system_prompt"] == SYSTEM_PROMPT
    assert (scorer.traces_logged, scorer.traces_skipped) == (1, 0)


def test_judgments_append_rather_than_overwrite(tmp_path):
    log = tmp_path / "traces.jsonl"
    scorer = _scorer(log, _fake_response('{"reasoning": "ok", "score": 4}'))

    _judge(scorer, _scenario("a"))
    _judge(scorer, _scenario("b"))

    assert [json.loads(line)["scenario_id"] for line in _lines(log)] == ["a", "b"]


def test_a_second_scorer_appends_to_an_existing_file(tmp_path):
    """The collector runs one process per iteration, all appending to one file."""
    log = tmp_path / "traces.jsonl"
    for ext_id in ("first-run", "second-run"):
        _judge(_scorer(log, _fake_response('{"reasoning": "ok", "score": 3}')), _scenario(ext_id))

    assert [json.loads(line)["scenario_id"] for line in _lines(log)] == ["first-run", "second-run"]


@pytest.mark.parametrize(
    "content",
    [
        "not json at all",  # parse_error -> 0.0 placeholder, not a label
        '{"reasoning": "no score field"}',  # missing score defaults to 0.0
        '{"reasoning": "zero", "score": 0}',  # below the documented 1-5 range
        '{"reasoning": "too high", "score": 7}',  # clamps to 5.0 — must not become a 5 label
        "[1, 2, 3]",  # valid JSON, not an object
        '{"reasoning": "nan", "score": NaN}',
    ],
)
def test_unclean_judgments_are_skipped_not_logged(tmp_path, content):
    log = tmp_path / "traces.jsonl"
    scorer = _scorer(log, _fake_response(content))

    result = _judge(scorer)  # must not raise, whatever the model said

    assert _lines(log) == []
    assert (scorer.traces_logged, scorer.traces_skipped) == (0, 1)
    assert result.llm_judge_score is not None  # the eval itself still gets its (placeholder) score


def test_out_of_range_score_is_still_clamped_for_scoring_but_not_logged(tmp_path):
    """Existing scoring behavior is unchanged; only the training log is stricter."""
    scorer = _scorer(tmp_path / "traces.jsonl", _fake_response('{"reasoning": "x", "score": 7}'))

    assert _judge(scorer).llm_judge_score == 5.0


def test_judge_api_error_is_skipped_not_logged(tmp_path):
    log = tmp_path / "traces.jsonl"
    scorer = _scorer(log, None)
    response = httpx.Response(
        400, request=httpx.Request("POST", "https://api.example.com/x"), json={}
    )
    scorer.client.chat.completions.create.side_effect = APIStatusError(
        "boom", response=response, body=None
    )

    result = _judge(scorer)

    assert "judge_error" in result.llm_judge_reasoning
    assert _lines(log) == []
    assert scorer.traces_skipped == 1


def test_without_a_trace_path_nothing_is_written_and_counters_stay_zero(tmp_path):
    scorer = LLMJudgeScorer(api_key="fake")
    scorer.client = MagicMock()
    scorer.client.chat.completions.create.return_value = _fake_response(
        '{"reasoning": "ok", "score": 4}'
    )

    _judge(scorer)

    assert scorer.trace_log_path is None
    assert (scorer.traces_logged, scorer.traces_skipped) == (0, 0)
    assert list(tmp_path.iterdir()) == []


def test_torn_last_line_is_terminated_so_it_cannot_swallow_the_next_entry(tmp_path):
    """A run killed mid-write leaves a line with no newline; a naive append
    would fuse it with the next entry and lose both."""
    log = tmp_path / "traces.jsonl"
    log.write_text('{"scenario_id": "torn", "raw_resp')  # no trailing newline
    scorer = _scorer(log, _fake_response('{"reasoning": "ok", "score": 4}'))

    _judge(scorer, _scenario("after-crash"))

    torn, good = _lines(log)
    with pytest.raises(json.JSONDecodeError):
        json.loads(torn)
    assert json.loads(good)["scenario_id"] == "after-crash"


def test_unwritable_path_fails_at_construction_not_hours_into_a_run(tmp_path):
    not_a_dir = tmp_path / "file"
    not_a_dir.write_text("x")

    with pytest.raises(OSError):
        LLMJudgeScorer(api_key="fake", trace_log_path=not_a_dir / "traces.jsonl")


def test_parent_directories_are_created(tmp_path):
    log = tmp_path / "a" / "b" / "traces.jsonl"
    _judge(_scorer(log, _fake_response('{"reasoning": "ok", "score": 4}')))
    assert len(_lines(log)) == 1


def test_a_logging_failure_never_costs_the_eval_its_score(tmp_path):
    scorer = _scorer(tmp_path / "traces.jsonl", _fake_response('{"reasoning": "ok", "score": 4}'))
    scorer.trace_log_path = tmp_path / "vanished" / "traces.jsonl"  # parent no longer exists

    result = _judge(scorer)

    assert result.llm_judge_score == 4.0
    assert (scorer.traces_logged, scorer.traces_skipped) == (0, 1)


def test_unserializable_entity_values_do_not_cost_an_example(tmp_path):
    log = tmp_path / "traces.jsonl"
    scorer = _scorer(log, _fake_response('{"reasoning": "ok", "score": 4}'))

    _judge(scorer, entities={"when": datetime(2026, 1, 1, tzinfo=timezone.utc)})

    (line,) = _lines(log)
    assert "2026-01-01" in json.loads(line)["actual_output"]["extracted_entities"]["when"]


# --- CLI wiring -----------------------------------------------------------


def test_collect_traces_with_no_judge_is_rejected_up_front(tmp_path):
    """Otherwise a long collection run would quietly produce an empty file."""
    log = tmp_path / "t.jsonl"
    result = CliRunner().invoke(
        main,
        ["eval", "run", str(SMOKE_SUITE), "--no-judge", "--collect-judge-traces", str(log)],
    )
    assert result.exit_code == 2
    assert "--no-judge" in result.output
    assert not log.exists()


def test_mark_baseline_with_no_persist_is_rejected():
    result = CliRunner().invoke(
        main, ["eval", "run", str(SMOKE_SUITE), "--no-judge", "--mark-baseline", "--no-persist"]
    )
    assert result.exit_code == 2
    assert "--no-persist" in result.output


def test_cli_collects_one_trace_per_judged_scenario(tmp_path, monkeypatch):
    """End to end through the real CLI, real suite YAML and real Jinja rubric
    templates — only the judge's network call is faked."""

    class FakeJudge(LLMJudgeScorer):
        def __init__(self, **kwargs):
            super().__init__(api_key="fake", **kwargs)
            self.client = MagicMock()
            self.client.chat.completions.create.return_value = _fake_response(
                '{"reasoning": "fine", "score": 4}'
            )

    monkeypatch.setattr("vantage_eval.cli.LLMJudgeScorer", FakeJudge)
    log = tmp_path / "t.jsonl"

    result = CliRunner().invoke(
        main,
        [
            "eval",
            "run",
            str(SMOKE_SUITE),
            "--adapter",
            "mock",
            "--no-persist",
            "--collect-judge-traces",
            str(log),
        ],
    )

    assert result.exit_code in (0, 1), result.output  # 1 = some scenarios failed, expected w/ mock
    entries = [json.loads(line) for line in _lines(log)]
    assert len(entries) == 10  # every scenario in the smoke suite has a judge prompt
    assert all(REQUIRED_FIELDS <= e.keys() for e in entries)
    assert {e["scenario_id"] for e in entries} == {
        "adversarial_001",
        "adversarial_004",
        "clear_001",
        "clear_002",
        "clear_007",
        "context_dependent_002",
        "context_dependent_003",
        "context_dependent_005",
        "out_of_scope_003",
        "out_of_scope_006",
    }
    assert "10 logged, 0 skipped" in result.output
    assert "not writing this run to Postgres" in result.output
