from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
import uuid

from pydantic import BaseModel
import pytest
from sqlalchemy import Table
from sqlalchemy.sql.elements import ClauseElement

from app.identifiers import FileId, IdempotencyKey, TaskId
from app.models.task import Task
from app.tasks.errors import TaskIdempotencyConflictError, TaskLeaseLostError
from app.tasks.schemas import TaskKind, TaskStatus
from app.tasks.service import PostgresTaskService


class _Payload(BaseModel):
    name: str
    destination: str


class _Result:
    def __init__(self, row: Task | None) -> None:
        self._row = row

    def scalar_one_or_none(self) -> Task | None:
        return self._row


class _Session:
    def __init__(self, *rows: Task | None) -> None:
        self._rows = iter(rows)
        self.commits = 0
        self.rollbacks = 0
        self.statements: list[object] = []

    async def execute(self, statement: object) -> _Result:
        self.statements.append(statement)
        return _Result(next(self._rows))

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _task(*, request_hash: str, status: TaskStatus = TaskStatus.PENDING) -> Task:
    now = datetime.now(UTC)
    task = Task()
    task.id = TaskId(uuid.uuid7())
    task.owner_user_id = uuid.uuid7()
    task.kind = TaskKind.FILE_SMART_CREATE
    task.status = status
    task.idempotency_key = IdempotencyKey(uuid.uuid7())
    task.request_hash = request_hash
    task.payload = {"destination": "folder-1", "name": "PRD"}
    task.result = None
    task.resource_id = None
    task.error_code = None
    task.error_detail = None
    task.attempt_count = 0
    task.lease_token = None
    task.lease_expires_at = None
    task.created_at = now
    task.started_at = None
    task.finished_at = None
    task.updated_at = now
    task.expires_at = now + timedelta(days=7)
    return task


def _service(session: _Session) -> PostgresTaskService:
    db: Any = session
    return PostgresTaskService(db=db)


async def test_create_or_replay_creates_one_canonical_task() -> None:
    # SHA-256 of the service's canonical compact/sorted JSON representation.
    row = _task(request_hash="34664c7dd3fa3e21bde1016366dfa8933e84372be6976afed70d10d4da73c7a6")
    session = _Session(row)

    reservation = await _service(session).create_or_replay(
        kind=TaskKind.FILE_SMART_CREATE,
        owner_user_id=row.owner_user_id,
        idempotency_key=row.idempotency_key,
        payload=_Payload(name="PRD", destination="folder-1"),
    )

    assert reservation.created is True
    assert reservation.task.id == row.id
    assert session.commits == 1
    sql = str(session.statements[0])
    assert "ON CONFLICT" in sql
    assert "owner_user_id" in sql
    assert "idempotency_key" in sql


async def test_same_key_with_changed_payload_is_rejected() -> None:
    row = _task(request_hash="0" * 64)
    session = _Session(None, row)

    with pytest.raises(TaskIdempotencyConflictError):
        await _service(session).create_or_replay(
            kind=TaskKind.FILE_SMART_CREATE,
            owner_user_id=row.owner_user_id,
            idempotency_key=row.idempotency_key,
            payload=_Payload(name="Different", destination="folder-1"),
        )


async def test_same_key_with_same_payload_replays_without_another_commit() -> None:
    row = _task(request_hash="34664c7dd3fa3e21bde1016366dfa8933e84372be6976afed70d10d4da73c7a6")
    session = _Session(None, row)

    reservation = await _service(session).create_or_replay(
        kind=TaskKind.FILE_SMART_CREATE,
        owner_user_id=row.owner_user_id,
        idempotency_key=row.idempotency_key,
        payload=_Payload(name="PRD", destination="folder-1"),
    )

    assert reservation.created is False
    assert reservation.task.id == row.id
    assert session.commits == 0


async def test_claim_is_an_atomic_conditional_transition_with_a_lease() -> None:
    row = _task(request_hash="0" * 64, status=TaskStatus.RUNNING)
    row.attempt_count = 1
    row.started_at = datetime.now(UTC)
    row.lease_token = uuid.uuid7()
    row.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    session = _Session(row)

    claim = await _service(session).claim(row.id, lease_duration=timedelta(minutes=1))

    assert claim.lease_token is not None
    assert session.commits == 1
    sql = str(session.statements[0])
    assert "tasks.status" in sql
    assert "tasks.lease_expires_at" in sql
    assert "RETURNING" in sql


async def test_stale_executor_cannot_complete_a_reclaimed_task() -> None:
    row = _task(request_hash="0" * 64, status=TaskStatus.RUNNING)
    session = _Session(None)

    with pytest.raises(TaskLeaseLostError):
        await _service(session).succeed(
            row.id,
            lease_token=uuid.uuid7(),
            result=_Payload(name="PRD", destination="folder-1"),
        )

    assert session.rollbacks == 1


async def test_defer_atomically_releases_retryable_running_lease() -> None:
    row = _task(request_hash="0" * 64, status=TaskStatus.RUNNING)
    row.attempt_count = 1
    row.started_at = datetime.now(UTC)
    row.lease_token = uuid.uuid7()
    row.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    row.resource_id = FileId("already-created")
    session = _Session(row, row)

    deferred = await _service(session).defer(row.id, lease_token=uuid.uuid7())
    reclaimed = await _service(session).claim(row.id, lease_duration=timedelta(minutes=6))

    assert deferred.status == TaskStatus.RUNNING
    assert deferred.resource_id == FileId("already-created")
    assert reclaimed.task.status == TaskStatus.RUNNING
    assert session.commits == 2
    sql = str(session.statements[0])
    assert "tasks.lease_token IS NOT DISTINCT FROM" in sql
    assert "RETURNING" in sql


async def test_failed_task_persists_only_bounded_public_detail() -> None:
    now = datetime.now(UTC)
    row = _task(request_hash="0" * 64, status=TaskStatus.FAILED)
    row.attempt_count = 1
    row.started_at = now
    row.finished_at = now
    row.error_code = "provider_failed"
    row.error_detail = "Task could not be completed"
    raw_provider_detail = (
        "https://www.googleapis.com/drive/v3/files/private-resource-id "
        "Bearer ya29.provider-access-token " + '{"error":{"message":"private provider body"}}' * 100
    )
    session = _Session(row)

    await _service(session).fail(
        row.id,
        lease_token=uuid.uuid7(),
        error_code="provider_failed",
        error_detail=raw_provider_detail,
    )

    statement = session.statements[0]
    assert isinstance(statement, ClauseElement)
    params = statement.compile().params or {}
    persisted = params["error_detail"]
    assert isinstance(persisted, str)
    assert persisted == "Task could not be completed"
    assert len(persisted) <= 256
    assert "private-resource-id" not in persisted
    assert "ya29.provider-access-token" not in persisted
    assert "private provider body" not in persisted


async def test_pending_task_can_be_discarded_before_execution() -> None:
    row = _task(request_hash="0" * 64)
    session = _Session(row)

    deleted = await _service(session).discard_pending(row.id, owner_user_id=row.owner_user_id)

    assert deleted is True
    assert session.commits == 1
    sql = str(session.statements[0])
    assert "DELETE FROM tasks" in sql
    assert "tasks.status" in sql
    assert "tasks.resource_id IS NULL" in sql


def test_database_model_carries_legal_state_and_idempotency_constraints() -> None:
    table = Task.__table__
    assert isinstance(table, Table)
    names = {constraint.name for constraint in table.constraints}

    assert "ck_tasks_legal_state" in names
    assert "uq_tasks_owner_kind_idempotency" in names
