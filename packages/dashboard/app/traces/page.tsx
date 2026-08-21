import { ApiDownState } from "@/components/api-down-state";
import { EmptyState } from "@/components/empty-state";
import { TracesTable } from "@/components/traces-table";
import { listTraces } from "@/lib/api/client";
import { checkApiHealth } from "@/lib/api/health";

interface PageProps {
  searchParams: Promise<{ project?: string; limit?: string }>;
}

export default async function TracesPage({ searchParams }: PageProps) {
  const params = await searchParams;
  const project = params.project ?? "vesper";
  const limit = parseLimit(params.limit);

  // Probed before fetching so a stopped backend renders as "API unreachable"
  // rather than throwing into the error boundary or, worse, looking like an
  // empty project.
  const health = await checkApiHealth();
  if (!health.ok) return <ApiDownState error={health.error} />;

  const traces = await listTraces(project, limit);

  return (
    <div className="min-h-screen">
      <Navbar />
      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-6 flex items-baseline justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Traces</h1>
            <p className="text-sm text-muted-foreground mt-1">
              Project: <span className="font-mono">{project}</span> · {traces.length}{" "}
              recent
            </p>
          </div>
        </div>
        {traces.length === 0 ? (
          <EmptyState project={project} />
        ) : (
          <TracesTable traces={traces} />
        )}
      </main>
    </div>
  );
}

/**
 * Search params are user-controlled, so every failure mode has to land on a
 * sane default rather than an error page:
 *   - `?limit=abc` and `?limit=` parse to NaN, which serialises into the query
 *     string as "NaN" and comes back a 422 from the API.
 *   - `?limit=1e5` parses to 1 under parseInt, silently showing a single row —
 *     worse than an error, because it looks like it worked.
 *   - `?limit=-5` used to reach Postgres and raise "LIMIT must not be negative".
 */
const DEFAULT_LIMIT = 50;
const MAX_LIMIT = 200;

function parseLimit(raw: string | undefined): number {
  // An absent or blank param means "unspecified", not zero — Number("") is 0,
  // which would otherwise clamp to a one-row page.
  if (raw === undefined || raw.trim() === "") return DEFAULT_LIMIT;
  const n = Number(raw); // rejects "50abc" and "1e5"-style surprises, unlike parseInt
  if (!Number.isInteger(n)) return DEFAULT_LIMIT;
  return Math.min(Math.max(n, 1), MAX_LIMIT);
}

function Navbar() {
  return (
    <nav className="border-b border-border">
      <div className="mx-auto max-w-7xl px-6 h-14 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-primary" />
          <span className="font-semibold tracking-tight">Vantage</span>
        </div>
        <div className="text-xs text-muted-foreground font-mono">v0.2.0</div>
      </div>
    </nav>
  );
}
