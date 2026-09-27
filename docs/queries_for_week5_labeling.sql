-- Sample queries the Week 5 human-labeling workflow will need against
-- eval_results.tool_sequence / .tool_calls / .final_reply (added in migration
-- 1cfabcd99636, "add tool_sequence, tool_calls, final_reply to eval_results").
--
-- This file is documentation, not something run automatically — it exists to
-- prove the schema supports the query patterns Week 5 will need, and to give
-- a labeler a starting point instead of reverse-engineering the JSONB shape.
--
-- All three columns are NULL on any row persisted before this migration (see
-- docs/deferred_for_week5.md item 1) — every query below should be read as
-- "among rows that have trajectory data," not "among all eval_results."


-- All scenarios where Vesper made a multi-tool call (tool_sequence is a
-- native jsonb array column already, no cast needed).
select s.external_id, r.tool_sequence
from eval_results r
join eval_scenarios s on r.scenario_id = s.scenario_id
where jsonb_array_length(r.tool_sequence) > 1;


-- All scenarios where the reply mentioned a specific action verb — e.g. the
-- "did chat_agent claim it did something it has no tool for" hallucination
-- pattern documented in docs/interview_exhibits/.
select s.external_id, r.final_reply
from eval_results r
join eval_scenarios s on r.scenario_id = s.scenario_id
where r.final_reply ilike '%sent%' or r.final_reply ilike '%scheduled%';


-- Tool-call arguments for a specific tool across every scenario that used it
-- (tool_calls is [{"name": ..., "arguments": {...}}, ...] — see
-- AgentOutput.tool_calls in vantage_eval/models.py). Useful for "did it pick
-- a sensible value" spot-checks, e.g. every set_volume level Vesper chose.
select s.external_id, tc->>'name' as tool_name, tc->'arguments' as arguments
from eval_results r
join eval_scenarios s on r.scenario_id = s.scenario_id
cross join lateral jsonb_array_elements(r.tool_calls) as tc
where tc->>'name' = 'set_volume';


-- NOTE on validating routed_agent against tool_sequence[0]: there is no
-- separate `routed_agent` column to compare tool_sequence against — by
-- design, AgentOutput.routed_agent IS tool_sequence[0] (or the REFUSE
-- sentinel; see the docstring on AgentOutput in
-- packages/eval_engine/vantage_eval/models.py), captured at scoring time
-- into deterministic_scores's `routed_agent_matches` check instead of its
-- own column. So there's nothing to cross-validate here — a query like
-- "does the hard check's recorded routing agree with tool_sequence[0]" would
-- just be checking a value against a copy of itself. What IS worth a
-- multi-tool-call review is scenarios that FAILED routed_agent_matches
-- alongside their full trajectory, so a labeler can see whether the
-- mismatch was a real routing miss or a judge/rubric issue (the exact class
-- of bug docs/interview_exhibits/measurement_fix_ab_evidence.md documents):
select
    s.external_id,
    r.deterministic_scores->'routed_agent_matches'->>'detail' as hard_check_detail,
    r.tool_sequence,
    r.final_reply
from eval_results r
join eval_scenarios s on r.scenario_id = s.scenario_id
where (r.deterministic_scores->'routed_agent_matches'->>'passed')::boolean = false
  and r.tool_sequence is not null;
