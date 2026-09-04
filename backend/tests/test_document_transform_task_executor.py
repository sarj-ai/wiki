from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Never, override
import uuid

import pytest

from app.config import settings
from app.document_transform.schemas import (
    DocumentTransformRequest,
    DriveFileSource,
    MarkdownSource,
    OutputStrategy,
    TransformMode,
)
from app.document_transform.task_executor import (
    DocumentTransformTaskExecutor,
    DriveDocumentTransformTaskExecutor,
    QueuedDocumentTransformTaskExecutor,
    defer_or_fail_retryable_task,
)
from app.drive.retry import ProviderDeadlineError
from app.identifiers import FileId, TaskId
from app.performance import (
    begin_request_metrics,
    provider_retry_remaining_seconds,
    reset_request_metrics,
)
from app.tasks.errors import TaskExecutionDeferredError
from app.tasks.internal_router import execute_document_transform_task
from app.tasks.schemas import (
    DocumentTransformTaskRecord,
    FailedTaskRead,
    RunningTaskRead,
    TaskClaim,
    TaskDispatchRequest,
    TaskKind,
    TaskRead,
    TaskStatus,
)


class _RetryTasks:
    def __init__(
        self,
        *,
        attempt_count: int = 1,
        payload: DocumentTransformRequest | None = None,
    ) -> None:
        self.deferred = 0
        self.failed = 0
        self.attempt_count = attempt_count
        self.payload = payload or _payload()
        self.last_error_code: str | None = None

    async def defer(self, task_id: TaskId, *, lease_token: uuid.UUID) -> TaskRead:
        self.deferred += 1
        return _claim(task_id, attempt_count=1).task

    async def fail(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        error_code: str,
        error_detail: str | None,
    ) -> TaskRead:
        self.failed += 1
        self.last_error_code = error_code
        now = datetime.now(UTC)
        return FailedTaskRead(
            id=task_id,
            kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
            status=TaskStatus.FAILED,
            result=None,
            resource_id=None,
            error_code=error_code,
            error_detail=error_detail,
            attempt_count=10,
            created_at=now,
            started_at=now,
            finished_at=now,
        )

    async def record_resource(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        resource_id: FileId,
    ) -> None:
        del task_id, lease_token, resource_id
        pytest.fail("deadline path must not record a resource")

    async def succeed(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        result: object,
    ) -> TaskRead:
        del task_id, lease_token, result
        pytest.fail("deadline path must not succeed")

    async def load_document_transform(
        self,
        task_id: TaskId,
    ) -> DocumentTransformTaskRecord:
        return DocumentTransformTaskRecord(
            owner_user_id=uuid.uuid7(),
            task=_claim(task_id, attempt_count=self.attempt_count).task,
            payload=self.payload,
        )

    async def claim(self, task_id: TaskId, *, lease_duration: timedelta) -> TaskClaim:
        del lease_duration
        return _claim(task_id, attempt_count=self.attempt_count)


class _DeadlineTransform:
    async def execute(
        self,
        *,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
    ) -> Never:
        del operation_id, request
        raise ProviderDeadlineError


class _BudgetProbeExecutor(DocumentTransformTaskExecutor):
    def __init__(self) -> None:
        self.remaining_seconds: float | None = None

    @override
    async def execute(self, task_id: TaskId) -> TaskRead:
        self.remaining_seconds = provider_retry_remaining_seconds()
        return _claim(task_id, attempt_count=1).task


async def test_retryable_failure_defers_before_attempt_limit() -> None:
    tasks = _RetryTasks()
    task_id = TaskId(uuid.uuid7())

    with pytest.raises(TaskExecutionDeferredError):
        await defer_or_fail_retryable_task(
            task_id,
            tasks=tasks,
            claim=_claim(task_id, attempt_count=9),
            error_code="model_unavailable",
            error_detail="Retry later",
            cause=RuntimeError("provider timeout"),
        )

    assert tasks.deferred == 1
    assert tasks.failed == 0


async def test_retryable_failure_becomes_terminal_at_attempt_limit() -> None:
    tasks = _RetryTasks()
    task_id = TaskId(uuid.uuid7())

    result = await defer_or_fail_retryable_task(
        task_id,
        tasks=tasks,
        claim=_claim(task_id, attempt_count=10),
        error_code="model_unavailable",
        error_detail="Retry later",
        cause=RuntimeError("provider timeout"),
    )

    assert result.status is TaskStatus.FAILED
    assert tasks.deferred == 0
    assert tasks.failed == 1


async def test_provider_deadline_defers_claimed_task_without_stranding_lease() -> None:
    tasks = _RetryTasks()
    task_id = TaskId(uuid.uuid7())
    executor = QueuedDocumentTransformTaskExecutor(
        tasks=tasks,
        transform=_DeadlineTransform(),
    )

    with pytest.raises(TaskExecutionDeferredError):
        await executor.execute(task_id)

    assert tasks.deferred == 1
    assert tasks.failed == 0


async def test_provider_deadline_becomes_terminal_at_attempt_limit() -> None:
    tasks = _RetryTasks(attempt_count=10)
    task_id = TaskId(uuid.uuid7())
    executor = QueuedDocumentTransformTaskExecutor(
        tasks=tasks,
        transform=_DeadlineTransform(),
    )

    result = await executor.execute(task_id)

    assert isinstance(result, FailedTaskRead)
    assert result.error_code == "document_provider_unavailable"
    assert tasks.deferred == 0
    assert tasks.failed == 1


async def test_internal_document_transform_gets_worker_provider_budget() -> None:
    task_id = TaskId(uuid.uuid7())
    executor = _BudgetProbeExecutor()
    tokens = begin_request_metrics()
    try:
        interactive_budget = provider_retry_remaining_seconds()
        await execute_document_transform_task(
            TaskDispatchRequest(task_id=task_id),
            executor,
        )
        restored_budget = provider_retry_remaining_seconds()
    finally:
        reset_request_metrics(tokens)

    assert interactive_budget is not None
    assert executor.remaining_seconds is not None
    assert executor.remaining_seconds > 230
    assert restored_budget is not None
    assert restored_budget <= interactive_budget


async def test_worker_kill_switch_blocks_claimed_in_place_task_before_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_IN_PLACE_ENABLED", False)
    tasks = _RetryTasks(payload=_in_place_payload())
    executor = object.__new__(DriveDocumentTransformTaskExecutor)
    monkeypatch.setattr(executor, "_tasks", tasks, raising=False)

    result = await executor.execute(TaskId(uuid.uuid7()))

    assert isinstance(result, FailedTaskRead)
    assert tasks.last_error_code == "in_place_disabled"
    assert tasks.failed == 1


def _claim(task_id: TaskId, *, attempt_count: int) -> TaskClaim:
    now = datetime.now(UTC)
    return TaskClaim(
        task=RunningTaskRead(
            id=task_id,
            kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
            status=TaskStatus.RUNNING,
            result=None,
            resource_id=None,
            error_code=None,
            error_detail=None,
            attempt_count=attempt_count,
            created_at=now,
            started_at=now,
            finished_at=None,
        ),
        lease_token=uuid.uuid7(),
    )


def _payload() -> DocumentTransformRequest:
    return DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown",
            content="# Decision\n\nThe team approved option B.",
            name="Decision",
            destination_folder_id=FileId("folder"),
        ),
        mode=TransformMode.BOTH,
    )


def _in_place_payload() -> DocumentTransformRequest:
    return DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file",
            file_id=FileId("source"),
            expected_modified_time="2026-09-03T00:00:00Z",
        ),
        mode=TransformMode.FORMAT,
        output_strategy=OutputStrategy.IN_PLACE,
    )
