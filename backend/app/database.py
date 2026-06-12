import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from google.cloud.sql.connector import Connector, IPTypes, RefreshStrategy
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


_STATEMENT_TIMEOUT_MS = "120000"  # 120s per statement

_POOL_KWARGS = {
    "echo": False,
    "pool_pre_ping": True,  # detect dead connections before using them
    "pool_recycle": 180,  # recycle every 3 min (Cloud SQL may drop idle sooner)
    "pool_size": 5,  # max persistent connections
    "max_overflow": 5,  # allow 5 more under load
    "pool_timeout": 30,  # wait max 30s for a connection from the pool
}


def build_engine() -> AsyncEngine:
    """One engine factory for both the app and Alembic.

    DSN path (local dev / CI): plain asyncpg with password auth.
    Connector path (production): Cloud SQL Python Connector with IAM
    database authentication — mTLS to the instance's private IP and a
    short-lived IAM token instead of a password.
    """
    if settings.DATABASE_URL is not None:
        return create_async_engine(
            str(settings.DATABASE_URL),
            connect_args={
                "server_settings": {"statement_timeout": _STATEMENT_TIMEOUT_MS},
            },
            **_POOL_KWARGS,
        )
    return create_async_engine(
        "postgresql+asyncpg://",
        async_creator=_connect_via_connector,
        **_POOL_KWARGS,
    )


async def close_connector() -> None:
    """Lifespan-shutdown hook; no-op when the DSN path is in use."""
    global _connector
    if _connector is not None:
        await _connector.close_async()
        _connector = None


_connector: Connector | None = None


async def _connect_via_connector() -> Any:
    instance = settings.DB_INSTANCE_CONNECTION_NAME
    iam_user = settings.DB_IAM_USER
    if instance is None or iam_user is None:  # pragma: no cover — config validator forbids this
        raise RuntimeError("Connector path requires DB_INSTANCE_CONNECTION_NAME and DB_IAM_USER")
    global _connector
    connector = _connector
    if connector is None:
        # Lazy refresh is the documented mode for Cloud Run's
        # CPU-throttled execution model. The loop kwarg is required for
        # connect_async: a bare Connector() binds to a private
        # background-thread loop and raises ConnectorLoopError when
        # called from the app's loop.
        connector = Connector(
            refresh_strategy=RefreshStrategy.LAZY,
            loop=asyncio.get_running_loop(),
        )
        _connector = connector
    return await connector.connect_async(
        instance,
        "asyncpg",
        user=iam_user,
        db=settings.DB_NAME,
        enable_iam_auth=True,
        ip_type=IPTypes.PRIVATE,
        # connect_args are ignored when async_creator is set, so the
        # statement timeout rides along with the driver kwargs here.
        server_settings={"statement_timeout": _STATEMENT_TIMEOUT_MS},
    )


engine = build_engine()
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with async_session() as session:
        yield session
