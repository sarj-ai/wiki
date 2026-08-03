from __future__ import annotations

from collections.abc import AsyncIterator
import os
from pathlib import Path
import subprocess
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.database import Base
from app.identifiers import GoogleUserId
from app.models.user import User


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")  # ruff: ignore[banned-api] — integration-only opt-in env var

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if TEST_DATABASE_URL:
        return
    here = Path(__file__).resolve().parent
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL is not set; skipping Postgres integration")
    for item in items:
        if item.path.is_relative_to(here):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def migrated_database_url() -> str:
    """Run alembic migrations once per session against the test database.

    Runs in a subprocess because alembic/env.py imports the app settings
    singleton (frozen with the dummy DATABASE_URL from tests/conftest.py)
    and calls asyncio.run, neither of which tolerates in-process reuse.
    """
    assert TEST_DATABASE_URL is not None
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}  # ruff: ignore[banned-api] — subprocess env for alembic
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        check=True,
        cwd=_BACKEND_ROOT,
        env=env,
    )
    return TEST_DATABASE_URL


@pytest.fixture
async def pg_engine(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database_url)
    tables = ", ".join(table.name for table in reversed(Base.metadata.sorted_tables))
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {tables} CASCADE"))
    yield engine
    await engine.dispose()


@pytest.fixture
def pg_sessionmaker(pg_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(pg_engine, expire_on_commit=False)


@pytest.fixture
async def pg_session(
    pg_sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with pg_sessionmaker() as session:
        yield session


@pytest.fixture
async def owner_id(pg_session: AsyncSession) -> uuid.UUID:
    user = User()
    user.email = f"it-{uuid.uuid4().hex[:8]}@sarj.ai"
    user.google_id = GoogleUserId(f"gid-{uuid.uuid4().hex[:8]}")
    pg_session.add(user)
    await pg_session.commit()
    await pg_session.refresh(user)
    return user.id
