import { Trace, TraceDetail } from "./types";

/**
 * Server-side API client for the Vantage backend.
 *
 * CRITICAL: this must only be called from server components, route handlers,
 * or server actions. The API key must never reach the browser.
 *
 * Why fetch from server components rather than the client?
 *   - Secrets stay server-side. A client-side fetch would need the key in the
 *     browser bundle, where "hidden" means nothing — anyone can read it out of
 *     the JS or watch the request headers. `VANTAGE_API_KEY` has no NEXT_PUBLIC_
 *     prefix precisely so Next refuses to inline it into client code.
 *   - No CORS. Server-to-server requests never hit a preflight, so the backend
 *     does not have to whitelist the dashboard's origin to serve it data.
 *   - The browser gets HTML with the data already in it: one round trip instead
 *     of load-JS-then-fetch, so first paint shows real content rather than a
 *     spinner.
 */

const API_URL = process.env.VANTAGE_API_URL;
const API_KEY = process.env.VANTAGE_API_KEY;

if (!API_URL || !API_KEY) {
  throw new Error("VANTAGE_API_URL and VANTAGE_API_KEY must be set");
}

async function fetchApi<T>(path: string, options: RequestInit = {}): Promise<T> {
  const url = `${API_URL}${path}`;
  const res = await fetch(url, {
    ...options,
    headers: {
      Authorization: `Bearer ${API_KEY}`,
      "Content-Type": "application/json",
      ...options.headers,
    },
    // Traces are stale-tolerant: a few seconds behind is fine for a list of
    // runs that already happened. A 5s window means clicking into a trace and
    // back doesn't re-hit the API, while a genuine revisit still shows fresh
    // data. Long enough to absorb navigation bursts, short enough that nobody
    // reaches for refresh.
    next: { revalidate: 5 },
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Vantage API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export async function listTraces(project = "vesper", limit = 50): Promise<Trace[]> {
  // Trailing slash is deliberate: the backend mounts this route as `/traces/`,
  // and `/traces?...` answers with a 307 to the canonical path. Following that
  // costs an extra round trip on every list request.
  return fetchApi<Trace[]>(
    `/traces/?project=${encodeURIComponent(project)}&limit=${limit}`,
  );
}

export async function getTrace(traceId: string): Promise<TraceDetail> {
  return fetchApi<TraceDetail>(`/traces/${encodeURIComponent(traceId)}`);
}
