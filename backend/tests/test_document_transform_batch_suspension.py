from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Unpack, override
import uuid

from app.document_transform.schemas import DocumentTransformRequest, DocumentTransformResult
from app.document_transform.task_executor import (
    DocumentTransformRuntime,
    DriveDocumentTransformTaskExecutor,
)
from app.identifiers import ModelBatchJobId, ModelCallId, ModelId, TaskId, UserId
from app.model_batches.ledger import MissedModelCall, ModelBatchPendingError, ModelCallLedger
from app.model_batches.requests import ModelRequest
from app.model_batches.service import (
    JobAdvance,
    JobStillRunning,
    ModelBatchBudgetExhausted,
    ModelBatchCoordinator,
    ModelBatchStore,
    SuspendedForModelBatch,
    SuspensionResult,
)
from app.model_batches.states import (
    FailedModelBatchJob,
    IngestedModelBatchJob,
    ModelBatchJob,
    ModelOutcome,
    PreparedModelBatchJob,
    RunningModelBatchJob,
    SubmittingModelBatchJob,
)
from app.model_batches.transport import BatchModelTransport
from app.tasks.schemas import RunningTaskRead, TaskRead
from tests.support import dependency
from tests.test_document_transform_task_executor import (
    _claim,
    _RetryTasks,
    _RuntimeArguments,
)


_MISS = MissedModelCall(
    ModelRequest(
        operation="unslop",
        model=ModelId("gemini-3.1-pro-preview"),
        contents="{}",
        system_instruction="Edit.",
        response_json_schema={"type": "object"},
        max_output_tokens=1024,
        thinking_level="HIGH",
    ),
    1,
)


class _AwaitingTasks(_RetryTasks):
    async def get_for_owner(self, task_id: TaskId, _owner_user_id: UserId) -> TaskRead:
        task = _claim(task_id, attempt_count=1).task
        return task.model_copy(update={"execution_state": "awaiting_model"})


@dataclass
class _Store(ModelBatchStore):
    result: SuspensionResult
    suspended: list[tuple[MissedModelCall, ...]] = field(default_factory=list)

    @override
    async def load_ledger(self, task_id: TaskId) -> ModelCallLedger:
        return ModelCallLedger({})

    @override
    async def suspend(
        self, task_id: TaskId, *, lease_token: uuid.UUID, misses: Sequence[MissedModelCall]
    ) -> SuspensionResult:
        self.suspended.append(tuple(misses))
        return self.result

    @override
    async def job(self, job_id: ModelBatchJobId) -> ModelBatchJob | None:
        raise NotImplementedError

    @override
    async def queued_requests(
        self, job_id: ModelBatchJobId
    ) -> list[tuple[ModelCallId, ModelRequest]]:
        raise NotImplementedError

    @override
    async def transition(self, current: ModelBatchJob, following: ModelBatchJob) -> None:
        raise NotImplementedError

    @override
    async def ingest(
        self,
        current: RunningModelBatchJob,
        following: IngestedModelBatchJob,
        outcomes: Mapping[str, ModelOutcome],
    ) -> None:
        raise NotImplementedError

    @override
    async def fail(
        self,
        current: SubmittingModelBatchJob | RunningModelBatchJob,
        following: FailedModelBatchJob,
    ) -> None:
        raise NotImplementedError

    @override
    async def resume(self, task_id: TaskId) -> datetime | None:
        raise NotImplementedError

    @override
    async def stale_ready_tasks(self, *, limit: int) -> list[tuple[TaskId, datetime]]:
        raise NotImplementedError

    @override
    async def stale_active_jobs(self, *, limit: int) -> list[ModelBatchJobId]:
        raise NotImplementedError

    @override
    async def expire_awaiting_tasks(self, *, limit: int) -> int:
        raise NotImplementedError


class _Coordinator(ModelBatchCoordinator):
    def __init__(self) -> None:
        self.advanced: list[ModelBatchJobId] = []

    @override
    async def advance(self, job_id: ModelBatchJobId) -> JobAdvance:
        self.advanced.append(job_id)
        return JobStillRunning(job_id)


class _PendingTransform:
    async def execute(
        self, *, operation_id: uuid.UUID, request: DocumentTransformRequest
    ) -> DocumentTransformResult:
        raise ModelBatchPendingError((_MISS,))


class _BatchWorker(DriveDocumentTransformTaskExecutor):
    def __init__(self, tasks: _RetryTasks, transport: BatchModelTransport) -> None:
        self._tasks = dependency(tasks)
        self._in_place_enabled = True
        self._model_transport = transport

    @override
    async def _runtime(
        self,
        owner_user_id: uuid.UUID,
        payload: DocumentTransformRequest,
        **_kwargs: Unpack[_RuntimeArguments],
    ) -> DocumentTransformRuntime:
        return DocumentTransformRuntime(transform=dependency(_PendingTransform()))


def _executor(
    tasks: _RetryTasks, store: _Store, coordinator: _Coordinator
) -> DriveDocumentTransformTaskExecutor:
    return _BatchWorker(tasks, BatchModelTransport(store=store, coordinator=coordinator))


def _job() -> PreparedModelBatchJob:
    return PreparedModelBatchJob(
        id=ModelBatchJobId(uuid.uuid7()),
        task_id=TaskId(uuid.uuid7()),
        model=ModelId("gemini-3.1-pro-preview"),
    )


async def test_replay_miss_suspends_the_task_and_submits_each_job() -> None:
    jobs = (_job(), _job())
    store = _Store(result=SuspendedForModelBatch(jobs=jobs))
    coordinator = _Coordinator()
    tasks = _AwaitingTasks()

    result = await _executor(tasks, store, coordinator).execute(TaskId(uuid.uuid7()))

    assert isinstance(result, RunningTaskRead)
    assert result.execution_state == "awaiting_model"
    assert store.suspended == [(_MISS,)]
    assert coordinator.advanced == [job.id for job in jobs]
    assert tasks.deferred == 0
    assert tasks.failed == 0


async def test_exhausted_batch_budget_fails_while_holding_the_lease() -> None:
    store = _Store(result=ModelBatchBudgetExhausted(failed_jobs=3))
    coordinator = _Coordinator()
    tasks = _AwaitingTasks()

    await _executor(tasks, store, coordinator).execute(TaskId(uuid.uuid7()))

    assert tasks.failed == 1
    assert tasks.last_error_code == "model_batch_failed"
    assert coordinator.advanced == []
