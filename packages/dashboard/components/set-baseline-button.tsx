"use client";

import { useActionState } from "react";

import { Button } from "@/components/ui/button";
import { setBaselineAction, type SetBaselineState } from "@/lib/actions/evals";

const INITIAL_STATE: SetBaselineState = { status: "idle" };

/** The only interactive piece of the run detail page — a plain `<form>` posting
 * to a Server Action needs no client component by itself; this exists purely
 * for `useActionState`'s pending flag and inline error, so the surrounding
 * EvalRunDetailView can stay a server component. */
export function SetBaselineButton({ runId }: { runId: string }) {
  const [state, formAction, pending] = useActionState(
    setBaselineAction.bind(null, runId),
    INITIAL_STATE,
  );

  return (
    <form action={formAction} className="flex flex-col items-end gap-1">
      <Button type="submit" variant="outline" size="sm" disabled={pending}>
        {pending ? "Setting baseline…" : "Set as baseline"}
      </Button>
      {state.status === "error" && (
        <p role="alert" className="text-xs text-destructive">
          {state.message}
        </p>
      )}
    </form>
  );
}
