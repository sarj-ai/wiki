# syntax=docker/dockerfile:1

FROM python:3.14-slim-bookworm AS builder

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.20 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM python:3.14-slim-bookworm AS runtime

WORKDIR /app

RUN apt-get update \
    && apt-get upgrade -y \
    && useradd --create-home --uid 1001 app \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder --chown=app:app /app/.venv .venv
COPY --from=builder --chown=app:app /app/app app
COPY --from=builder --chown=app:app /app/alembic alembic
COPY --from=builder --chown=app:app /app/alembic.ini .

USER app

EXPOSE 8000

CMD ["sh", "-c", ".venv/bin/alembic upgrade head && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000"]
