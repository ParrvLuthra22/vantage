"use server";

import { revalidatePath } from "next/cache";

import { setBaseline } from "@/lib/api/client";

export type SetBaselineState =
  | { status: "idle" }
  | { status: "success" }
  | { status: "error"; message: string };

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Mark a run as its suite's baseline. Meant to be bound to a run
 * (`setBaselineAction.bind(null, runId)`) and handed to `useActionState`,
 * which will also pass the previous state and FormData — neither is needed
 * here (the run ID is the only input, and each result stands alone), so
 * they're simply not declared.
 *
 * A Server Action is a public POST endpoint — the form isn't a security
 * boundary — so the ID is re-validated here rather than trusted because
 * "the UI only ever sends real ones". Errors are mapped to short messages
 * instead of returning the raw API error text, which can carry a response
 * body the browser has no business seeing.
 */
export async function setBaselineAction(runId: string): Promise<SetBaselineState> {
  if (!UUID_RE.test(runId)) {
    return { status: "error", message: "Invalid run ID." };
  }

  try {
    await setBaseline(runId);
  } catch (e) {
    if (e instanceof Error && /Vantage API 404/.test(e.message)) {
      return { status: "error", message: "Run not found." };
    }
    return { status: "error", message: "Couldn't set baseline — is the Vantage API reachable?" };
  }

  // Read-your-own-writes: both pages show the baseline badge, and
  // fetchApi's 5s revalidate window would otherwise serve the pre-mutation
  // copy for a few seconds after the click.
  revalidatePath(`/evals/runs/${runId}`);
  revalidatePath("/evals");
  return { status: "success" };
}
