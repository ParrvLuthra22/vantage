from unittest.mock import MagicMock

from vantage_eval.models import AgentOutput, Rubric, Scenario, ScenarioResult
from vantage_eval.scorers.llm_judge import LLMJudgeScorer


def _fake_response(content: str, in_tokens: int = 300, out_tokens: int = 100):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.usage = MagicMock()
    resp.usage.prompt_tokens = in_tokens
    resp.usage.completion_tokens = out_tokens
    return resp


def test_judge_parses_valid_json_score():
    scenario = Scenario(
        external_id="t1", category="clear", complexity="single_step",
        input="book meeting", expected={"routed_agent": "calendar"},
        rubric=Rubric(llm_judge_prompt="Judge {{ input }} routed to {{ actual.routed_agent }}"),
    )
    output = AgentOutput(routed_agent="calendar", latency_ms=10)
    result = ScenarioResult(external_id="t1", output=output)

    scorer = LLMJudgeScorer(api_key="fake")
    scorer.client = MagicMock()
    scorer.client.chat.completions.create.return_value = _fake_response(
        '{"reasoning": "correct routing", "score": 5}'
    )

    scorer.score(scenario, output, result)
    assert result.llm_judge_score == 5.0
    assert "correct routing" in result.llm_judge_reasoning
    assert scorer.total_cost_usd > 0


def test_judge_handles_malformed_json():
    scenario = Scenario(
        external_id="t1", category="clear", complexity="single_step",
        input="x", expected={}, rubric=Rubric(llm_judge_prompt="judge"),
    )
    output = AgentOutput(routed_agent="chat_agent", latency_ms=10)
    result = ScenarioResult(external_id="t1", output=output)

    scorer = LLMJudgeScorer(api_key="fake")
    scorer.client = MagicMock()
    scorer.client.chat.completions.create.return_value = _fake_response("not json at all")

    scorer.score(scenario, output, result)
    assert result.llm_judge_score == 0.0
    assert "parse_error" in result.llm_judge_reasoning


def test_judge_skipped_when_prompt_absent():
    scenario = Scenario(
        external_id="t1", category="clear", complexity="single_step",
        input="x", expected={}, rubric=Rubric(hard_checks=[]),  # no llm_judge_prompt
    )
    output = AgentOutput(routed_agent="chat_agent", latency_ms=10)
    result = ScenarioResult(external_id="t1", output=output)

    scorer = LLMJudgeScorer(api_key="fake")
    scorer.client = MagicMock()
    scorer.score(scenario, output, result)
    assert result.llm_judge_score is None
    scorer.client.chat.completions.create.assert_not_called()
