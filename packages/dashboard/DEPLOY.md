# Deploying the Vantage dashboard

Next.js app deployed to Vercel. It renders on the server and calls the Vantage
API with a key that never reaches the browser, so the deployment has real
secrets and a hard dependency on the API being reachable.

## Prerequisite: the API must be deployed first

The dashboard has no data source of its own. Deploy the API (see
`packages/api/DEPLOY.md`) and have these to hand before starting:

- the API's public URL
- the production `API_KEY` set in Railway

## Verify the build locally

```bash
cd packages/dashboard
npm run build
```

Expect a clean run ending in the route table:

```
┌ ○ /
├ ○ /_not-found
├ ○ /icon.svg
├ ƒ /traces
└ ƒ /traces/[trace_id]
```

## Set environment variables BEFORE the first deploy

This ordering is not optional. `lib/api/client.ts` validates its configuration
at module load, and Next imports every route module during the "Collecting page
data" phase of a build — so a build without these variables does not warn, it
fails:

```
Error: Failed to collect page data for /traces/[trace_id]
  [cause]: Error: VANTAGE_API_URL and VANTAGE_API_KEY must be set
```

That fail-fast is deliberate — a dashboard that builds fine and 500s on every
request would be worse — but it means running bare `vercel` before setting
variables produces a failed deploy rather than the preview URL you expected.

Link the project without deploying, set the variables, then deploy:

```bash
cd packages/dashboard
vercel link                       # create/link "vantage-dashboard", no build

vercel env add VANTAGE_API_URL production
vercel env add VANTAGE_API_KEY production

# Preview builds run the same code path, so they need the variables too or
# every PR preview fails to build. Development is only needed for `vercel dev`.
vercel env add VANTAGE_API_URL preview
vercel env add VANTAGE_API_KEY preview

vercel --prod
```

| Variable | Value | Notes |
| --- | --- | --- |
| `VANTAGE_API_URL` | `https://<your-api-host>` | No trailing slash — the client appends paths directly. |
| `VANTAGE_API_KEY` | the production key from Railway | Deliberately **not** `NEXT_PUBLIC_`-prefixed, which is what stops Next inlining it into client JS. Never rename it with that prefix. |

### Monorepo root directory

Running `vercel` from `packages/dashboard` sets the root correctly. If you
instead connect the GitHub repo through Vercel's dashboard, set **Root
Directory** to `packages/dashboard` — otherwise Vercel builds from the repo root,
finds no Next app, and fails.

## Point CORS back at the deployed dashboard

Once you have the Vercel URL, update the API's `CORS_ORIGINS` in Railway:

```json
["https://vantage-dashboard.vercel.app", "http://localhost:3000"]
```

It must be valid JSON — pydantic-settings parses it as a JSON array and a bare
string will fail at API startup. Railway redeploys on save.

Worth knowing: the dashboard's own pages do **not** need CORS. They fetch from
server components, server-to-server, which never triggers a preflight. CORS
matters for browser-side callers of the API — a future client-side fetch, or
anyone hitting the API from their own page. Getting `CORS_ORIGINS` wrong will
therefore not break the dashboard, which makes it easy to leave broken.

## Custom domain

Vercel → Project → Domains → add the hostname, then create the CNAME it shows
you at your DNS provider. Propagation is usually minutes. Add the new hostname
to `CORS_ORIGINS` as well.

## End-to-end check

```bash
# 1. dashboard loads and lists real traces
open https://<your-dashboard-host>/traces

# 2. inject a trace against production
export VANTAGE_BASE_URL=https://<your-api-host>
export VANTAGE_API_KEY=<prod-key>
python examples/vesper_integration/main.py

# 3. wait ~10s for the exporter's flush interval, then refresh
```

Ten seconds is the right wait: the SDK batches by size *and* time, and with a
five-second flush interval sixteen spans are well under the size threshold, so
they leave on the timer.

If the dashboard shows "Vantage API unreachable", the card prints the
`VANTAGE_API_URL` it tried — check that first, then the API's own `/health`.

## Cost

Vercel's free tier covers a project this size. The dashboard is server-rendered
on every request for `/traces` and `/traces/[trace_id]`, so traffic maps to
function invocations rather than static bandwidth; the 5-second `revalidate` on
the API fetch keeps repeat navigations off the API but does not reduce Vercel
invocations. Nothing here approaches free-tier limits at portfolio traffic.
