import { Span } from "@/lib/api/types";
import {
  durationMs,
  formatCost,
  formatDuration,
  formatTokens,
} from "@/lib/utils/format";

/**
 * Attributes are rendered with JSON.stringify into a text node. React escapes
 * text content in JSX, so a span attribute containing `<script>` or an onclick
 * payload is displayed literally rather than parsed — user-supplied attribute
 * strings cannot inject markup here. That guarantee comes from staying in text
 * nodes: if this ever grows a dangerouslySetInnerHTML path (rendering an
 * attribute as markdown, say) the value would need sanitising first, because
 * React's escaping does not apply to raw HTML injection.
 */
export function SpanDetailPanel({ span }: { span: Span | null }) {
  if (!span) {
    return (
      <div className="rounded-md border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
        Select a span to inspect
      </div>
    );
  }

  const dur = durationMs(span.start_time, span.end_time);

  return (
    <div className="rounded-md border border-border p-4 space-y-4">
      <div>
        <div className="text-xs uppercase tracking-wider text-muted-foreground">
          Name
        </div>
        <div className="font-mono text-sm mt-1 break-all">{span.name}</div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <MiniStat label="Duration" value={formatDuration(dur)} />
        <MiniStat label="Status" value={span.status} />
        {span.model && <MiniStat label="Model" value={span.model} />}
        {span.input_tokens != null && (
          <MiniStat label="Input tokens" value={formatTokens(span.input_tokens)} />
        )}
        {span.output_tokens != null && (
          <MiniStat label="Output tokens" value={formatTokens(span.output_tokens)} />
        )}
        {span.cost_usd != null && (
          <MiniStat label="Cost" value={formatCost(span.cost_usd)} />
        )}
      </div>

      {span.error_message && (
        <div>
          <div className="text-xs uppercase tracking-wider text-destructive">
            Error
          </div>
          <div className="mt-1 rounded bg-destructive/10 border border-destructive/40 p-2 font-mono text-xs break-all">
            {span.error_message}
          </div>
        </div>
      )}

      <div>
        <div className="text-xs uppercase tracking-wider text-muted-foreground mb-1">
          Attributes
        </div>
        <pre className="rounded bg-secondary/40 p-3 font-mono text-xs overflow-x-auto max-h-96 overflow-y-auto">
          {JSON.stringify(span.attributes, null, 2)}
        </pre>
      </div>

      <div>
        <div className="text-xs uppercase tracking-wider text-muted-foreground mb-1">
          IDs
        </div>
        <div className="space-y-1 font-mono text-xs break-all">
          <div>span: {span.span_id}</div>
          <div>trace: {span.trace_id}</div>
          {span.parent_span_id && <div>parent: {span.parent_span_id}</div>}
        </div>
      </div>
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="font-mono text-sm mt-0.5 break-all">{value}</div>
    </div>
  );
}
