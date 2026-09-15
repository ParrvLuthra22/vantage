import { notFound } from "next/navigation";

import { EvalRunDetailView } from "@/components/eval-run-detail-view";
import { getEvalRun } from "@/lib/api/client";

interface PageProps {
  params: Promise<{ run_id: string }>;
}

export default async function EvalRunPage({ params }: PageProps) {
  const { run_id } = await params;

  let run;
  try {
    run = await getEvalRun(run_id);
  } catch (e) {
    // Same 404/422 -> not-found mapping as app/traces/[trace_id]/page.tsx.
    if (e instanceof Error && /Vantage API (404|422)/.test(e.message)) notFound();
    throw e;
  }

  return <EvalRunDetailView run={run} />;
}
