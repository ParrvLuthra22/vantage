/**
 * Mirrors the backend's Pydantic schemas in `vantage_api/schemas.py`.
 *
 * This is a hand-maintained copy of the API contract — the dashboard cannot
 * import Python types, so drift here surfaces as a runtime shape mismatch
 * rather than a compile error. Any change to TraceOut / TraceDetail / SpanOut
 * on the backend has to land here too.
 */

export type SpanStatus = "ok" | "error";

export interface Span {
  span_id: string;
  trace_id: string;
  parent_span_id: string | null;
  name: string;
  start_time: string;
  end_time: string | null;
  attributes: Record<string, unknown>;
  status: SpanStatus;
  error_message: string | null;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number | null;
}

export interface Trace {
  trace_id: string;
  project: string;
  root_span_id: string | null;
  start_time: string;
  end_time: string | null;
  status: SpanStatus;
  total_cost_usd: number;
  total_tokens: number;
}

export interface TraceDetail extends Trace {
  spans: Span[];
}

export interface EvalScenarioSummary {
  external_id: string;
  category: string;
  known_failing: boolean;
  known_failing_reason: string | null;
}

export interface EvalResult {
  result_id: string;
  scenario: EvalScenarioSummary;
  trace_id: string | null;
  deterministic_scores: Record<string, { check_name: string; passed: boolean; detail: string | null }>;
  llm_judge_score: number | null;
  llm_judge_reasoning: string | null;
  latency_ms: number | null;
  latency_within_budget: boolean | null;
  passed: boolean;
}

export interface EvalRunSummary {
  total: number;
  passed: number;
  failed: number;
  pass_rate: number;
  total_scenarios: number;
  known_failing: number;
  avg_llm_score: number | null;
  total_judge_cost_usd: number;
  duration_seconds: number;
  by_category: Record<string, Record<string, number>>;
}

export interface EvalRun {
  run_id: string;
  suite_id: string;
  agent_version: string;
  judge_model: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  is_baseline: boolean;
  summary: EvalRunSummary;
}

export interface EvalRunDetail extends EvalRun {
  results: EvalResult[];
}
