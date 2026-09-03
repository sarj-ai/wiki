from __future__ import annotations

from datetime import UTC, datetime
import uuid

import pytest

from app.document_transform.task_executor import defer_or_fail_retryable_task
from app.identifiers import TaskId
from app.tasks.errors import TaskExecutionDeferredError
from app.tasks.schemas import (
    FailedTaskRead,
    RunningTaskRead,
    TaskClaim,
    TaskKind,
    TaskRead,
    TaskStatus,
)


class _RetryTasks:
    def __init__(self) -> None:
        self.deferred = 0
        self.failed = 0

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
