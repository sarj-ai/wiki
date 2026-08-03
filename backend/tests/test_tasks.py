from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
import uuid

from pydantic import BaseModel
import pytest
from sqlalchemy import Table
from sqlalchemy.ext.asyncio import AsyncSession

from app.identifiers import FileId, IdempotencyKey, TaskId
from app.models.task import Task
from app.tasks.errors import (
    TaskIdempotencyConflictError,
    TaskLeaseLostError,
    TaskNotClaimableError,
    TaskNotFoundError,
)
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

    async def get(self, model: type[Task], task_id: object) -> Task | None:
        del model, task_id
        return next(self._rows)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _task(*, request_hash: str, status: TaskStatus = TaskStatus.PENDING) -> Task:
    now = datetime.now(UTC)
    task = Task()
    task.id = TaskId(uuid.uuid4())
    task.owner_user_id = uuid.uuid4()
    task.kind = TaskKind.FILE_SMART_CREATE
    task.status = status
    task.idempotency_key = IdempotencyKey(uuid.uuid4())
    task.request_hash = request_hash
    task.payload = cast("dict[str, object]", {"destination": "folder-1", "name": "PRD"})
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
    return PostgresTaskService(db=cast("AsyncSession", session))


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
    row.lease_token = uuid.uuid4()
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
            lease_token=uuid.uuid4(),
            result=_Payload(name="PRD", destination="folder-1"),
        )

    assert session.rollbacks == 1


async def test_defer_atomically_releases_retryable_running_lease() -> None:
    row = _task(request_hash="0" * 64, status=TaskStatus.RUNNING)
    row.attempt_count = 1
    row.started_at = datetime.now(UTC)
    row.lease_token = uuid.uuid4()
    row.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    row.resource_id = FileId("already-created")
    session = _Session(row, row)

    deferred = await _service(session).defer(row.id, lease_token=uuid.uuid4())
    reclaimed = await _service(session).claim(row.id, lease_duration=timedelta(minutes=6))

    assert deferred.status == TaskStatus.RUNNING
    assert deferred.resource_id == FileId("already-created")
    assert reclaimed.task.status == TaskStatus.RUNNING
    assert session.commits == 2
    sql = str(session.statements[0])
    assert "tasks.lease_token IS NOT DISTINCT FROM" in sql
    assert "RETURNING" in sql


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
    table = cast("Table", Task.__table__)
    names = {constraint.name for constraint in table.constraints}

    assert "ck_tasks_legal_state" in names
    assert "uq_tasks_owner_kind_idempotency" in names


def _running(row: Task) -> Task:
    row.status = TaskStatus.RUNNING
    row.attempt_count = 1
    row.started_at = datetime.now(UTC)
    return row


def _smart_create_payload() -> dict[str, object]:
    return {
        "source": {"kind": "blank", "file_type": "document"},
        "parent_folder_id": "folder-1",
        "name": "PRD",
        "emoji": "📄",
        "alias": "prd-notes",
    }


def _smart_create_result(task_id: TaskId) -> dict[str, object]:
    return {
        "task_id": str(task_id),
        "file": {
            "id": "new-file",
            "name": "PRD",
            "mime_type": "application/vnd.google-apps.document",
            "parent_folder_id": "folder-1",
            "drive_id": "drive-1",
            "modified_time": "2026-08-01T00:00:00Z",
        },
        "warnings": [],
    }


async def test_claim_rejects_non_positive_lease_duration() -> None:
    session = _Session()

    with pytest.raises(ValueError, match="lease_duration"):
        await _service(session).claim(TaskId(uuid.uuid4()), lease_duration=timedelta(0))

    assert session.statements == []


async def test_unclaimable_task_rolls_back() -> None:
    session = _Session(None)

    with pytest.raises(TaskNotClaimableError):
        await _service(session).claim(TaskId(uuid.uuid4()), lease_duration=timedelta(minutes=1))

    assert session.rollbacks == 1
    assert session.commits == 0


async def test_record_resource_requires_the_holders_lease() -> None:
    row = _running(_task(request_hash="0" * 64))
    row.resource_id = FileId("created-file")
    session = _Session(row)

    await _service(session).record_resource(
        row.id, lease_token=uuid.uuid4(), resource_id=FileId("created-file")
    )

    assert session.commits == 1
    sql = str(session.statements[0])
    assert "tasks.lease_token IS NOT DISTINCT FROM" in sql
    assert "resource_id" in sql


async def test_succeed_clears_lease_and_serializes_result() -> None:
    row = _running(_task(request_hash="0" * 64))
    row.status = TaskStatus.SUCCEEDED
    row.result = _smart_create_result(row.id)
    row.finished_at = datetime.now(UTC)
    session = _Session(row)

    read = await _service(session).succeed(
        row.id,
        lease_token=uuid.uuid4(),
        result=_Payload(name="PRD", destination="folder-1"),
    )

    assert read.status == TaskStatus.SUCCEEDED
    assert session.commits == 1
    sql = str(session.statements[0])
    assert "tasks.status" in sql
    assert "lease_token" in sql
    assert "finished_at" in sql


async def test_fail_requires_an_error_code() -> None:
    session = _Session()

    with pytest.raises(ValueError, match="error_code"):
        await _service(session).fail(
            TaskId(uuid.uuid4()),
            lease_token=uuid.uuid4(),
            error_code="",
            error_detail=None,
        )

    assert session.statements == []


async def test_fail_records_error_and_extends_retention() -> None:
    row = _running(_task(request_hash="0" * 64))
    row.status = TaskStatus.FAILED
    row.error_code = "drive_upstream"
    row.error_detail = "Drive 502"
    row.finished_at = datetime.now(UTC)
    session = _Session(row)

    read = await _service(session).fail(
        row.id,
        lease_token=uuid.uuid4(),
        error_code="drive_upstream",
        error_detail="Drive 502",
    )

    assert read.status == TaskStatus.FAILED
    sql = str(session.statements[0])
    assert "error_code" in sql
    assert "expires_at" in sql


async def test_cancel_requires_a_pending_task_owned_by_the_caller() -> None:
    session = _Session(None)

    with pytest.raises(TaskNotClaimableError):
        await _service(session).cancel(TaskId(uuid.uuid4()), owner_user_id=uuid.uuid4())

    assert session.rollbacks == 1


async def test_cancel_marks_a_pending_task_cancelled() -> None:
    row = _task(request_hash="0" * 64, status=TaskStatus.CANCELLED)
    row.finished_at = datetime.now(UTC)
    session = _Session(row)

    read = await _service(session).cancel(row.id, owner_user_id=row.owner_user_id)

    assert read.status == TaskStatus.CANCELLED
    sql = str(session.statements[0])
    assert "tasks.owner_user_id" in sql
    assert "tasks.status" in sql


async def test_get_for_owner_hides_other_users_tasks() -> None:
    session = _Session(None)

    with pytest.raises(TaskNotFoundError):
        await _service(session).get_for_owner(TaskId(uuid.uuid4()), uuid.uuid4())

    sql = str(session.statements[0])
    assert "tasks.owner_user_id" in sql


async def test_get_for_owner_returns_the_owners_task() -> None:
    row = _task(request_hash="0" * 64)
    session = _Session(row)

    read = await _service(session).get_for_owner(row.id, row.owner_user_id)

    assert read.id == row.id
    assert read.status == TaskStatus.PENDING


async def test_load_smart_create_rejects_missing_task() -> None:
    session = _Session(None)

    with pytest.raises(TaskNotFoundError):
        await _service(session).load_smart_create(TaskId(uuid.uuid4()))


async def test_load_smart_create_returns_validated_record() -> None:
    row = _task(request_hash="0" * 64)
    row.payload = _smart_create_payload()
    session = _Session(row)

    record = await _service(session).load_smart_create(row.id)

    assert record.owner_user_id == row.owner_user_id
    assert record.payload.name == "PRD"
    assert record.payload.alias == "prd-notes"


async def test_discard_pending_reports_when_nothing_was_deleted() -> None:
    session = _Session(None)

    deleted = await _service(session).discard_pending(
        TaskId(uuid.uuid4()), owner_user_id=uuid.uuid4()
    )

    assert deleted is False
    assert session.commits == 1
