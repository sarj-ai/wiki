FROM python:3.14-slim AS base

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install dependencies (cached layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code and install the project
COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini .
RUN uv sync --frozen --no-dev

EXPOSE 8000

# Run migrations then start the server
CMD ["sh", "-c", ".venv/bin/alembic upgrade head && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000"]
