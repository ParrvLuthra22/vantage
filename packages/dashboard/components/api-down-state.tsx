import { AlertCircle } from "lucide-react";

/**
 * Shown when the health probe fails, so an unreachable backend reads as an
 * infrastructure problem rather than "you have no traces". Surfacing the
 * configured URL turns the most common cause — pointing at the wrong port or a
 * stopped server — into something diagnosable without opening a terminal.
 *
 * Server component: process.env is read at render time and never ships to the
 * browser. The URL is not a secret; the API key is, and it is not referenced here.
 */
export function ApiDownState({ error }: { error?: string }) {
  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <div className="max-w-md rounded-md border border-destructive/40 bg-destructive/5 p-6">
        <div className="flex items-center gap-2 text-destructive">
          <AlertCircle className="w-4 h-4" />
          <span className="font-semibold">Vantage API unreachable</span>
        </div>
        <p className="text-sm text-muted-foreground mt-2">{error ?? "Unknown error"}</p>
        <p className="text-xs text-muted-foreground mt-4 font-mono">
          VANTAGE_API_URL={process.env.VANTAGE_API_URL}
        </p>
      </div>
    </div>
  );
}
