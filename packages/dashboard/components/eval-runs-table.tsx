import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { EvalRun } from "@/lib/api/types";
import { formatCost, formatDuration, relativeTime, truncateId } from "@/lib/utils/format";

export function EvalRunsTable({ runs }: { runs: EvalRun[] }) {
  return (
    <div className="rounded-md border border-border overflow-hidden">
      <table className="w-full">
        <thead className="bg-secondary/50">
          <tr className="text-left text-xs uppercase tracking-wider text-muted-foreground">
            <th className="px-4 py-3 font-medium">Run</th>
            <th className="px-4 py-3 font-medium">Started</th>
            <th className="px-4 py-3 font-medium">Agent version</th>
            <th className="px-4 py-3 font-medium text-right">Pass rate</th>
            <th className="px-4 py-3 font-medium text-right">Known failing</th>
            <th className="px-4 py-3 font-medium text-right">Judge cost</th>
            <th className="px-4 py-3 font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr
              key={r.run_id}
              className="border-t border-border hover:bg-secondary/30 transition-colors"
            >
              <td className="px-4 py-3">
                <Link
                  href={`/evals/runs/${r.run_id}`}
                  className="font-mono text-sm text-primary hover:underline"
                >
                  {truncateId(r.run_id, 12)}
                </Link>
              </td>
              <td className="px-4 py-3 text-sm text-muted-foreground">
                {relativeTime(r.started_at)}
              </td>
              <td className="px-4 py-3 text-sm font-mono">{truncateId(r.agent_version, 8)}</td>
              <td className="px-4 py-3 text-sm font-mono text-right">
                {(r.summary.pass_rate * 100).toFixed(0)}% ({r.summary.passed}/{r.summary.total})
              </td>
              <td className="px-4 py-3 text-sm font-mono text-right">
                {r.summary.known_failing || "—"}
              </td>
              <td className="px-4 py-3 text-sm font-mono text-right">
                {formatCost(r.summary.total_judge_cost_usd)}
              </td>
              <td className="px-4 py-3">
                {r.is_baseline ? (
                  <Badge variant="default">baseline</Badge>
                ) : (
                  <Badge variant="secondary">{formatDuration(r.summary.duration_seconds * 1000)}</Badge>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
