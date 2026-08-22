# Deploying the Vantage API

The API ships as a container. `Dockerfile` runs `alembic upgrade head` and then
starts uvicorn, so a deploy migrates before it serves.

## Build and run locally

```bash
cd packages/api
docker build -t vantage-api .
```

Running it takes one wrinkle. `.env` points `DATABASE_URL` at `localhost`, and
inside a container `localhost` is the *container*, not your machine — so
`docker run --env-file .env` starts and then fails to reach Postgres. Attach to
the compose network and address Postgres by service name instead:

```bash
docker run --rm -p 8000:8000 --network vantage_default \
  -e DATABASE_URL="postgresql+asyncpg://vantage:vantage@postgres:5432/vantage" \
  -e API_KEY="dev-key-change-me" \
  -e CORS_ORIGINS='["http://localhost:3000"]' \
  vantage-api
```

A clean start looks like this — migration first, then the server:

```
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO:     Started server process [1]
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

`Started server process [1]` is worth checking: uvicorn should be PID 1. If it
isn't, the container will not shut down gracefully (see *Signals*, below).

## Railway

Railway detects the Dockerfile and needs no build configuration.

```bash
cd packages/api
railway login
railway init          # create a project named "vantage-api"
railway up
```

### Required environment variables

Set these in the Railway dashboard under **Variables** — never in git.

| Variable | Value |
| --- | --- |
| `DATABASE_URL` | Your Neon URL, with the scheme changed to `postgresql+asyncpg://`. Keep `?sslmode=require`; `database.py` rewrites it to the `ssl=` form asyncpg understands. |
| `API_KEY` | A strong random key — `openssl rand -hex 32`. Never the dev default. |
| `CORS_ORIGINS` | JSON array, e.g. `["https://your-dashboard.vercel.app"]`. Must be valid JSON or pydantic-settings will fail to parse it at startup. |

`PORT` is injected by Railway; the Dockerfile defaults it to 8000 for local runs
and the `CMD` reads it, so nothing needs setting.

### Verify a deploy

```bash
curl https://<your-app>.up.railway.app/health
# {"status":"ok","version":"0.1.0"}

curl -i https://<your-app>.up.railway.app/traces/          # expect 401
curl -H "Authorization: Bearer $API_KEY" \
     "https://<your-app>.up.railway.app/traces/?project=vesper"
```

Use the trailing slash on `/traces/`. Without it the API answers 307 to the
canonical path, and following that redirect costs an extra round trip.

## Deploy-safety notes

**Never put the production `DATABASE_URL` in git.** It lives only in Railway's
variables. `alembic.ini` contains a local development URL as a fallback, but
`alembic/env.py` overrides it from `settings.database_url` at runtime, so
`DATABASE_URL` wins in every environment. Nothing in the repo should ever
contain a Neon connection string.

**Rotating `API_KEY` is a coordinated change, not a config edit.** The key is
read once at process start into a module-level settings singleton, so changing
it in Railway does nothing until the service redeploys. And every SDK client
using the old key starts getting 401s the moment it takes effect — with the
exporter swallowing HTTP errors by design, those clients will drop spans
silently rather than raise. Rotation therefore means: update the SDK clients
first or accept a gap, then change the variable, then redeploy. There is no
support for two valid keys at once, which is what would make this a zero-gap
operation; that is a real limitation of single-key auth.

**A failed migration crashloops the service.** The `CMD` chains
`alembic upgrade head && uvicorn`, so a migration error means uvicorn never
starts, the container exits non-zero, and Railway restarts it — repeatedly.
The symptom is a service that never becomes healthy; the cause is in the deploy
logs, above the restart, as an alembic traceback. Note the old version is
already gone by then, so a bad migration is an outage rather than a failed
deploy that rolls back. Check migrations against a Neon branch before shipping.

**Signals.** The `CMD` uses `exec` so uvicorn becomes PID 1 and receives
SIGTERM directly. Without it the shell holds PID 1, and on redeploy the shell
takes the signal and exits — uvicorn is torn down without ever running its
shutdown sequence, cutting in-flight requests and skipping the lifespan hook
that disposes the connection pool. Verified by diff: the non-exec build logs no
shutdown lines at all, the exec build logs the full drain.

**The container runs as uid 10001, not root.** Nothing here writes to disk. If
a future change needs a writable path, create it and `chown` it in the build
rather than reverting to root.

## Cost

Railway's free tier includes $5/month of credit; this service is small enough to
sit near $2/month at low traffic. Neon's free tier is sized for a project like
this — the compute suspends when idle, which is most of the time for a portfolio
deployment. Both are usage-metered, so a runaway ingest loop is the thing that
would change the bill, not steady-state traffic.
