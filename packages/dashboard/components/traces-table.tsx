import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Trace } from "@/lib/api/types";
import {
  durationMs,
  formatCost,
  formatDuration,
  formatTokens,
  relativeTime,
  truncateId,
} from "@/lib/utils/format";

export function TracesTable({ traces }: { traces: Trace[] }) {
  return (
    <div className="rounded-md border border-border overflow-hidden">
      <table className="w-full">
        <thead className="bg-secondary/50">
          <tr className="text-left text-xs uppercase tracking-wider text-muted-foreground">
            <th className="px-4 py-3 font-medium">Trace ID</th>
            <th className="px-4 py-3 font-medium">Started</th>
            <th className="px-4 py-3 font-medium">Duration</th>
            <th className="px-4 py-3 font-medium text-right">Tokens</th>
            <th className="px-4 py-3 font-medium text-right">Cost</th>
            <th className="px-4 py-3 font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          {traces.map((t) => (
            <tr
              key={t.trace_id}
              className="border-t border-border hover:bg-secondary/30 transition-colors"
            >
              <td className="px-4 py-3">
                <Link
                  href={`/traces/${t.trace_id}`}
                  className="font-mono text-sm text-primary hover:underline"
                >
                  {truncateId(t.trace_id, 12)}
                </Link>
              </td>
              <td className="px-4 py-3 text-sm text-muted-foreground">
                {relativeTime(t.start_time)}
              </td>
              <td className="px-4 py-3 text-sm font-mono">
                {formatDuration(durationMs(t.start_time, t.end_time))}
              </td>
              <td className="px-4 py-3 text-sm font-mono text-right">
                {formatTokens(t.total_tokens)}
              </td>
              <td className="px-4 py-3 text-sm font-mono text-right">
                {formatCost(t.total_cost_usd)}
              </td>
              <td className="px-4 py-3">
                <Badge variant={t.status === "ok" ? "secondary" : "destructive"}>
                  {t.status}
                </Badge>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
