import Link from "next/link";
import { notFound } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { compareRuns } from "@/lib/api/client";
import { ScenarioChange } from "@/lib/api/types";
import { truncateId } from "@/lib/utils/format";

interface PageProps {
  params: Promise<{ run_id: string; baseline_id: string }>;
}

/** Server component — same shape as EvalRunDetailView: no interaction here,
 * so no "use client" needed. */
export default async function CompareRunsPage({ params }: PageProps) {
  const { run_id, baseline_id } = await params;

  let report;
  try {
    report = await compareRuns(run_id, baseline_id);
  } catch (e) {
    // Same 404/422 -> not-found mapping as app/evals/runs/[run_id]/page.tsx.
    if (e instanceof Error && /Vantage API (404|422)/.test(e.message)) notFound();
    throw e;
  }

  const regressions = report.changes.filter((c) => c.change_type === "regression");
  const improvements = report.changes.filter((c) => c.change_type === "improvement");
  const stable = report.changes.filter((c) => c.change_type.startsWith("stable"));
  const added = report.changes.filter((c) => c.change_type === "new");
  const removed = report.changes.filter((c) => c.change_type === "removed");

  const deltaPct = report.pass_rate_delta * 100;
  const deltaColor = deltaPct >= 0 ? "text-green-600 dark:text-green-400" : "text-destructive";
  const deltaSign = deltaPct >= 0 ? "+" : "";

  return (
    <div className="min-h-screen">
      <Navbar />
      <main className="mx-auto max-w-7xl px-6 py-8">
        <Link
          href={`/evals/runs/${run_id}`}
          className="text-sm text-muted-foreground hover:text-foreground"
        >
          ← Run {truncateId(run_id)}
        </Link>

        <h1 className="text-xl font-semibold mt-4">Regression Report</h1>
        <p className="text-sm text-muted-foreground mt-1 font-mono">
          {truncateId(report.baseline_run_id)} → {truncateId(report.current_run_id)}
        </p>

        <div className="mt-6 rounded-md border border-border p-6">
          <div className="text-xs uppercase text-muted-foreground">Pass rate</div>
          <div className="mt-2 text-3xl font-mono">
            {/* One decimal: pass rates over a 40-scenario suite are multiples of
                2.5%, so toFixed(0) shows "20% → 28% (+7.5pp)" for a real
                20.0% → 27.5% — visibly inconsistent arithmetic. */}
            {(report.baseline_pass_rate * 100).toFixed(1)}% →{" "}
            {(report.current_pass_rate * 100).toFixed(1)}%{" "}
            <span className={`text-xl ${deltaColor}`}>
              ({deltaSign}
              {deltaPct.toFixed(1)}pp)
            </span>
          </div>
        </div>

        {regressions.length > 0 && (
          <ChangeSection title="Regressions" tone="regression" changes={regressions} />
        )}
        {improvements.length > 0 && (
          <ChangeSection title="Improvements" tone="improvement" changes={improvements} />
        )}
        {added.length > 0 && <ChangeSection title="New" tone="neutral" changes={added} />}
        {removed.length > 0 && <ChangeSection title="Removed" tone="neutral" changes={removed} />}
        <ChangeSection title="Stable" tone="neutral" changes={stable} collapsed />
      </main>
    </div>
  );
}

const SECTION_TITLE_COLOR: Record<string, string> = {
  regression: "text-destructive",
  improvement: "text-green-600 dark:text-green-400",
  neutral: "text-muted-foreground",
};

function ChangeSection({
  title,
  tone,
  changes,
  collapsed = false,
}: {
  title: string;
  tone: "regression" | "improvement" | "neutral";
  changes: ScenarioChange[];
  collapsed?: boolean;
}) {
  const rows = (
    <ul className="space-y-2">
      {changes.map((c) => (
        <li
          key={c.external_id}
          className="font-mono text-sm rounded border border-border p-3 flex items-center justify-between"
        >
          <span className="flex items-center gap-2">
            <ScenarioBadge change={c} />
            {c.external_id}
          </span>
          <span className="text-xs text-muted-foreground">
            {c.baseline_llm_score ?? "—"} → {c.current_llm_score ?? "—"}
          </span>
        </li>
      ))}
    </ul>
  );

  return (
    <div className="mt-8">
      <h2 className={`text-sm uppercase tracking-wider mb-2 ${SECTION_TITLE_COLOR[tone]}`}>
        {title} ({changes.length})
      </h2>
      {collapsed && changes.length > 5 ? (
        <details>
          <summary className="text-sm text-muted-foreground cursor-pointer">
            Show all {changes.length}
          </summary>
          <div className="mt-2">{rows}</div>
        </details>
      ) : (
        rows
      )}
    </div>
  );
}

function ScenarioBadge({ change }: { change: ScenarioChange }) {
  if (change.change_type === "regression") return <Badge variant="destructive">regression</Badge>;
  if (change.change_type === "improvement")
    return (
      <Badge
        variant="outline"
        className="border-green-600/40 text-green-600 dark:border-green-400/40 dark:text-green-400"
      >
        improvement
      </Badge>
    );
  return <Badge variant="secondary">{change.change_type.replace("_", " ")}</Badge>;
}

function Navbar() {
  return (
    <nav className="border-b border-border">
      <div className="mx-auto max-w-7xl px-6 h-14 flex items-center justify-between">
        <Link href="/" className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-primary" />
          <span className="font-semibold tracking-tight">Vantage</span>
        </Link>
        <div className="flex items-center gap-4 text-sm text-muted-foreground">
          <Link href="/traces" className="hover:text-foreground">
            Traces
          </Link>
          <Link href="/evals" className="text-foreground">
            Evals
          </Link>
        </div>
      </div>
    </nav>
  );
}
