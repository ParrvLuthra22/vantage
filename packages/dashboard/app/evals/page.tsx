import Link from "next/link";

import { ApiDownState } from "@/components/api-down-state";
import { EvalRunsTable } from "@/components/eval-runs-table";
import { listEvalRuns } from "@/lib/api/client";
import { checkApiHealth } from "@/lib/api/health";

interface PageProps {
  searchParams: Promise<{ suite?: string; limit?: string }>;
}

export default async function EvalsPage({ searchParams }: PageProps) {
  const params = await searchParams;
  const suite = params.suite ?? "orchestrator_v1";
  const limit = parseLimit(params.limit);

  const health = await checkApiHealth();
  if (!health.ok) return <ApiDownState error={health.error} />;

  const runs = await listEvalRuns(suite, limit);

  return (
    <div className="min-h-screen">
      <Navbar />
      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-6 flex items-baseline justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Eval runs</h1>
            <p className="text-sm text-muted-foreground mt-1">
              Suite: <span className="font-mono">{suite}</span> · {runs.length} recent
            </p>
          </div>
        </div>
        {runs.length === 0 ? <EmptyState suite={suite} /> : <EvalRunsTable runs={runs} />}
      </main>
    </div>
  );
}

// Same defensive parsing as app/traces/page.tsx — see its comment for why each
// case matters (blank/NaN/fractional/negative all need a sane fallback rather
// than a raw pass-through to the API).
const DEFAULT_LIMIT = 50;
const MAX_LIMIT = 200;

function parseLimit(raw: string | undefined): number {
  if (raw === undefined || raw.trim() === "") return DEFAULT_LIMIT;
  const n = Number(raw);
  if (!Number.isInteger(n)) return DEFAULT_LIMIT;
  return Math.min(Math.max(n, 1), MAX_LIMIT);
}

function EmptyState({ suite }: { suite: string }) {
  return (
    <div className="rounded-md border border-dashed border-border p-12 text-center">
      <p className="text-sm text-muted-foreground">
        No eval runs found for suite <span className="font-mono">{suite}</span>.
      </p>
      <p className="text-xs text-muted-foreground mt-2">
        Run <span className="font-mono">vantage eval run</span> to see runs appear here.
      </p>
    </div>
  );
}

function Navbar() {
  return (
    <nav className="border-b border-border">
      <div className="mx-auto max-w-7xl px-6 h-14 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-primary" />
          <span className="font-semibold tracking-tight">Vantage</span>
        </div>
        <div className="flex items-center gap-4 text-sm text-muted-foreground">
          <Link href="/traces" className="hover:text-foreground">Traces</Link>
          <Link href="/evals" className="text-foreground">Evals</Link>
        </div>
      </div>
    </nav>
  );
}
