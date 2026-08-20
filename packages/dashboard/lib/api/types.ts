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
