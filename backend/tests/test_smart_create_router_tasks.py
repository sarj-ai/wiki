from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast, override
import uuid

from fastapi import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.files.schemas import FileResponse
from app.files.service import FilesService
from app.identifiers import FileId, IdempotencyKey, TaskId
from app.models.user import User
from app.sidebar.cache import SidebarCacheService
from app.smart_create.creation import SmartFileCreationService
from app.smart_create.router import create_smart_file
from app.smart_create.schemas import BlankSource, SmartCreateResult, SmartFileCreate
from app.tasks.schemas import (
    PendingTaskRead,
    RunningTaskRead,
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


def _running(task_id: TaskId) -> RunningTaskRead:
    return RunningTaskRead(
        id=task_id,
        kind=TaskKind.FILE_SMART_CREATE,
        status=TaskStatus.RUNNING,
        result=None,
        resource_id=None,
        error_code=None,
        error_detail=None,
        attempt_count=1,
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        finished_at=None,
    )


class _Tasks(TaskService):
    def __init__(self, *, created: bool) -> None:
        self.task_id = TaskId(uuid.uuid4())
        self.created = created
        self.lease_duration: timedelta | None = None

    @override
    async def create_or_replay(
        self,
        *,
        kind: TaskKind,
        owner_user_id: uuid.UUID,
        idempotency_key: IdempotencyKey,
        payload: BaseModel,
    ) -> TaskReservation:
        task = _pending(self.task_id) if self.created else _running(self.task_id)
        return TaskReservation(task=task, created=self.created)

    @override
    async def claim(self, task_id: TaskId, *, lease_duration: timedelta) -> TaskClaim:
        self.lease_duration = lease_duration
        return TaskClaim(task=_running(task_id), lease_token=uuid.uuid4())

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
        return True

    @override
    async def cancel(self, task_id: TaskId, *, owner_user_id: uuid.UUID) -> TaskRead:
        raise NotImplementedError

    @override
    async def get_for_owner(self, task_id: TaskId, owner_user_id: uuid.UUID) -> TaskRead:
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


class _Creation(SmartFileCreationService):
    def __init__(self) -> None:
        self.calls = 0

    @override
    async def execute(
        self,
        *,
        task_id: TaskId,
        lease_token: uuid.UUID,
        payload: SmartFileCreate,
        existing_resource: FileResponse | None,
    ) -> SmartCreateResult:
        self.calls += 1
        return _result(task_id)


def _user() -> User:
    user = User()
    user.id = uuid.uuid4()
    return user


async def _create(
    *,
    tasks: _Tasks,
    creation: _Creation,
    response: Response | None = None,
) -> SmartCreateResult:
    return await create_smart_file(
        payload=_payload(),
        idempotency_key=uuid.uuid4(),
        response=response or Response(),
        user=_user(),
        tasks=tasks,
        creation=creation,
        files=cast("FilesService", object()),
        db=cast("AsyncSession", None),
        sidebar_cache=cast("SidebarCacheService", None),
    )


async def test_concurrent_replay_waits_for_result_without_executing() -> None:
    tasks = _Tasks(created=False)
    creation = _Creation()
    response = Response()

    result = await _create(
        tasks=tasks,
        creation=creation,
        response=response,
    )

    assert result.file.id == FileId("created")
    assert creation.calls == 0
    assert response.headers["Idempotency-Replayed"] == "true"


async def test_new_task_uses_lease_longer_than_request_timeout() -> None:
    tasks = _Tasks(created=True)
    creation = _Creation()

    result = await _create(
        tasks=tasks,
        creation=creation,
    )

    assert result.file.id == FileId("created")
    assert tasks.lease_duration == timedelta(minutes=6)
