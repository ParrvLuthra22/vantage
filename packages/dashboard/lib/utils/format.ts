import { formatDistanceToNow } from "date-fns";

/** First 8 chars of a UUID — enough to identify a trace at a glance in a table. */
export function truncateId(id: string, len = 8): string {
  return id.slice(0, len);
}

export function relativeTime(iso: string): string {
  return formatDistanceToNow(new Date(iso), { addSuffix: true });
}

export function durationMs(startIso: string, endIso: string | null): number | null {
  if (!endIso) return null;
  return new Date(endIso).getTime() - new Date(startIso).getTime();
}

/** Null renders as an em dash: an in-flight span has no duration yet, not zero. */
export function formatDuration(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

/**
 * Sub-cent costs render in millidollars. Per-call LLM spend is routinely
 * $0.0006, and four decimal places of leading zeros is unreadable in a column.
 */
export function formatCost(usd: number): string {
  if (usd < 0.01) return `$${(usd * 1000).toFixed(2)}m`;
  return `$${usd.toFixed(4)}`;
}

export function formatTokens(n: number): string {
  if (n < 1000) return n.toString();
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}
