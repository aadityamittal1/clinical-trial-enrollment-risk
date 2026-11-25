# syntax=docker/dockerfile:1
# Pipeline image: ingestion + dbt + ml + pipeline. Build from the repo root:
#   docker build -f deploy/docker/pipeline.Dockerfile -t trial-risk-pipeline:local .
FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# LightGBM needs the OpenMP runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app /data \
    && chown app:app /app /data

WORKDIR /app

# Dependencies first so this layer is cached until pyproject/uv.lock change.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --extra cloud

COPY --chown=app:app common/ ./common/
COPY --chown=app:app ingestion/ ./ingestion/
COPY --chown=app:app ml/ ./ml/
COPY --chown=app:app pipeline/ ./pipeline/
COPY --chown=app:app dbt/ ./dbt/
COPY --chown=app:app data/sample/ ./data/sample/
COPY --chmod=0755 deploy/docker/entrypoint.sh /usr/local/bin/entrypoint.sh

# dbt writes target/ and logs/ under the project dir.
RUN mkdir -p /app/dbt/target /app/dbt/logs /app/data/landing \
    && chown -R app:app /app/dbt /app/data /data

USER app
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "-m", "pipeline", "run", "--source", "sample"]
