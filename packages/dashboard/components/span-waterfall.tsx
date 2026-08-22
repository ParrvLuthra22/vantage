"use client";

import { useMemo, useState } from "react";

import { Span } from "@/lib/api/types";
import { formatDuration } from "@/lib/utils/format";

/**
 * Span waterfall.
 *
 * Why hand-built SVG rather than Canvas?
 *   SVG elements are real DOM: inspectable in devtools, stylable with CSS, and
 *   reachable by ARIA later. Canvas wins past ~10k elements, and a trace with
 *   ten thousand spans is a different product problem than the one this solves.
 *
 * Why hand-built rather than Recharts?
 *   Chart libraries are built around series of x/y points — line, bar, pie. A
 *   waterfall is nested rows with per-row time offsets and an indentation axis,
 *   which is not a shape they model. Bending one into this costs more code than
 *   emitting the rects directly, and leaves the layout logic buried in config.
 *
 * Why depth-first ordering rather than chronological?
 *   Sorting every span by start time flattens the tree: a child would sit next
 *   to an unrelated sibling of its parent and the nesting would be unreadable.
 *   Depth-first keeps each subtree contiguous, so indentation actually
 *   corresponds to ancestry. Siblings are still sorted by start time, so time
 *   order holds wherever it does not fight the hierarchy.
 */

interface WaterfallProps {
  spans: Span[];
  traceStart: string;
  traceEnd: string | null;
  selectedId: string | null;
  onSelect: (spanId: string | null) => void;
}

interface LayoutRow {
  span: Span;
  depth: number;
  startMs: number;
  durationMs: number;
}

function buildLayout(spans: Span[], traceStartMs: number): LayoutRow[] {
  const childrenOf = new Map<string | null, Span[]>();
  for (const s of spans) {
    const key = s.parent_span_id;
    const list = childrenOf.get(key) ?? [];
    list.push(s);
    childrenOf.set(key, list);
  }
  for (const list of childrenOf.values()) {
    list.sort(
      (a, b) => new Date(a.start_time).getTime() - new Date(b.start_time).getTime(),
    );
  }

  const rows: LayoutRow[] = [];
  const seen = new Set<string>();

  const push = (s: Span, depth: number) => {
    const startMs = new Date(s.start_time).getTime() - traceStartMs;
    const endMs = s.end_time
      ? new Date(s.end_time).getTime() - traceStartMs
      : startMs;
    rows.push({ span: s, depth, startMs, durationMs: endMs - startMs });
    seen.add(s.span_id);
  };

  function visit(parentId: string | null, depth: number) {
    for (const s of childrenOf.get(parentId) ?? []) {
      if (seen.has(s.span_id)) continue; // cycle guard
      push(s, depth);
      visit(s.span_id, depth + 1);
    }
  }
  visit(null, 0);

  // Orphans: spans whose parent_span_id references a span that isn't here.
  // parent_span_id carries no foreign key precisely because spans can arrive
  // out of order, so a parent may still be in flight or may have been dropped
  // by the exporter. A tree walk from the roots would silently omit them —
  // unacceptable in a debugging tool, where the missing span is often the one
  // you came to look at. Surface them at the root instead, in time order.
  const orphans = spans
    .filter((s) => !seen.has(s.span_id))
    .sort(
      (a, b) => new Date(a.start_time).getTime() - new Date(b.start_time).getTime(),
    );
  for (const s of orphans) push(s, 0);

  return rows;
}

export function SpanWaterfall({
  spans,
  traceStart,
  traceEnd,
  selectedId,
  onSelect,
}: WaterfallProps) {
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  const traceStartMs = new Date(traceStart).getTime();

  const rows = useMemo(() => buildLayout(spans, traceStartMs), [spans, traceStartMs]);

  // A trace still in flight has no end_time, and falling back to traceStart
  // would make the span of time being drawn 1ms wide — every bar would shoot
  // hundreds of times past the right edge. Fall back to the furthest point any
  // span reaches so an open trace still renders to scale.
  const traceDurationMs = useMemo(() => {
    const explicit = traceEnd ? new Date(traceEnd).getTime() - traceStartMs : 0;
    const furthestSpan = rows.reduce(
      (max, r) => Math.max(max, r.startMs + r.durationMs),
      0,
    );
    return Math.max(1, explicit, furthestSpan);
  }, [traceEnd, traceStartMs, rows]);

  const ROW_HEIGHT = 32;
  const NAME_COL_WIDTH = 260;
  const WATERFALL_WIDTH = 640;
  // SVG clips to its viewport, so the time axis cannot end at the SVG edge:
  // the duration label of any bar reaching 100% would be drawn past it and
  // vanish — which is every root span, since a root spans the whole trace by
  // definition. The final axis tick is centred on that same x and would lose
  // its right half. This gutter is the room those two need.
  const LABEL_GUTTER = 72;
  const TOTAL_WIDTH = NAME_COL_WIDTH + WATERFALL_WIDTH + LABEL_GUTTER;
  const HEIGHT = rows.length * ROW_HEIGHT + 32; // header space

  const timeToX = (ms: number) =>
    NAME_COL_WIDTH + (ms / traceDurationMs) * WATERFALL_WIDTH;

  const ticks = [0, 0.25, 0.5, 0.75, 1];

  return (
    <div className="rounded-md border border-border p-4 overflow-x-auto">
      <svg width={TOTAL_WIDTH} height={HEIGHT} className="font-mono text-xs">
        {ticks.map((frac) => (
          <line
            key={frac}
            x1={NAME_COL_WIDTH + frac * WATERFALL_WIDTH}
            y1={16}
            x2={NAME_COL_WIDTH + frac * WATERFALL_WIDTH}
            y2={HEIGHT}
            stroke="hsl(0 0% 14%)"
            strokeDasharray="2 2"
          />
        ))}
        {ticks.map((frac) => (
          <text
            key={frac}
            x={NAME_COL_WIDTH + frac * WATERFALL_WIDTH}
            y={12}
            textAnchor="middle"
            fill="hsl(0 0% 60%)"
          >
            {formatDuration(frac * traceDurationMs)}
          </text>
        ))}

        {rows.map((row, i) => {
          const y = 32 + i * ROW_HEIGHT;
          const barX = timeToX(row.startMs);
          const barW = Math.max(
            2,
            (row.durationMs / traceDurationMs) * WATERFALL_WIDTH,
          );
          const isError = row.span.status === "error";
          const isHovered = hoveredId === row.span.span_id;
          const isSelected = selectedId === row.span.span_id;

          return (
            <g
              key={row.span.span_id}
              onMouseEnter={() => setHoveredId(row.span.span_id)}
              onMouseLeave={() => setHoveredId(null)}
              // Clicking the selected row again clears it, so the panel can be
              // dismissed without hunting for a close control.
              onClick={() => onSelect(isSelected ? null : row.span.span_id)}
              className="cursor-pointer"
              role="button"
              tabIndex={0}
              aria-pressed={isSelected}
              aria-label={`Span ${row.span.name}`}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelect(isSelected ? null : row.span.span_id);
                }
              }}
            >
              <rect
                x={0}
                y={y - 12}
                width={TOTAL_WIDTH}
                height={ROW_HEIGHT}
                fill={
                  isSelected
                    ? "hsl(0 0% 12%)"
                    : isHovered
                      ? "hsl(0 0% 8%)"
                      : "transparent"
                }
              />
              <text x={8 + row.depth * 16} y={y + 4} fill="hsl(0 0% 90%)">
                {row.span.name.length > 32
                  ? row.span.name.slice(0, 30) + "…"
                  : row.span.name}
              </text>
              <rect
                x={barX}
                y={y - 6}
                width={barW}
                height={20}
                rx={2}
                fill={
                  isError
                    ? isSelected
                      ? "hsl(0 84% 60% / 1)"
                      : "hsl(0 84% 60% / 0.8)"
                    : isSelected
                      ? "hsl(24 100% 55% / 1)"
                      : "hsl(24 100% 55% / 0.7)"
                }
                stroke={isError ? "hsl(0 84% 60%)" : "hsl(24 100% 55%)"}
                strokeWidth={isSelected ? 2 : 1}
              />
              {barW > 40 && (
                <text x={barX + barW + 8} y={y + 4} fill="hsl(0 0% 60%)">
                  {formatDuration(row.durationMs)}
                </text>
              )}
            </g>
          );
        })}
      </svg>

    </div>
  );
}
