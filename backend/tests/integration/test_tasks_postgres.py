"""Idempotency and lease semantics of PostgresTaskService against real Postgres.

The unit suite exercises this service through fake sessions that assert on
compiled SQL; these tests execute the ON CONFLICT and conditional-UPDATE
statements for real, where the constraints and race outcomes actually live.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import uuid

from pydantic import BaseModel
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.files.schemas import FileResponse
from app.identifiers import FileId, IdempotencyKey, TaskId
from app.models.task import Task
from app.smart_create.schemas import SmartCreateResult
from app.tasks.errors import (
    TaskIdempotencyConflictError,
    TaskLeaseLostError,
    TaskNotClaimableError,
)
from app.tasks.schemas import TaskKind, TaskStatus
from app.tasks.service import PostgresTaskService


pytestmark = pytest.mark.integration


class _Payload(BaseModel):
    name: str
    destination: str


def _payload(name: str = "PRD") -> _Payload:
    return _Payload(name=name, destination="folder-1")


def _result(task_id: TaskId) -> SmartCreateResult:
    return SmartCreateResult(
        task_id=task_id,
        file=FileResponse(
            id=FileId("new-file"),
            name="PRD",
            mime_type="application/vnd.google-apps.document",
            parent_folder_id=FileId("folder-1"),
            drive_id=None,
            modified_time="2026-08-01T00:00:00Z",
        ),
        warnings=[],
    )


async def _count_tasks(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(Task))).scalar_one()


async def test_concurrent_create_or_replay_produces_exactly_one_task(
    pg_sessionmaker: async_sessionmaker[AsyncSession],
    pg_session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    key = IdempotencyKey(uuid.uuid4())

    async def attempt() -> tuple[bool, TaskId]:
        async with pg_sessionmaker() as session:
            reservation = await PostgresTaskService(db=session).create_or_replay(
                kind=TaskKind.FILE_SMART_CREATE,
                owner_user_id=owner_id,
                idempotency_key=key,
                payload=_payload(),
            )
            return reservation.created, reservation.task.id

    outcomes = await asyncio.gather(attempt(), attempt())

    assert sorted(created for created, _ in outcomes) == [False, True]
    assert len({task_id for _, task_id in outcomes}) == 1
    assert await _count_tasks(pg_session) == 1


async def test_same_key_with_different_payload_conflicts_without_an_orphan_row(
    pg_session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    key = IdempotencyKey(uuid.uuid4())
    service = PostgresTaskService(db=pg_session)
    original = await service.create_or_replay(
        kind=TaskKind.FILE_SMART_CREATE,
        owner_user_id=owner_id,
        idempotency_key=key,
        payload=_payload("Original"),
    )

    with pytest.raises(TaskIdempotencyConflictError):
        await service.create_or_replay(
            kind=TaskKind.FILE_SMART_CREATE,
            owner_user_id=owner_id,
            idempotency_key=key,
            payload=_payload("Tampered"),
        )

    assert await _count_tasks(pg_session) == 1
    row = (await pg_session.execute(select(Task))).scalar_one()
    assert row.id == original.task.id
    assert row.payload["name"] == "Original"


async def test_replaying_the_same_payload_returns_the_original_task(
    pg_session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    key = IdempotencyKey(uuid.uuid4())
    service = PostgresTaskService(db=pg_session)
    first = await service.create_or_replay(
        kind=TaskKind.FILE_SMART_CREATE,
        owner_user_id=owner_id,
        idempotency_key=key,
        payload=_payload(),
    )

    replay = await service.create_or_replay(
        kind=TaskKind.FILE_SMART_CREATE,
        owner_user_id=owner_id,
        idempotency_key=key,
        payload=_payload(),
    )

    assert first.created is True
    assert replay.created is False
    assert replay.task.id == first.task.id


async def test_a_valid_lease_excludes_other_claimers(
    pg_session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    service = PostgresTaskService(db=pg_session)
    reservation = await service.create_or_replay(
        kind=TaskKind.FILE_SMART_CREATE,
        owner_user_id=owner_id,
        idempotency_key=IdempotencyKey(uuid.uuid4()),
        payload=_payload(),
    )

    claim = await service.claim(reservation.task.id, lease_duration=timedelta(minutes=5))

    assert claim.task.status == TaskStatus.RUNNING
    with pytest.raises(TaskNotClaimableError):
        await service.claim(reservation.task.id, lease_duration=timedelta(minutes=5))


async def test_expired_lease_can_be_reclaimed_and_the_stale_token_is_dead(
    pg_session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    service = PostgresTaskService(db=pg_session)
    reservation = await service.create_or_replay(
        kind=TaskKind.FILE_SMART_CREATE,
        owner_user_id=owner_id,
        idempotency_key=IdempotencyKey(uuid.uuid4()),
        payload=_payload(),
    )
    task_id = reservation.task.id

    stale = await service.claim(task_id, lease_duration=timedelta(minutes=5))
    # Expire the lease deterministically instead of sleeping past it
    # (ck_tasks_lease_after_start requires lease_expires_at > started_at,
    # so both timestamps move into the past together).
    await pg_session.execute(
        update(Task)
        .where(Task.id == task_id)
        .values(
            started_at=func.now() - timedelta(minutes=10),
            lease_expires_at=func.now() - timedelta(minutes=5),
        )
    )
    await pg_session.commit()

    reclaimed = await service.claim(task_id, lease_duration=timedelta(minutes=5))
    assert reclaimed.task.attempt_count == 2

    with pytest.raises(TaskLeaseLostError):
        await service.succeed(task_id, lease_token=stale.lease_token, result=_result(task_id))
    with pytest.raises(TaskLeaseLostError):
        await service.fail(
            task_id,
            lease_token=stale.lease_token,
            error_code="stale",
            error_detail=None,
        )

    finished = await service.succeed(
        task_id, lease_token=reclaimed.lease_token, result=_result(task_id)
    )
    assert finished.status == TaskStatus.SUCCEEDED


async def test_terminal_task_cannot_be_reclaimed(
    pg_session: AsyncSession,
    owner_id: uuid.UUID,
) -> None:
    service = PostgresTaskService(db=pg_session)
    reservation = await service.create_or_replay(
        kind=TaskKind.FILE_SMART_CREATE,
        owner_user_id=owner_id,
        idempotency_key=IdempotencyKey(uuid.uuid4()),
        payload=_payload(),
    )
    task_id = reservation.task.id
    claim = await service.claim(task_id, lease_duration=timedelta(minutes=5))
    await service.succeed(task_id, lease_token=claim.lease_token, result=_result(task_id))

    with pytest.raises(TaskNotClaimableError):
        await service.claim(task_id, lease_duration=timedelta(minutes=5))
