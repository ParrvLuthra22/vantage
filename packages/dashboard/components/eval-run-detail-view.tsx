import Link from "next/link";
import { ChevronLeft } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { SetBaselineButton } from "@/components/set-baseline-button";
import { EvalResult, EvalRunDetail } from "@/lib/api/types";
import { formatCost, formatDuration, relativeTime, truncateId } from "@/lib/utils/format";

/** Server component — unlike TraceDetailView, nothing here needs client state,
 * so this stays a plain server-rendered page (no "use client" needed). The one
 * interactive control, SetBaselineButton, is its own small client component
 * rather than turning this whole view into one. */
export function EvalRunDetailView({ run }: { run: EvalRunDetail }) {
  const knownFailing = run.results.filter((r) => r.scenario.known_failing);
  const realFailures = run.results.filter((r) => !r.scenario.known_failing && !r.passed);
  const passed = run.results.filter((r) => !r.scenario.known_failing && r.passed);

  return (
    <div className="min-h-screen">
      <nav className="border-b border-border">
        <div className="mx-auto max-w-7xl px-6 h-14 flex items-center justify-between">
          <Link href="/" className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-primary" />
            <span className="font-semibold tracking-tight">Vantage</span>
          </Link>
          <div className="text-xs text-muted-foreground font-mono">v0.2.0</div>
        </div>
      </nav>

      <main className="mx-auto max-w-7xl px-6 py-8">
        <Link
          href="/evals"
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-4"
        >
          <ChevronLeft className="w-4 h-4" /> All eval runs
        </Link>

        <div className="mb-8 flex items-start justify-between">
          <div>
            <h1 className="text-xl font-mono break-all flex items-center gap-2">
              {run.run_id}
              {run.is_baseline && <Badge variant="default">baseline</Badge>}
            </h1>
            <p className="text-sm text-muted-foreground mt-1">
              agent <span className="font-mono">{truncateId(run.agent_version, 12)}</span> ·
              judge <span className="font-mono">{run.judge_model}</span> ·{" "}
              {relativeTime(run.started_at)}
            </p>
          </div>
          {!run.is_baseline && <SetBaselineButton runId={run.run_id} />}
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-8">
          <MetricCard
            label="Effective pass rate"
            value={`${(run.summary.pass_rate * 100).toFixed(0)}%`}
            sub={`${run.summary.passed}/${run.summary.total}`}
          />
          <MetricCard label="Known failing" value={run.summary.known_failing.toString()} sub="excluded above" />
          <MetricCard
            label="Avg judge score"
            value={run.summary.avg_llm_score != null ? run.summary.avg_llm_score.toFixed(2) : "—"}
          />
          <MetricCard label="Judge cost" value={formatCost(run.summary.total_judge_cost_usd)} />
        </div>

        {realFailures.length > 0 && (
          <ResultSection title="Failed" tone="destructive" results={realFailures} />
        )}
        {knownFailing.length > 0 && (
          <ResultSection
            title="Known failing (excluded from pass rate)"
            tone="secondary"
            results={knownFailing}
            showIssueLink
          />
        )}
        {passed.length > 0 && <ResultSection title="Passed" tone="secondary" results={passed} collapsedByDefault />}
      </main>
    </div>
  );
}

function ResultSection({
  title,
  tone,
  results,
  showIssueLink = false,
  collapsedByDefault = false,
}: {
  title: string;
  tone: "destructive" | "secondary";
  results: EvalResult[];
  showIssueLink?: boolean;
  collapsedByDefault?: boolean;
}) {
  return (
    <details className="mb-6" open={!collapsedByDefault}>
      <summary className="text-sm uppercase tracking-wider text-muted-foreground mb-2 cursor-pointer select-none">
        {title} ({results.length})
      </summary>
      <div className="rounded-md border border-border overflow-hidden mt-2">
        <table className="w-full">
          <thead className="bg-secondary/50">
            <tr className="text-left text-xs uppercase tracking-wider text-muted-foreground">
              <th className="px-4 py-3 font-medium">Scenario</th>
              <th className="px-4 py-3 font-medium">Category</th>
              <th className="px-4 py-3 font-medium">Trace</th>
              <th className="px-4 py-3 font-medium text-right">Judge score</th>
              <th className="px-4 py-3 font-medium text-right">Latency</th>
              {showIssueLink && <th className="px-4 py-3 font-medium">Issue</th>}
            </tr>
          </thead>
          <tbody>
            {results.map((r) => (
              <tr key={r.result_id} className="border-t border-border">
                <td className="px-4 py-3 text-sm font-mono">
                  <Badge variant={r.passed ? "secondary" : tone}>{r.scenario.external_id}</Badge>
                </td>
                <td className="px-4 py-3 text-sm text-muted-foreground">{r.scenario.category}</td>
                <td className="px-4 py-3 text-sm">
                  {r.trace_id ? (
                    <Link href={`/traces/${r.trace_id}`} className="font-mono text-primary hover:underline">
                      {truncateId(r.trace_id, 8)}
                    </Link>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </td>
                <td className="px-4 py-3 text-sm font-mono text-right">
                  {r.llm_judge_score != null ? r.llm_judge_score.toFixed(1) : "—"}
                </td>
                <td className="px-4 py-3 text-sm font-mono text-right">
                  {r.latency_ms != null ? formatDuration(r.latency_ms) : "—"}
                </td>
                {showIssueLink && (
                  <td className="px-4 py-3 text-xs text-muted-foreground max-w-xs truncate">
                    {r.scenario.known_failing_reason ?? "—"}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function MetricCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-md border border-border p-4">
      <div className="text-xs uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="text-2xl font-semibold mt-1 font-mono">{value}</div>
      {sub && <div className="text-xs text-muted-foreground mt-0.5">{sub}</div>}
    </div>
  );
}
