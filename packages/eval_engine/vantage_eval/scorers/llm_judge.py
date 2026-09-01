"""LLM-as-judge scorer using OpenAI's chat completion API.

Uses GPT-4o-mini by default. Structured JSON output for parse safety.
Chain-of-thought reasoning captured for spot-checking bias.

Known biases (documented so we're honest about limitations):
  * Position bias: not applicable here (we don't show multiple options to compare)
  * Length bias: partially mitigated — the judge scores a routing DECISION,
    not free-form text, so length isn't a dominant signal
  * Self-preference bias: applicable if our agent-under-test is also GPT-based;
    would prefer to use a different-family judge for evaluating GPT-family agents.
    We use OpenAI to judge Gemini-based Vesper, so self-preference doesn't apply here.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from jinja2 import Template
from openai import OpenAI

from vantage_eval.models import AgentOutput, Scenario, ScenarioResult
from vantage_eval.scorers.base import Scorer

# Rough per-1M-token pricing for GPT-4o-mini (Aug 2026 — verify against current pricing)
INPUT_COST_PER_MTOK = 0.15
OUTPUT_COST_PER_MTOK = 0.60


SYSTEM_PROMPT = """\
You are evaluating an AI orchestrator's routing decision. Your job is to judge whether \
the orchestrator made a defensible routing choice given the user's input, not whether \
the routed agent successfully executed the task.

Reason step-by-step about the decision, then output a strict JSON object with fields:
  reasoning: string (your chain of thought)
  score:     integer 1-5

Score guide:
  1 = obviously wrong routing (agent cannot possibly serve this request)
  2 = wrong but understandable (a reasonable person could see why the model made this mistake)
  3 = defensible but suboptimal (a better routing exists but this isn't wrong)
  4 = correct routing (the right agent picked, entities reasonably extracted)
  5 = correct and well-reasoned (right agent, all entities correct, no over/under-extraction)

Output ONLY the JSON object. No prose before or after.
"""


class LLMJudgeScorer(Scorer):
    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: Optional[str] = None,
        temperature: float = 0.0,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.client = OpenAI(api_key=api_key or os.environ["OPENAI_API_KEY"])
        self.total_cost_usd = 0.0

    def score(self, scenario: Scenario, output: AgentOutput, result: ScenarioResult) -> None:
        if not scenario.rubric.llm_judge_prompt:
            return  # scenario opted out of LLM judging

        user_prompt = self._render_prompt(scenario, output)

        response = self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )

        raw = response.choices[0].message.content or ""
        result.llm_judge_raw_response = raw

        # Track cost
        if response.usage:
            in_cost = response.usage.prompt_tokens * INPUT_COST_PER_MTOK / 1_000_000
            out_cost = response.usage.completion_tokens * OUTPUT_COST_PER_MTOK / 1_000_000
            self.total_cost_usd += in_cost + out_cost

        try:
            parsed = json.loads(raw)
            score_val = float(parsed.get("score", 0))
            reasoning = str(parsed.get("reasoning", ""))
        except (json.JSONDecodeError, ValueError, TypeError):
            # Model returned malformed JSON — record as 0 score with the raw text
            score_val = 0.0
            reasoning = f"parse_error: {raw[:200]}"

        # Clamp to valid range
        score_val = max(0.0, min(5.0, score_val))

        result.llm_judge_score = score_val
        result.llm_judge_reasoning = reasoning

    def _render_prompt(self, scenario: Scenario, output: AgentOutput) -> str:
        # scenario.rubric.llm_judge_prompt is a Jinja2 template
        tmpl = Template(scenario.rubric.llm_judge_prompt or "")
        return tmpl.render(
            input=scenario.input,
            context=scenario.context,
            expected=scenario.expected,
            actual={
                "routed_agent": output.routed_agent,
                "extracted_entities": output.extracted_entities,
                "reasoning": output.reasoning,
            },
        )
