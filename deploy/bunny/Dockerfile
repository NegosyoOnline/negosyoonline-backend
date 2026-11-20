# Use the NegosyoOnline backend checkout as the build context.
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
COPY scripts/start_worker.sh scripts/start_beat.sh scripts/

ENV UV_NO_SYNC=1
USER nobody

FROM backend AS worker
CMD ["celery", "-A", "config", "worker", "--loglevel=info", "--concurrency=2"]

FROM backend AS beat
CMD ["celery", "-A", "config", "beat", "--loglevel=info", "--schedule=/tmp/celerybeat-schedule"]

FROM backend AS api
COPY deploy/bunny/start-api.sh /app/start-api.sh
ENTRYPOINT ["sh", "/app/start-api.sh"]
EXPOSE 8000
CMD ["gunicorn", "config.asgi:application", "-k", "uvicorn_worker.UvicornWorker", "--bind", "0.0.0.0:8000", "--access-logfile", "-", "--error-logfile", "-"]
