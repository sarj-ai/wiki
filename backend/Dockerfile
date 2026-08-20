# syntax=docker/dockerfile:1

FROM python:3.14.6-alpine AS builder

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock uv.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

FROM python:3.14.6-alpine AS runtime

WORKDIR /app

ARG RELEASE_SHA=dev
ENV RELEASE_SHA=$RELEASE_SHA

RUN apk upgrade --no-cache \
    && adduser -D -u 1001 app

COPY --from=builder --chown=app:app /app/.venv .venv
COPY --from=builder --chown=app:app /app/app app
COPY --from=builder --chown=app:app /app/alembic alembic
COPY --from=builder --chown=app:app /app/alembic.ini .

USER app

EXPOSE 8000

CMD ["sh", "-c", ".venv/bin/alembic upgrade head && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000"]
