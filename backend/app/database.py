import asyncio
from collections.abc import AsyncGenerator
from functools import partial
import time
from typing import Any, ClassVar, Protocol, assert_never

from google.cloud.sql.connector import Connector, IPTypes, RefreshStrategy
from sqlalchemy import Text, event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.database_config import (
    CloudSqlDatabaseConnection,
    DirectDatabaseConnection,
    database_settings,
)
from app.identifiers import DriveId, FileId, GoogleUserId
from app.performance import add_duration, increment


_STATEMENT_TIMEOUT_MS = "120000"

_POOL_KWARGS = {
    "echo": False,
    "pool_pre_ping": True,
    "pool_recycle": 180,
    "pool_size": 4,
    "max_overflow": 1,
    "pool_timeout": 30,
}


def build_engine() -> AsyncEngine:
    match connection := database_settings.connection:
        case DirectDatabaseConnection(url):
            return create_async_engine(
                str(url),
                connect_args={
                    "server_settings": {"statement_timeout": _STATEMENT_TIMEOUT_MS},
                },
                **_POOL_KWARGS,
            )
        case CloudSqlDatabaseConnection():
            return create_async_engine(
                "postgresql+asyncpg://",
                async_creator=partial(_connect_via_connector, connection),
                **_POOL_KWARGS,
            )
        case _:
            assert_never(connection)


async def close_connector() -> None:
    global _connector
    if _connector is not None:
        await _connector.close_async()
        _connector = None


_connector: Connector | None = None


async def _connect_via_connector(connection: CloudSqlDatabaseConnection) -> Any:
    global _connector
    connector = _connector
    if connector is None:
        connector = Connector(
            refresh_strategy=RefreshStrategy.LAZY,
            loop=asyncio.get_running_loop(),
        )
        _connector = connector
    return await connector.connect_async(
        connection.instance,
        "asyncpg",
        user=connection.iam_user,
        db=connection.database,
        enable_iam_auth=True,
        ip_type=IPTypes.PRIVATE,
        server_settings={"statement_timeout": _STATEMENT_TIMEOUT_MS},
    )


engine = build_engine()
async_session = async_sessionmaker(engine, expire_on_commit=False)


class _TimedExecutionContext(Protocol):
    wiki_query_started_at: float | None


@event.listens_for(engine.sync_engine, "before_cursor_execute")
def _before_cursor_execute(
    _conn: Any,
    _cursor: Any,
    _statement: str,
    _parameters: Any,
    context: _TimedExecutionContext,
    _executemany: Any,
) -> None:
    context.wiki_query_started_at = time.perf_counter()


@event.listens_for(engine.sync_engine, "after_cursor_execute")
def _after_cursor_execute(
    _conn: Any,
    _cursor: Any,
    _statement: str,
    _parameters: Any,
    context: _TimedExecutionContext,
    _executemany: Any,
) -> None:
    try:
        started_at = context.wiki_query_started_at
    except AttributeError:
        started_at = None
    if isinstance(started_at, float):
        add_duration("db", (time.perf_counter() - started_at) * 1000)
    increment("db_query_count")


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        FileId: Text(),
        DriveId: Text(),
        GoogleUserId: Text(),
    }


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with async_session() as session:
        yield session
