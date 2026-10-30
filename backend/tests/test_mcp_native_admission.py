import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import datetime, timedelta
import traceback
import uuid

from pydantic_settings import BaseSettings, SettingsConfigDict
import pytest
from sqlalchemy import Table, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from app.mcp import native_admission
from app.mcp.native_admission import (
    PostgresRegistrationAdmission,
    RegistrationAdmissionDeniedError,
    RegistrationAdmissionUnavailableError,
)
from app.models.mcp_registration_admission import McpRegistrationAdmission


class QASettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WIKI_MCP_QA_")
    database_url: str | None = None


@dataclass
class Lab:
    sessions: async_sessionmaker[AsyncSession]
    now: datetime

    async def seed(self, history: list[datetime]) -> None:
        async with self.sessions.begin() as db:
            db.add(McpRegistrationAdmission(id=1, admitted_at=history))

    async def history(self) -> list[datetime]:
        async with self.sessions() as db:
            history = await db.scalar(select(McpRegistrationAdmission.admitted_at))
            return history or []


@pytest.fixture
async def lab() -> AsyncGenerator[Lab]:
    dsn = QASettings().database_url
    if dsn is None:
        pytest.skip("Requires disposable admission PostgreSQL QA database")
    url = make_url(dsn)
    if (
        url.host not in {"127.0.0.1", "localhost", "::1"}
        or not (
            (url.database or "").startswith("wiki_mcp_qa") or url.database == "wiki_polish29_qa"
        )
        or url.drivername != "postgresql+asyncpg"
    ):
        pytest.fail("Only loopback PostgreSQL named wiki_mcp_qa* or wiki_polish29_qa is allowed")
    schema = "admission_test_" + uuid.uuid7().hex
    engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.execute(CreateSchema(schema))
            table = McpRegistrationAdmission.__table__
            assert isinstance(table, Table)
            await connection.run_sync(table.create)
        async with sessions() as db:
            now = await native_admission._database_time(db)
        yield Lab(sessions, now)
    finally:
        async with engine.begin() as connection:
            await connection.execute(DropSchema(schema, cascade=True))
        await engine.dispose()


def freeze_time(lab: Lab, monkeypatch: pytest.MonkeyPatch) -> None:
    async def database_time(_db: AsyncSession) -> datetime:
        return lab.now

    monkeypatch.setattr(native_admission, "_database_time", database_time)


async def test_two_instances_admit_exactly_ten_and_restarts_preserve_budget(lab: Lab) -> None:
    instances = [PostgresRegistrationAdmission(lab.sessions) for _ in range(2)]
    results = await asyncio.gather(
        *(instances[index % 2].admit() for index in range(24)), return_exceptions=True
    )
    assert sum(value is None for value in results) == 10
    assert sum(isinstance(value, RegistrationAdmissionDeniedError) for value in results) == 14
    assert len(await lab.history()) == 10
    with pytest.raises(RegistrationAdmissionDeniedError) as error:
        await PostgresRegistrationAdmission(lab.sessions).admit()
    assert 1 <= error.value.retry_after_seconds <= 60


@pytest.mark.parametrize(("limit", "seconds"), [(10, 60), (300, 1800)])
@pytest.mark.parametrize("offset_us", [-1, 0, 1])
async def test_exact_rolling_window_boundaries(
    lab: Lab, monkeypatch: pytest.MonkeyPatch, limit: int, seconds: int, offset_us: int
) -> None:
    freeze_time(lab, monkeypatch)
    timestamp = lab.now - timedelta(seconds=seconds) + timedelta(microseconds=offset_us)
    await lab.seed([timestamp] * limit)
    admission = PostgresRegistrationAdmission(lab.sessions)
    if offset_us > 0:
        with pytest.raises(RegistrationAdmissionDeniedError) as error:
            await admission.admit()
        assert error.value.retry_after_seconds == 1
        assert len(await lab.history()) == limit
    else:
        await admission.admit()
        expected = limit + 1 if seconds == 60 else 1
        assert len(await lab.history()) == expected


async def test_retry_accounts_for_both_exhausted_windows(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze_time(lab, monkeypatch)
    await lab.seed([lab.now - timedelta(seconds=1700)] * 290 + [lab.now] * 10)
    with pytest.raises(RegistrationAdmissionDeniedError) as error:
        await PostgresRegistrationAdmission(lab.sessions).admit()
    assert error.value.retry_after_seconds == 100


async def test_expired_history_is_replaced_by_one_current_admission(lab: Lab) -> None:
    await lab.seed([lab.now - timedelta(hours=1)] * 300)
    await PostgresRegistrationAdmission(lab.sessions).admit()
    history = await lab.history()
    assert len(history) == 1
    assert history[0] >= lab.now


async def test_later_transaction_failure_cannot_refund_admission(lab: Lab) -> None:
    admission = PostgresRegistrationAdmission(lab.sessions)

    async def failed_registration() -> None:
        async with lab.sessions.begin() as db:
            await admission.admit()
            db.add(McpRegistrationAdmission(id=2, admitted_at=[]))

    with pytest.raises(IntegrityError):
        await failed_registration()
    assert len(await lab.history()) == 1


@pytest.mark.parametrize("mode", ["cancel", "deadline"])
async def test_interrupted_admission_rolls_back_and_releases_lock(
    lab: Lab, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    entered = asyncio.Event()
    never = asyncio.Event()

    async def blocked_time(_db: AsyncSession) -> datetime:
        entered.set()
        await never.wait()
        return lab.now

    with monkeypatch.context() as interrupted:
        interrupted.setattr(native_admission, "_database_time", blocked_time)
        if mode == "deadline":
            interrupted.setattr(native_admission, "_ADMISSION_TIMEOUT", timedelta(milliseconds=50))
        task = asyncio.create_task(PostgresRegistrationAdmission(lab.sessions).admit())
        await entered.wait()
        if mode == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(RegistrationAdmissionUnavailableError):
                await task
    assert await lab.history() == []
    await PostgresRegistrationAdmission(lab.sessions).admit()
    assert len(await lab.history()) == 1


async def test_database_failure_is_constant_and_redacted(lab: Lab) -> None:
    async with lab.sessions.begin() as db:
        table = McpRegistrationAdmission.__table__
        assert isinstance(table, Table)
        connection = await db.connection()
        await connection.run_sync(table.drop)
    with pytest.raises(RegistrationAdmissionUnavailableError) as error:
        await PostgresRegistrationAdmission(lab.sessions).admit()
    rendered = "".join(traceback.format_exception(error.value))
    assert "UndefinedTable" not in rendered
    assert "SELECT" not in rendered
    assert str(error.value) == "Wiki registration admission unavailable"


@pytest.mark.parametrize("history", ["oversize", "null-element", "null-array"])
async def test_database_rejects_invalid_ledger(lab: Lab, history: str) -> None:
    statements = {
        "oversize": "INSERT INTO mcp_registration_admission VALUES (1, array_fill(now(), ARRAY[301]))",
        "null-element": "INSERT INTO mcp_registration_admission VALUES (1, ARRAY[NULL]::timestamptz[])",
        "null-array": "INSERT INTO mcp_registration_admission VALUES (1, NULL)",
    }
    with pytest.raises(IntegrityError):
        async with lab.sessions.begin() as db:
            await db.execute(text(statements[history]))
    assert await lab.history() == []
