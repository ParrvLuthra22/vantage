const API_URL = process.env.VANTAGE_API_URL!;

/**
 * Liveness probe for the backend, used to tell "no traces yet" apart from
 * "the API is down" in empty states.
 *
 * `cache: "no-store"` because a cached health check is worthless — the whole
 * point is the state right now. Unlike the data endpoints this never throws;
 * a failed health check is a UI state, not an error boundary.
 */
export async function checkApiHealth(): Promise<{ ok: boolean; error?: string }> {
  try {
    const res = await fetch(`${API_URL}/health`, { cache: "no-store" });
    if (!res.ok) return { ok: false, error: `HTTP ${res.status}` };
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "Unknown error" };
  }
}
