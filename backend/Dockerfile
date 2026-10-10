# syntax=docker/dockerfile:1.26@sha256:ecfaec9ed6d810b56388c508f4121597bfbba70d41a6dfeee4d8cad5f295fc32

FROM python:3.14.7-alpine@sha256:05b2b8b732ecd268fee8727a369f936f022d1321b59befd13c30ede22769dcdc AS builder

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.12.24@sha256:3af4716e991d6956a41e573eab705d0ee08500cd829ed30293eb8472f372c65a /uv /uvx /usr/local/bin/

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

FROM python:3.14.7-alpine@sha256:05b2b8b732ecd268fee8727a369f936f022d1321b59befd13c30ede22769dcdc AS runtime

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
