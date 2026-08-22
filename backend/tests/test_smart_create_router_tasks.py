from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import override
import uuid

from fastapi import Response
from pydantic import BaseModel

from app.files.schemas import FileResponse
from app.identifiers import FileId, IdempotencyKey, TaskId, UserId
from app.models.user import User
from app.smart_create.router import create_smart_file
from app.smart_create.schemas import BlankSource, SmartCreateResult, SmartFileCreate
from app.tasks.queue import TaskEnqueuer
from app.tasks.schemas import (
    PendingTaskRead,
    SmartCreateTaskRecord,
    SucceededTaskRead,
    TaskClaim,
    TaskKind,
    TaskRead,
    TaskReservation,
    TaskStatus,
)
from app.tasks.service import TaskService


def _payload() -> SmartFileCreate:
    return SmartFileCreate(
        source=BlankSource(kind="blank", file_type="document"),
        parent_folder_id=FileId("folder"),
        name="PRD",
        emoji="📝",
        alias="product-requirements",
    )


def _result(task_id: TaskId) -> SmartCreateResult:
    return SmartCreateResult(
        task_id=task_id,
        file=FileResponse(
            id=FileId("created"),
            name="PRD",
            mime_type="application/vnd.google-apps.document",
            parent_folder_id=FileId("folder"),
            drive_id=None,
            modified_time=None,
        ),
        warnings=[],
    )


def _pending(task_id: TaskId) -> PendingTaskRead:
    return PendingTaskRead(
        id=task_id,
        kind=TaskKind.FILE_SMART_CREATE,
        status=TaskStatus.PENDING,
        result=None,
        resource_id=None,
        error_code=None,
        error_detail=None,
        attempt_count=0,
        created_at=datetime.now(UTC),
        started_at=None,
        finished_at=None,
    )


def _succeeded(task_id: TaskId) -> SucceededTaskRead:
    result = _result(task_id)
    return SucceededTaskRead(
        id=task_id,
        kind=TaskKind.FILE_SMART_CREATE,
        status=TaskStatus.SUCCEEDED,
        result=result,
        resource_id=result.file.id,
        error_code=None,
        error_detail=None,
        attempt_count=1,
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )


class _Tasks(TaskService):
    def __init__(self, *, created: bool, terminal: bool = False) -> None:
        self.task_id = TaskId(uuid.uuid4())
        self.created = created
        self.terminal = terminal

    @override
    async def create_or_replay(
        self,
        *,
        kind: TaskKind,
        owner_user_id: uuid.UUID,
        idempotency_key: IdempotencyKey,
        payload: BaseModel,
    ) -> TaskReservation:
        task = _succeeded(self.task_id) if self.terminal else _pending(self.task_id)
        return TaskReservation(task=task, created=self.created)

    @override
    async def claim(self, task_id: TaskId, *, lease_duration: timedelta) -> TaskClaim:
        raise NotImplementedError

    @override
    async def defer(self, task_id: TaskId, *, lease_token: uuid.UUID) -> TaskRead:
        raise NotImplementedError

    @override
    async def record_resource(
        self, task_id: TaskId, *, lease_token: uuid.UUID, resource_id: FileId
    ) -> None:
        raise NotImplementedError

    @override
    async def succeed(
        self, task_id: TaskId, *, lease_token: uuid.UUID, result: BaseModel
    ) -> TaskRead:
        raise NotImplementedError

    @override
    async def fail(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        error_code: str,
        error_detail: str | None,
    ) -> TaskRead:
        raise NotImplementedError

    @override
    async def discard_pending(self, task_id: TaskId, *, owner_user_id: uuid.UUID) -> bool:
        raise NotImplementedError

    @override
    async def cancel(self, task_id: TaskId, *, owner_user_id: uuid.UUID) -> TaskRead:
        raise NotImplementedError

    @override
    async def get_for_owner(self, task_id: TaskId, owner_user_id: uuid.UUID) -> TaskRead:
        raise NotImplementedError

    @override
    async def load_smart_create(self, task_id: TaskId) -> SmartCreateTaskRecord:
        raise NotImplementedError


class _Enqueuer(TaskEnqueuer):
    def __init__(self) -> None:
        self.task_ids: list[TaskId] = []

    @override
    async def enqueue_smart_create(self, task_id: TaskId) -> None:
        self.task_ids.append(task_id)


def _user() -> User:
    user = User()
    user.id = UserId(uuid.uuid4())
    return user


async def test_new_task_returns_pending_immediately_and_enqueues() -> None:
    tasks = _Tasks(created=True)
    enqueuer = _Enqueuer()
    response = Response()

    task = await create_smart_file(
        payload=_payload(),
        idempotency_key=uuid.uuid4(),
        response=response,
        user=_user(),
        tasks=tasks,
        enqueuer=enqueuer,
    )

    assert isinstance(task, PendingTaskRead)
    assert enqueuer.task_ids == [task.id]
    assert response.headers["Idempotency-Replayed"] == "false"
    assert response.headers["Retry-After"] == "1"


async def test_pending_replay_reenqueues_deterministically() -> None:
    tasks = _Tasks(created=False)
    enqueuer = _Enqueuer()

    task = await create_smart_file(
        payload=_payload(),
        idempotency_key=uuid.uuid4(),
        response=Response(),
        user=_user(),
        tasks=tasks,
        enqueuer=enqueuer,
    )

    assert enqueuer.task_ids == [task.id]


async def test_succeeded_replay_returns_typed_result_without_enqueue() -> None:
    tasks = _Tasks(created=False, terminal=True)
    enqueuer = _Enqueuer()

    task = await create_smart_file(
        payload=_payload(),
        idempotency_key=uuid.uuid4(),
        response=Response(),
        user=_user(),
        tasks=tasks,
        enqueuer=enqueuer,
    )

    assert isinstance(task, SucceededTaskRead)
    assert task.result.file.id == FileId("created")
    assert enqueuer.task_ids == []
