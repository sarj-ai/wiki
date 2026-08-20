import asyncio
from collections.abc import AsyncGenerator
from typing import Any, ClassVar

from google.cloud.sql.connector import Connector, IPTypes, RefreshStrategy
from sqlalchemy import Text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.database_config import database_settings
from app.identifiers import DriveId, FileId, GoogleUserId


_STATEMENT_TIMEOUT_MS = "120000"

_POOL_KWARGS = {
    "echo": False,
    "pool_pre_ping": True,
    "pool_recycle": 180,
    "pool_size": 5,
    "max_overflow": 5,
    "pool_timeout": 30,
}


def build_engine() -> AsyncEngine:
    if database_settings.DATABASE_URL is not None:
        return create_async_engine(
            str(database_settings.DATABASE_URL),
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
    global _connector
    if _connector is not None:
        await _connector.close_async()
        _connector = None


_connector: Connector | None = None


async def _connect_via_connector() -> Any:
    instance = database_settings.DB_INSTANCE_CONNECTION_NAME
    iam_user = database_settings.DB_IAM_USER
    if instance is None or iam_user is None:  # pragma: no cover — config validator forbids this
        msg = "Connector path requires DB_INSTANCE_CONNECTION_NAME and DB_IAM_USER"
        raise RuntimeError(msg)
    global _connector
    connector = _connector
    if connector is None:
        connector = Connector(
            refresh_strategy=RefreshStrategy.LAZY,
            loop=asyncio.get_running_loop(),
        )
        _connector = connector
    return await connector.connect_async(
        instance,
        "asyncpg",
        user=iam_user,
        db=database_settings.DB_NAME,
        enable_iam_auth=True,
        ip_type=IPTypes.PRIVATE,
        server_settings={"statement_timeout": _STATEMENT_TIMEOUT_MS},
    )


engine = build_engine()
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        FileId: Text(),
        DriveId: Text(),
        GoogleUserId: Text(),
    }


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with async_session() as session:
        yield session
