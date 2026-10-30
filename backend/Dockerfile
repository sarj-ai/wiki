# syntax=docker/dockerfile:1.26@sha256:ecfaec9ed6d810b56388c508f4121597bfbba70d41a6dfeee4d8cad5f295fc32

FROM python:3.14.8-alpine@sha256:f6a589d43c42b9e7f7dc67a12d37132491f362859a5d750607710cc56da3bc72 AS builder

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uv /uvx /usr/local/bin/

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

FROM python:3.14.8-alpine@sha256:f6a589d43c42b9e7f7dc67a12d37132491f362859a5d750607710cc56da3bc72 AS runtime

WORKDIR /app

ARG RELEASE_SHA=dev
ENV RELEASE_SHA=$RELEASE_SHA

RUN --mount=type=bind,source=scripts/prepare-runtime.sh,target=/tmp/prepare-runtime.sh \
    /bin/sh /tmp/prepare-runtime.sh

COPY --from=builder --chown=app:app /app/.venv .venv
COPY --from=builder --chown=app:app /app/app app
COPY --from=builder --chown=app:app /app/alembic alembic
COPY --from=builder --chown=app:app /app/alembic.ini .

USER 1001:1001

EXPOSE 8000

CMD [".venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
