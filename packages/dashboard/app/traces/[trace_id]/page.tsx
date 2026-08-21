import { ChevronLeft } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";

import { SpanWaterfall } from "@/components/span-waterfall";
import { Badge } from "@/components/ui/badge";
import { getTrace } from "@/lib/api/client";
import {
  durationMs,
  formatCost,
  formatDuration,
  formatTokens,
  relativeTime,
} from "@/lib/utils/format";

interface PageProps {
  params: Promise<{ trace_id: string }>;
}

export default async function TraceDetailPage({ params }: PageProps) {
  const { trace_id } = await params;

  let trace;
  try {
    trace = await getTrace(trace_id);
  } catch (e) {
    // 404 is a real trace id that isn't here; 422 is an id that isn't a UUID at
    // all, which FastAPI rejects during path validation. Both mean "no such
    // trace" to a reader who typed or pasted something wrong, so both render the
    // not-found page rather than an error boundary.
    if (e instanceof Error && /Vantage API (404|422)/.test(e.message)) notFound();
    throw e;
  }

  const traceDuration = durationMs(trace.start_time, trace.end_time);

  return (
    <div className="min-h-screen">
      <Navbar />
      <main className="mx-auto max-w-7xl px-6 py-8">
        <Link
          href="/traces"
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors mb-4"
        >
          <ChevronLeft className="w-4 h-4" />
          All traces
        </Link>

        <div className="mb-8">
          <h1 className="text-2xl font-semibold tracking-tight font-mono">
            {trace.trace_id}
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Project <span className="font-mono">{trace.project}</span> ·{" "}
            {relativeTime(trace.start_time)}
          </p>
        </div>

        <div className="grid grid-cols-4 gap-4 mb-8">
          <MetricCard label="Duration" value={formatDuration(traceDuration)} />
          <MetricCard label="Spans" value={trace.spans.length.toString()} />
          <MetricCard label="Tokens" value={formatTokens(trace.total_tokens)} />
          <MetricCard label="Cost" value={formatCost(trace.total_cost_usd)} />
        </div>

        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-medium tracking-wide uppercase text-muted-foreground">
            Span waterfall
          </h2>
          <Badge variant={trace.status === "ok" ? "secondary" : "destructive"}>
            {trace.status}
          </Badge>
        </div>

        <SpanWaterfall
          spans={trace.spans}
          traceStart={trace.start_time}
          traceEnd={trace.end_time}
        />
      </main>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border p-4">
      <div className="text-xs uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className="text-2xl font-semibold mt-1 font-mono">{value}</div>
    </div>
  );
}

function Navbar() {
  return (
    <nav className="border-b border-border">
      <div className="mx-auto max-w-7xl px-6 h-14 flex items-center justify-between">
        <Link href="/" className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-primary" />
          <span className="font-semibold tracking-tight">Vantage</span>
        </Link>
        <div className="text-xs text-muted-foreground font-mono">v0.2.0</div>
      </div>
    </nav>
  );
}
