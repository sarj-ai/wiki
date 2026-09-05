from __future__ import annotations

import asyncio
import uuid

from pydantic_settings import BaseSettings, SettingsConfigDict
import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.document_transform.schemas import (
    DocumentTransformRequest,
    DriveFileSource,
    TransformMode,
)
from app.identifiers import FileId, GoogleUserId, IdempotencyKey, UserId
from app.models.task import Task
from app.models.task_replay_key import TaskReplayKey
from app.models.user import User
from app.tasks.errors import TaskIdempotencyConflictError, TaskTargetBusyError
from app.tasks.schemas import TaskKind
from app.tasks.service import PostgresTaskService


class _QASettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WIKI_POLISH29_QA_")
    database_url: str | None = None


def _qa_engine() -> AsyncEngine:
    dsn = _QASettings().database_url
    if not dsn:
        pytest.skip("Requires an explicitly configured disposable PostgreSQL QA database")
    url = make_url(dsn)
    if (
        url.host not in {"127.0.0.1", "localhost", "::1"}
        or not (url.database or "").startswith("wiki_polish29_qa")
        or url.drivername != "postgresql+asyncpg"
    ):
        pytest.fail("Only a loopback PostgreSQL database named wiki_polish29_qa* is allowed")
    return create_async_engine(dsn, pool_size=12, max_overflow=0)


async def test_real_postgres_concurrent_source_dedupe() -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = UserId(uuid.uuid7())
    source = FileId(f"qa-source-{uuid.uuid7()}")
    payload = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file", file_id=source, expected_modified_time="2026-09-05T00:00:00Z"
        ),
        mode=TransformMode.FORMAT,
    )
    keys = [IdempotencyKey(uuid.uuid7()) for _ in range(8)]
    start = asyncio.Event()
    admission_calls = 0

    async def admit() -> None:
        nonlocal admission_calls
        admission_calls += 1
        await asyncio.sleep(0.02)

    async def reserve(key: IdempotencyKey):
        await start.wait()
        async with sessions() as session:
            return await PostgresTaskService(db=session).create_or_replay(
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                owner_user_id=owner,
                idempotency_key=key,
                payload=payload,
                admit=admit,
            )

    try:
        async with sessions() as session:
            session.add(
                User(id=owner, email=f"qa-{owner}@sarj.ai", google_id=GoogleUserId(str(owner)))
            )
            await session.commit()
        pending = [asyncio.create_task(reserve(key)) for key in keys]
        start.set()
        reservations = await asyncio.gather(*pending)
        task_id = reservations[0].task.id
        assert {reservation.task.id for reservation in reservations} == {task_id}
        assert sum(reservation.created for reservation in reservations) == 1
        assert admission_calls == len(keys)
        async with sessions() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(Task).where(Task.owner_user_id == owner)
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(TaskReplayKey)
                    .where(TaskReplayKey.owner_user_id == owner)
                )
                == len(keys) - 1
            )
            service = PostgresTaskService(db=session)
            with pytest.raises(TaskTargetBusyError):
                await service.create_or_replay(
                    kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                    owner_user_id=owner,
                    idempotency_key=IdempotencyKey(uuid.uuid7()),
                    payload=payload.model_copy(update={"mode": TransformMode.UNSLOP}),
                    admit=admit,
                )
            await service.cancel(task_id, owner_user_id=owner)
        for key in keys:
            replay = await reserve(key)
            assert replay.task.id == task_id
            assert not replay.created
        async with sessions() as session:
            with pytest.raises(TaskIdempotencyConflictError):
                await PostgresTaskService(db=session).create_or_replay(
                    kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                    owner_user_id=owner,
                    idempotency_key=keys[-1],
                    payload=payload.model_copy(update={"mode": TransformMode.BOTH}),
                    admit=admit,
                )
        deliberate = await reserve(IdempotencyKey(uuid.uuid7()))
        assert deliberate.created
        assert deliberate.task.id != task_id
    finally:
        await engine.dispose()
