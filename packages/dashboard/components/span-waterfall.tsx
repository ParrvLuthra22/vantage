import { Span } from "@/lib/api/types";

export function SpanWaterfall({
  spans,
}: {
  spans: Span[];
  traceStart: string;
  traceEnd: string | null;
}) {
  return (
    <div className="rounded-md border border-border p-4">
      <p className="text-sm text-muted-foreground">Waterfall coming in P17.</p>
      <ul className="mt-4 space-y-1">
        {spans.map((s) => (
          <li key={s.span_id} className="text-xs font-mono">
            {s.name} ({s.span_id.slice(0, 8)})
          </li>
        ))}
      </ul>
    </div>
  );
}
