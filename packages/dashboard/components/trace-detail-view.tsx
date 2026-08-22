"use client";

import { ChevronLeft } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { TraceDetail } from "@/lib/api/types";
import {
  durationMs,
  formatCost,
  formatDuration,
  formatTokens,
  relativeTime,
} from "@/lib/utils/format";

import { SpanDetailPanel } from "./span-detail-panel";
import { SpanWaterfall } from "./span-waterfall";

/**
 * Client wrapper owning the selected-span state.
 *
 * Selection has to live above both the waterfall and the panel, and the page
 * itself stays a server component so the trace is fetched server-side with the
 * API key. This is the boundary: data comes down as props, interaction starts
 * here.
 */
export function TraceDetailView({ trace }: { trace: TraceDetail }) {
  const [selectedSpanId, setSelectedSpanId] = useState<string | null>(null);
  const selectedSpan = selectedSpanId
    ? (trace.spans.find((s) => s.span_id === selectedSpanId) ?? null)
    : null;

  const traceDuration = durationMs(trace.start_time, trace.end_time);

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
          href="/traces"
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-4"
        >
          <ChevronLeft className="w-4 h-4" /> All traces
        </Link>

        <div className="mb-8">
          <h1 className="text-xl font-mono break-all">{trace.trace_id}</h1>
          <p className="text-sm text-muted-foreground mt-1">
            <span className="font-mono">{trace.project}</span> ·{" "}
            {relativeTime(trace.start_time)}
          </p>
        </div>

        <div className="grid grid-cols-4 gap-4 mb-8">
          <MetricCard label="Duration" value={formatDuration(traceDuration)} />
          <MetricCard label="Spans" value={trace.spans.length.toString()} />
          <MetricCard label="Tokens" value={formatTokens(trace.total_tokens)} />
          <MetricCard label="Cost" value={formatCost(trace.total_cost_usd)} />
        </div>

        {/* items-start keeps the two columns independent: without it the grid
            stretches both to the tallest, so selecting a span with a long
            attribute blob would resize the waterfall column too. */}
        <div className="grid grid-cols-[1fr_360px] gap-4 items-start">
          <div>
            <div className="mb-2 flex items-center justify-between h-6">
              <h2 className="text-sm uppercase tracking-wider text-muted-foreground">
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
              selectedId={selectedSpanId}
              onSelect={setSelectedSpanId}
            />
          </div>
          <div>
            <div className="mb-2 h-6 flex items-center">
              <h2 className="text-sm uppercase tracking-wider text-muted-foreground">
                Span details
              </h2>
            </div>
            {/* Sticky so a tall attribute blob does not push the panel out of
                view while the waterfall stays put. */}
            <div className="sticky top-4">
              <SpanDetailPanel span={selectedSpan} />
            </div>
          </div>
        </div>
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
