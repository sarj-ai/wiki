import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from cryptography.fernet import Fernet
from pydantic_settings import BaseSettings, SettingsConfigDict
import pytest
from sqlalchemy import Table, func, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.mcp.protection import OAuthStateProtector
from app.mcp.storage import PostgresOAuthStorage
from app.models.mcp_oauth import MCPOAuthEntry


_COLLECTION = "mcp-authorization-codes"


class _Session:
    calls = 0

    async def execute(self, *_args: Any, **_kwargs: Any) -> None:
        self.calls += 1


class _Sessions:
    def __init__(self) -> None:
        self.calls = 0
        self.session = _Session()

    @asynccontextmanager
    async def begin(self) -> AsyncGenerator[_Session]:
        self.calls += 1
        yield self.session

    def factory(self) -> Any:
        return self


def _protector() -> OAuthStateProtector:
    return OAuthStateProtector(
        (Fernet.generate_key().decode(),), lock_key=Fernet.generate_key().decode()
    )


@pytest.mark.parametrize(
    ("key", "collection", "ttl"),
    [
        ("", _COLLECTION, 1),
        ("x" * 8193, _COLLECTION, 1),
        ("key", None, 1),
        ("key", "arbitrary", 1),
        ("key", _COLLECTION, 0),
        ("key", _COLLECTION, float("nan")),
        ("key", _COLLECTION, float("inf")),
        ("key", _COLLECTION, True),
        ("key", _COLLECTION, 40_000_000),
    ],
)
async def test_invalid_input_never_opens_database(
    key: str, collection: str | None, ttl: float
) -> None:
    sessions = _Sessions()
    store = PostgresOAuthStorage(sessions.factory(), _protector())
    with pytest.raises(ValueError, match="OAuth"):
        await store.put(key, {"value": 1}, collection=collection, ttl=ttl)
    assert sessions.calls == 0


async def test_bulk_validates_all_payloads_before_write() -> None:
    sessions = _Sessions()
    store = PostgresOAuthStorage(sessions.factory(), _protector())
    with pytest.raises(ValueError, match="OAuth state"):
        await store.put_many(
            ["good", "bad"], [{"x": 1}, {"x": float("nan")}], collection=_COLLECTION
        )
    with pytest.raises(ValueError, match="duplicate"):
        await store.put_many(["same", "same"], [{}, {}], collection=_COLLECTION)
    with pytest.raises(ValueError, match="limit"):
        await store.get_many(["x"] * 101, collection=_COLLECTION)
    assert sessions.calls == 0


async def test_operation_reuses_session_and_rejects_cross_task_and_nested_identity() -> None:
    sessions = _Sessions()
    session: Any = sessions.session
    store = PostgresOAuthStorage(sessions.factory(), _protector())
    async with store.operation("grant:one") as outer, store.operation("grant:one") as inner:
        assert outer is inner is session
        with pytest.raises(RuntimeError, match="same identity"):
            async with store.operation("grant:two"):
                pytest.fail("different nested lock accepted")
        with pytest.raises(RuntimeError, match="child tasks"):
            await asyncio.create_task(store.get("code", collection=_COLLECTION))

        async def child_operation() -> None:
            async with store.operation("grant:one"):
                pytest.fail("child task inherited the operation")

        with pytest.raises(RuntimeError, match="child tasks"):
            await asyncio.create_task(child_operation())
    assert sessions.session.calls == 3
    assert sessions.calls == 1
    async with store.operation("grant:two") as later:
        assert later is session
    assert sessions.calls == 2


class _QASettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WIKI_MCP_QA_")
    database_url: str | None = None


async def test_real_postgres_two_instance_single_consumption_and_rollback() -> None:
    dsn = _QASettings().database_url
    if not dsn:
        pytest.skip("Requires an explicitly configured disposable MCP PostgreSQL QA database")
    url = make_url(dsn)
    if (
        url.host not in {"localhost", "127.0.0.1", "::1"}
        or not (
            (url.database or "").startswith("wiki_mcp_qa") or url.database == "wiki_polish29_qa"
        )
        or url.drivername != "postgresql+asyncpg"
    ):
        pytest.fail("Only loopback PostgreSQL named wiki_mcp_qa* or wiki_polish29_qa is allowed")
    engine = create_async_engine(dsn)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    protector = _protector()
    stores = [PostgresOAuthStorage(sessions, protector) for _ in range(2)]
    table = MCPOAuthEntry.__table__
    assert isinstance(table, Table)
    async with engine.begin() as connection:
        await connection.run_sync(table.create, checkfirst=True)
    key = Fernet.generate_key().decode()
    await stores[0].put(key, {"code": "private"}, collection=_COLLECTION, ttl=60)

    async def consume(store: PostgresOAuthStorage) -> bool:
        async with store.operation(f"code:{key}"):
            value = await store.get(key, collection=_COLLECTION)
            if value is None:
                return False
            assert await store.delete(key, collection=_COLLECTION)
            return True

    async def rollback() -> None:
        async with stores[0].operation(f"code:{key}"):
            await stores[0].put(key, {"x": 2}, collection=_COLLECTION)
            msg = "rollback"
            raise RuntimeError(msg)

    try:
        assert sorted(await asyncio.gather(*(consume(store) for store in stores))) == [False, True]
        with pytest.raises(RuntimeError, match="rollback"):
            await rollback()
        assert await stores[1].get(key, collection=_COLLECTION) is None
        await stores[0].put(key, {"credential": "private-value"}, collection=_COLLECTION, ttl=60)
        value, remaining = await stores[1].ttl(key, collection=_COLLECTION)
        assert value == {"credential": "private-value"}
        assert remaining is not None
        assert 0 < remaining <= 60
        async with sessions.begin() as session:
            row = (
                await session.execute(
                    select(MCPOAuthEntry).where(
                        MCPOAuthEntry.collection == _COLLECTION,
                        MCPOAuthEntry.key_digest == protector.physical_keys(_COLLECTION, key)[0],
                    )
                )
            ).scalar_one()
            assert key not in row.key_digest
            assert "private-value" not in row.ciphertext
            await session.execute(
                update(MCPOAuthEntry)
                .where(
                    MCPOAuthEntry.collection == _COLLECTION,
                    MCPOAuthEntry.key_digest == row.key_digest,
                )
                .values(expires_at=func.clock_timestamp())
            )
        assert await stores[1].get(key, collection=_COLLECTION) is None
        assert await stores[1].cleanup_expired() >= 1
    finally:
        await stores[0].delete(key, collection=_COLLECTION)
        await engine.dispose()


async def test_real_postgres_rotated_ring_reads_and_deletes_all_aliases() -> None:
    dsn = _QASettings().database_url
    if not dsn:
        pytest.skip("Requires an explicitly configured disposable MCP PostgreSQL QA database")
    url = make_url(dsn)
    if (
        url.host not in {"localhost", "127.0.0.1", "::1"}
        or not (
            (url.database or "").startswith("wiki_mcp_qa") or url.database == "wiki_polish29_qa"
        )
        or url.drivername != "postgresql+asyncpg"
    ):
        pytest.fail("Only loopback PostgreSQL named wiki_mcp_qa* or wiki_polish29_qa is allowed")
    engine = create_async_engine(dsn)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    old, new, lock = (Fernet.generate_key().decode() for _ in range(3))
    first = PostgresOAuthStorage(sessions, OAuthStateProtector((old, new), lock_key=lock))
    second = PostgresOAuthStorage(sessions, OAuthStateProtector((new, old), lock_key=lock))
    table = MCPOAuthEntry.__table__
    assert isinstance(table, Table)
    async with engine.begin() as connection:
        await connection.run_sync(table.create, checkfirst=True)
    key = Fernet.generate_key().decode()
    try:
        await first.put(key, {"generation": 1}, collection=_COLLECTION)
        assert await second.ttl(key, collection=_COLLECTION) == ({"generation": 1}, None)
        await second.put(key, {"generation": 2}, collection=_COLLECTION)
        assert await first.get(key, collection=_COLLECTION) == {"generation": 2}
        assert await first.delete(key, collection=_COLLECTION)
        assert not await second.delete(key, collection=_COLLECTION)
        assert await second.get(key, collection=_COLLECTION) is None
    finally:
        await second.delete(key, collection=_COLLECTION)
        await engine.dispose()
