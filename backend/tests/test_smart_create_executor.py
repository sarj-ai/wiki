from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import NamedTuple, override
import uuid

from pydantic import BaseModel
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.files.errors import DriveUpstreamError
from app.files.schemas import FileResponse
from app.identifiers import DriveId, FileId, IdempotencyKey, TaskId
from app.smart_create.executor import DriveSmartCreateTaskExecutor, SmartCreateRuntime
from app.smart_create.schemas import BlankSource, SmartCreateResult, SmartFileCreate
from app.tasks.errors import TaskExecutionDeferredError, TaskNotClaimableError
from app.tasks.schemas import (
    PendingTaskRead,
    RunningTaskRead,
    SmartCreateTaskRecord,
    SucceededTaskRead,
    TaskClaim,
    TaskKind,
    TaskRead,
    TaskReservation,
    TaskStatus,
)
from app.tasks.service import TaskService
from tests.support import dependency


def _payload() -> SmartFileCreate:
    return SmartFileCreate(
        source=BlankSource(kind="blank", file_type="document"),
        parent_folder_id=FileId("folder"),
        name="PRD",
        emoji="📝",
        alias="product-requirements",
    )


def _file() -> FileResponse:
    return FileResponse(
        id=FileId("created"),
        name="PRD",
        mime_type="application/vnd.google-apps.document",
        parent_folder_id=FileId("folder"),
        drive_id=DriveId("drive"),
        modified_time=None,
    )


class _Tasks(TaskService):
    def __init__(self, *, claimable: bool = True) -> None:
        self.task_id = TaskId(uuid.uuid7())
        self.owner_id = uuid.uuid7()
        self.claimable = claimable
        self.status: TaskStatus = TaskStatus.PENDING
        self.lease_duration: timedelta | None = None
        self.result: SmartCreateResult | None = None

    def read(self) -> TaskRead:
        now = datetime.now(UTC)
        if self.status == TaskStatus.SUCCEEDED:
            assert self.result is not None
            return SucceededTaskRead(
                id=self.task_id,
                kind=TaskKind.FILE_SMART_CREATE,
                status=TaskStatus.SUCCEEDED,
                result=self.result,
                resource_id=self.result.file.id,
                error_code=None,
                error_detail=None,
                attempt_count=1,
                created_at=now,
                started_at=now,
                finished_at=now,
            )
        if self.status == TaskStatus.RUNNING:
            return RunningTaskRead(
                id=self.task_id,
                kind=TaskKind.FILE_SMART_CREATE,
                status=TaskStatus.RUNNING,
                result=None,
                resource_id=None,
                error_code=None,
                error_detail=None,
                attempt_count=1,
                created_at=now,
                started_at=now,
                finished_at=None,
            )
        return PendingTaskRead(
            id=self.task_id,
            kind=TaskKind.FILE_SMART_CREATE,
            status=TaskStatus.PENDING,
            result=None,
            resource_id=None,
            error_code=None,
            error_detail=None,
            attempt_count=0,
            created_at=now,
            started_at=None,
            finished_at=None,
        )

    @override
    async def create_or_replay(
        self,
        *,
        kind: TaskKind,
        owner_user_id: uuid.UUID,
        idempotency_key: IdempotencyKey,
        payload: BaseModel,
        admit: Callable[[], Awaitable[None]] | None = None,
        admit_new: Callable[[], None] | None = None,
        admit_terminal_replay: Callable[[TaskRead], Awaitable[bool]] | None = None,
    ) -> TaskReservation:
        del admit_terminal_replay
        raise NotImplementedError

    @override
    async def claim(self, task_id: TaskId, *, lease_duration: timedelta) -> TaskClaim:
        if not self.claimable:
            self.status = TaskStatus.RUNNING
            raise TaskNotClaimableError
        self.status = TaskStatus.RUNNING
        self.lease_duration = lease_duration
        task = self.read()
        assert isinstance(task, RunningTaskRead)
        return TaskClaim(task=task, lease_token=uuid.uuid7())

    @override
    async def defer(self, task_id: TaskId, *, lease_token: uuid.UUID) -> TaskRead:
        self.status = TaskStatus.RUNNING
        return self.read()

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
        return SmartCreateTaskRecord(
            owner_user_id=self.owner_id,
            task=self.read(),
            payload=_payload(),
        )


class _Files:
    async def get(self, file_id: FileId) -> FileResponse:
        return _file()


class _Creation:
    def __init__(self, tasks: _Tasks, *, upstream_error: bool = False) -> None:
        self.tasks = tasks
        self.upstream_error = upstream_error
        self.calls = 0

    async def execute(
        self,
        *,
        task_id: TaskId,
        lease_token: uuid.UUID,
        payload: SmartFileCreate,
        existing_resource: FileResponse | None,
    ) -> SmartCreateResult:
        self.calls += 1
        if self.upstream_error:
            raise DriveUpstreamError(503)
        result = SmartCreateResult(task_id=task_id, file=_file(), warnings=[])
        self.tasks.result = result
        self.tasks.status = TaskStatus.SUCCEEDED
        return result


class _Sidebar:
    def __init__(self) -> None:
        self.invalidated: DriveId | None = None

    async def invalidate_drive(self, db: AsyncSession, drive_id: DriveId) -> None:
        self.invalidated = drive_id


class _Executor(DriveSmartCreateTaskExecutor):
    def __init__(self, tasks: _Tasks, runtime: SmartCreateRuntime) -> None:
        super().__init__(
            db=AsyncSession(),
            tasks=tasks,
            cache=dependency(object()),
            sessionmaker=async_sessionmaker(),
        )
        self.runtime = runtime

    @override
    async def _runtime(self, owner_user_id: uuid.UUID) -> SmartCreateRuntime:
        return self.runtime


class _ExecutorFixture(NamedTuple):
    executor: _Executor
    creation: _Creation
    sidebar: _Sidebar


def _executor(tasks: _Tasks, *, upstream_error: bool = False) -> _ExecutorFixture:
    creation = _Creation(tasks, upstream_error=upstream_error)
    sidebar = _Sidebar()
    runtime = SmartCreateRuntime(
        creation=dependency(creation),
        files=dependency(_Files()),
        sidebar=dependency(sidebar),
    )
    return _ExecutorFixture(_Executor(tasks, runtime), creation, sidebar)


async def test_executor_claims_runs_and_returns_typed_success() -> None:
    tasks = _Tasks()
    executor, creation, sidebar = _executor(tasks)

    result = await executor.execute(tasks.task_id)

    assert isinstance(result, SucceededTaskRead)
    assert creation.calls == 1
    assert tasks.lease_duration == timedelta(minutes=6)
    assert sidebar.invalidated == DriveId("drive")


async def test_executor_defers_when_another_delivery_owns_live_lease() -> None:
    tasks = _Tasks(claimable=False)
    executor, creation, _sidebar = _executor(tasks)

    with pytest.raises(TaskExecutionDeferredError):
        await executor.execute(tasks.task_id)

    assert creation.calls == 0


async def test_retryable_drive_error_releases_lease_for_next_queue_attempt() -> None:
    tasks = _Tasks()
    executor, creation, _sidebar = _executor(tasks, upstream_error=True)

    with pytest.raises(TaskExecutionDeferredError):
        await executor.execute(tasks.task_id)

    assert creation.calls == 1
    assert tasks.status == TaskStatus.RUNNING
