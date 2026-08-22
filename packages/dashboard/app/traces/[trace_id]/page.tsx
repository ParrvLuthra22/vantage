import { notFound } from "next/navigation";

import { TraceDetailView } from "@/components/trace-detail-view";
import { getTrace } from "@/lib/api/client";

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

  return <TraceDetailView trace={trace} />;
}
