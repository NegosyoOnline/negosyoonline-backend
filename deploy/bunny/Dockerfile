# Bunny staging image. Build context: the repository root.
# One image per container role (api, worker, beat), all built from the same commit:
#   docker build -f deploy/bunny/Dockerfile --target api --build-arg APP_COMMIT_SHA=<sha> .
FROM python:3.12-slim AS backend
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /bin/uv
WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY manage.py gunicorn.conf.py ./
COPY apps/ apps/
COPY config/ config/
COPY deploy/bunny/ deploy/bunny/

# Baked in last. /healthz/ reports its first 12 characters (settings.release_commit).
ARG APP_COMMIT_SHA=unknown
ENV UV_NO_SYNC=1 \
    APP_COMMIT_SHA=${APP_COMMIT_SHA}
USER nobody

FROM backend AS worker
ENTRYPOINT ["sh", "/app/deploy/bunny/start-worker.sh"]

FROM backend AS beat
ENTRYPOINT ["sh", "/app/deploy/bunny/start-beat.sh"]

FROM backend AS api
# init-api.sh takes the migration lock, migrates, then execs start-api.sh (which never migrates).
ENTRYPOINT ["sh", "/app/deploy/bunny/init-api.sh"]
EXPOSE 8000
