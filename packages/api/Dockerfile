FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
# pyproject declares `readme = "README.md"`, and hatchling reads it while
# generating metadata — without this the build dies on
# "OSError: Readme file does not exist: README.md".
COPY README.md ./
COPY vantage_api ./vantage_api
COPY alembic ./alembic
COPY alembic.ini ./

RUN pip install --upgrade pip && pip install .

# Drop privileges. Nothing here writes to disk, so the app has no reason to run
# as root, and a container escape from an unprivileged uid is a smaller problem.
RUN useradd --create-home --uid 10001 vantage
USER vantage

# Railway sets PORT env var; default to 8000 for local Docker runs
ENV PORT=8000

EXPOSE 8000

# Migrate, then serve. The `exec` is load-bearing, and the difference is
# measurable: without it PID 1 is `/bin/sh -c ...` with uvicorn as a child, and
# on `docker stop` the shell takes the SIGTERM and exits, tearing the container
# down with uvicorn never notified — its logs end at "Uvicorn running", with no
# shutdown sequence at all. So in-flight requests are cut mid-response and the
# lifespan shutdown hook never runs, leaving the connection pool undisposed.
# With exec, uvicorn is PID 1 and logs the full "Shutting down / Waiting for
# application shutdown / Application shutdown complete" drain. Note the
# container stops quickly either way — speed is not the signal here, the
# shutdown log lines are.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn vantage_api.main:app --host 0.0.0.0 --port ${PORT}"]
