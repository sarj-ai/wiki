from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from http import HTTPStatus
import json
import re
from typing import TYPE_CHECKING, ClassVar, Literal, TypedDict, assert_never, override
import uuid

from google.api_core.exceptions import GoogleAPICallError
from google.auth.exceptions import GoogleAuthError
from google.genai import errors as genai_errors
from loguru import logger
from pydantic import JsonValue, RootModel
from sqlalchemy import and_, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.auth.encryption import decrypt_cache, encrypt_cache
from app.document_transform.errors import OutputConflictError
from app.identifiers import (
    ModelBatchJobId,
    ModelCallId,
    ModelId,
    ModelRequestHash,
    TaskId,
)
from app.model_batches.gateway import ModelBatchSubmissionRejectedError
from app.model_batches.ledger import ModelCallLedger
from app.model_batches.requests import ModelRequest, batch_line_call_id
from app.model_batches.states import (
    MAX_FAILED_JOBS_PER_TASK,
    FailedModelBatchJob,
    FailedModelOutcome,
    IngestedModelBatchJob,
    ModelBatchJob,
    ModelCall,
    ModelCallFailure,
    ModelOutcome,
    PreparedModelBatchJob,
    ProviderJobActive,
    ProviderJobSucceeded,
    ProviderJobTerminated,
    RunningModelBatchJob,
    SubmittingModelBatchJob,
    SucceededModelOutcome,
    begin_submission,
    fail_job,
    ingest_job,
    record_submission,
    recover_submission,
)
from app.models.model_batch import ModelBatchJobRow, ModelCallRow
from app.models.task import Task
from app.tasks.errors import TaskEnqueueError, TaskLeaseLostError
from app.tasks.schemas import TaskKind, TaskStatus


if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.model_batches.gateway import ModelBatchGateway
    from app.model_batches.ledger import MissedModelCall
    from app.tasks.queue import TaskEnqueuer


class ReconcileReport(TypedDict):
    expired_tasks: int
    advanced_jobs: int


# Vertex AI's batch SLA is 24 hours; keep a margin for queueing and ingestion.
AWAITING_MODEL_BUDGET = timedelta(hours=26)
_STALE_ACTIVE_JOB = timedelta(minutes=2)
_ACTIVE_JOB_STATUSES = ("prepared", "submitting", "running")
_HTTP_STATUSES = frozenset(status.value for status in HTTPStatus)
# Failures that leave a durable job for the next event or reconcile to advance.
RECOVERABLE_BATCH_ERRORS = (
    genai_errors.APIError,
    GoogleAPICallError,
    GoogleAuthError,
    OutputConflictError,
    TaskEnqueueError,
    OSError,
    TimeoutError,
    ValueError,
)
_MISSING_OUTPUT = ModelCallFailure(code=500, message="The request was absent from batch output")
_LEADING_STATUS = re.compile(r"^\s*(\d{3})\b")


class _StoredCall(RootModel[ModelCall]):
    pass


class _StoredJob(RootModel[ModelBatchJob]):
    pass


class _StoredOutcome(RootModel[ModelOutcome]):
    pass


@dataclass(frozen=True, slots=True)
class SuspendedForModelBatch:
    kind: ClassVar[Literal["suspended"]] = "suspended"
    jobs: tuple[PreparedModelBatchJob, ...]


@dataclass(frozen=True, slots=True)
class ModelBatchBudgetExhausted:
    kind: ClassVar[Literal["budget_exhausted"]] = "budget_exhausted"
    failed_jobs: int


type SuspensionResult = SuspendedForModelBatch | ModelBatchBudgetExhausted


@dataclass(frozen=True, slots=True)
class JobStillRunning:
    kind: ClassVar[Literal["still_running"]] = "still_running"
    job_id: ModelBatchJobId


@dataclass(frozen=True, slots=True)
class JobSettled:
    kind: ClassVar[Literal["settled"]] = "settled"
    job_id: ModelBatchJobId
    task_resumed: bool


@dataclass(frozen=True, slots=True)
class JobUnknown:
    kind: ClassVar[Literal["unknown"]] = "unknown"
    job_id: ModelBatchJobId


type JobAdvance = JobStillRunning | JobSettled | JobUnknown


def outcomes_from_output(lines: Sequence[dict[str, JsonValue]]) -> dict[str, ModelOutcome]:
    outcomes: dict[str, ModelOutcome] = {}
    for line in lines:
        call_id = batch_line_call_id(line)
        if call_id is None:
            continue
        response = line.get("response")
        status = line.get("status")
        if isinstance(response, dict) and not status:
            outcomes[call_id] = SucceededModelOutcome(response=response)
        else:
            outcomes[call_id] = FailedModelOutcome(failure=_line_failure(status))
    return outcomes


def _line_failure(status: JsonValue) -> ModelCallFailure:
    message = status if isinstance(status, str) else json.dumps(status)
    code = 500
    if isinstance(status, str):
        try:
            parsed = json.loads(status)
        except ValueError:
            parsed = None
        error = parsed.get("error") if isinstance(parsed, dict) else None
        if isinstance(error, dict) and isinstance(error.get("code"), int):
            code = int(error["code"])
        elif (match := _LEADING_STATUS.match(status)) is not None:
            code = int(match.group(1))
    if code not in _HTTP_STATUSES:
        code = HTTPStatus.INTERNAL_SERVER_ERROR
    return ModelCallFailure(code=code, message=message[:2_000])


class ModelBatchStore(ABC):
    @abstractmethod
    async def load_ledger(self, task_id: TaskId) -> ModelCallLedger: ...

    @abstractmethod
    async def suspend(
        self, task_id: TaskId, *, lease_token: uuid.UUID, misses: Sequence[MissedModelCall]
    ) -> SuspensionResult: ...

    @abstractmethod
    async def job(self, job_id: ModelBatchJobId) -> ModelBatchJob | None: ...

    @abstractmethod
    async def queued_requests(
        self, job_id: ModelBatchJobId
    ) -> list[tuple[ModelCallId, ModelRequest]]: ...

    @abstractmethod
    async def transition(self, current: ModelBatchJob, following: ModelBatchJob) -> None: ...

    @abstractmethod
    async def ingest(
        self,
        current: RunningModelBatchJob,
        following: IngestedModelBatchJob,
        outcomes: Mapping[str, ModelOutcome],
    ) -> None: ...

    @abstractmethod
    async def fail(
        self,
        current: SubmittingModelBatchJob | RunningModelBatchJob,
        following: FailedModelBatchJob,
    ) -> None: ...

    @abstractmethod
    async def resume(self, task_id: TaskId) -> datetime | None: ...

    @abstractmethod
    async def stale_ready_tasks(self, *, limit: int) -> list[tuple[TaskId, datetime]]: ...

    @abstractmethod
    async def stale_active_jobs(self, *, limit: int) -> list[ModelBatchJobId]: ...

    @abstractmethod
    async def expire_awaiting_tasks(self, *, limit: int) -> int: ...


class PostgresModelBatchStore(ModelBatchStore):
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    @override
    async def load_ledger(self, task_id: TaskId) -> ModelCallLedger:
        rows = (
            await self._db.execute(select(ModelCallRow).where(ModelCallRow.task_id == task_id))
        ).scalars()
        calls = {(row.request_sha256, row.occurrence): _call(row) for row in rows}
        return ModelCallLedger(calls)

    @override
    async def suspend(
        self, task_id: TaskId, *, lease_token: uuid.UUID, misses: Sequence[MissedModelCall]
    ) -> SuspensionResult:
        try:
            result = await self._suspend(task_id, lease_token=lease_token, misses=misses)
        except BaseException:
            await self._db.rollback()
            raise
        match result:
            case ModelBatchBudgetExhausted():
                await self._db.rollback()
            case SuspendedForModelBatch():
                await self._db.commit()
            case unreachable:
                assert_never(unreachable)
        return result

    async def _suspend(
        self, task_id: TaskId, *, lease_token: uuid.UUID, misses: Sequence[MissedModelCall]
    ) -> SuspensionResult:
        failed_jobs = (
            await self._db.execute(
                select(func.count())
                .select_from(ModelBatchJobRow)
                .where(ModelBatchJobRow.task_id == task_id, ModelBatchJobRow.status == "failed")
            )
        ).scalar_one()
        if failed_jobs >= MAX_FAILED_JOBS_PER_TASK:
            return ModelBatchBudgetExhausted(failed_jobs=failed_jobs)
        await self._release_lease_for_model(task_id, lease_token)
        for miss in misses:
            await self._db.execute(
                insert(ModelCallRow)
                .values(
                    id=uuid.uuid7(),
                    task_id=task_id,
                    request_sha256=miss.request.request_sha256,
                    occurrence=miss.occurrence,
                    model=miss.request.model,
                    status="pending",
                    request_ciphertext=encrypt_cache(miss.request.model_dump_json()),
                )
                .on_conflict_do_nothing(constraint="uq_model_calls_task_request")
            )
        return SuspendedForModelBatch(jobs=await self._prepare_jobs(task_id))

    async def _release_lease_for_model(self, task_id: TaskId, lease_token: uuid.UUID) -> None:
        suspended = (
            await self._db.execute(
                update(Task)
                .where(
                    Task.id == task_id,
                    Task.kind == TaskKind.FILE_DOCUMENT_TRANSFORM,
                    Task.status == TaskStatus.RUNNING,
                    Task.lease_token.is_not_distinct_from(lease_token),
                )
                .values(
                    status=TaskStatus.AWAITING_MODEL,
                    lease_token=None,
                    lease_expires_at=None,
                    expires_at=func.greatest(Task.expires_at, func.now() + AWAITING_MODEL_BUDGET),
                    updated_at=func.now(),
                )
                .returning(Task.id)
            )
        ).scalar_one_or_none()
        if suspended is None:
            raise TaskLeaseLostError

    async def _prepare_jobs(self, task_id: TaskId) -> tuple[PreparedModelBatchJob, ...]:
        pending = (
            await self._db.execute(
                select(ModelCallRow.id, ModelCallRow.model)
                .where(ModelCallRow.task_id == task_id, ModelCallRow.status == "pending")
                .order_by(ModelCallRow.created_at, ModelCallRow.id)
                .with_for_update()
            )
        ).all()
        by_model: dict[ModelId, list[ModelCallId]] = defaultdict(list)
        for call_id, model in pending:
            by_model[model].append(call_id)
        jobs: list[PreparedModelBatchJob] = []
        for model, call_ids in by_model.items():
            job = PreparedModelBatchJob(
                id=ModelBatchJobId(uuid.uuid7()), task_id=task_id, model=model
            )
            self._db.add(
                ModelBatchJobRow(id=job.id, task_id=task_id, model=model, status=job.status)
            )
            await self._db.flush()
            await self._db.execute(
                update(ModelCallRow)
                .where(ModelCallRow.id.in_(call_ids), ModelCallRow.status == "pending")
                .values(status="queued", job_id=job.id, updated_at=func.now())
            )
            jobs.append(job)
        return tuple(jobs)

    @override
    async def job(self, job_id: ModelBatchJobId) -> ModelBatchJob | None:
        row = await self._db.get(ModelBatchJobRow, job_id)
        return None if row is None else _job(row)

    @override
    async def queued_requests(
        self, job_id: ModelBatchJobId
    ) -> list[tuple[ModelCallId, ModelRequest]]:
        rows = (
            await self._db.execute(
                select(ModelCallRow.id, ModelCallRow.request_ciphertext)
                .where(ModelCallRow.job_id == job_id, ModelCallRow.status == "queued")
                .order_by(ModelCallRow.id)
            )
        ).all()
        return [
            (call_id, ModelRequest.model_validate_json(decrypt_cache(ciphertext)))
            for call_id, ciphertext in rows
        ]

    @override
    async def transition(self, current: ModelBatchJob, following: ModelBatchJob) -> None:
        if current.id != following.id:
            raise OutputConflictError
        try:
            await self._compare_and_set(current, following)
        except BaseException:
            await self._db.rollback()
            raise
        await self._db.commit()

    @override
    async def ingest(
        self,
        current: RunningModelBatchJob,
        following: IngestedModelBatchJob,
        outcomes: Mapping[str, ModelOutcome],
    ) -> None:
        try:
            await self._compare_and_set(current, following)
            queued = (
                await self._db.execute(
                    select(ModelCallRow.id).where(
                        ModelCallRow.job_id == current.id, ModelCallRow.status == "queued"
                    )
                )
            ).scalars()
            for call_id in queued:
                outcome = outcomes.get(call_id.hex) or FailedModelOutcome(failure=_MISSING_OUTPUT)
                await self._db.execute(
                    update(ModelCallRow)
                    .where(ModelCallRow.id == call_id, ModelCallRow.status == "queued")
                    .values(
                        status=outcome.kind,
                        outcome_ciphertext=encrypt_cache(outcome.model_dump_json()),
                        updated_at=func.now(),
                    )
                )
        except BaseException:
            await self._db.rollback()
            raise
        await self._db.commit()

    @override
    async def fail(
        self,
        current: SubmittingModelBatchJob | RunningModelBatchJob,
        following: FailedModelBatchJob,
    ) -> None:
        try:
            await self._compare_and_set(current, following)
            # The calls return to pending; the resumed replay misses them again
            # and the next suspension submits them in a fresh job.
            await self._db.execute(
                update(ModelCallRow)
                .where(ModelCallRow.job_id == current.id, ModelCallRow.status == "queued")
                .values(status="pending", job_id=None, updated_at=func.now())
            )
        except BaseException:
            await self._db.rollback()
            raise
        await self._db.commit()

    @override
    async def resume(self, task_id: TaskId) -> datetime | None:
        try:
            ready_at = (
                await self._db.execute(
                    update(Task)
                    .where(
                        Task.id == task_id,
                        Task.status == TaskStatus.AWAITING_MODEL,
                        ~exists().where(
                            ModelBatchJobRow.task_id == task_id,
                            ModelBatchJobRow.status.in_(_ACTIVE_JOB_STATUSES),
                        ),
                    )
                    .values(
                        status=TaskStatus.MODEL_READY,
                        dispatch_ready_at=func.now(),
                        updated_at=func.now(),
                    )
                    .returning(Task.dispatch_ready_at)
                )
            ).scalar_one_or_none()
            if ready_at is None:
                # Idempotent: an already-ready task keeps its delivery name.
                ready_at = (
                    await self._db.execute(
                        select(Task.dispatch_ready_at).where(
                            Task.id == task_id, Task.status == TaskStatus.MODEL_READY
                        )
                    )
                ).scalar_one_or_none()
        except BaseException:
            await self._db.rollback()
            raise
        await self._db.commit()
        return ready_at

    @override
    async def stale_ready_tasks(self, *, limit: int) -> list[tuple[TaskId, datetime]]:
        rows = await self._db.execute(
            select(Task.id, Task.dispatch_ready_at)
            .where(
                Task.status == TaskStatus.MODEL_READY,
                Task.dispatch_ready_at <= func.now() - _STALE_ACTIVE_JOB,
            )
            .order_by(Task.dispatch_ready_at, Task.id)
            .limit(limit)
        )
        return [(task_id, ready_at) for task_id, ready_at in rows if ready_at is not None]

    @override
    async def stale_active_jobs(self, *, limit: int) -> list[ModelBatchJobId]:
        return list(
            (
                await self._db.execute(
                    select(ModelBatchJobRow.id)
                    .where(
                        ModelBatchJobRow.status.in_(_ACTIVE_JOB_STATUSES),
                        ModelBatchJobRow.updated_at <= func.now() - _STALE_ACTIVE_JOB,
                    )
                    .order_by(ModelBatchJobRow.updated_at, ModelBatchJobRow.id)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    @override
    async def expire_awaiting_tasks(self, *, limit: int) -> int:
        expired = (
            select(Task.id)
            .where(
                Task.status.in_((TaskStatus.AWAITING_MODEL, TaskStatus.MODEL_READY)),
                Task.expires_at <= func.now(),
            )
            .order_by(Task.expires_at, Task.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self._db.execute(
            update(Task)
            .where(Task.id.in_(expired))
            .values(
                status=TaskStatus.FAILED,
                error_code="model_batch_expired",
                error_detail="Gemini batch prediction did not finish in time. Start the polish again.",
                finished_at=func.now(),
                updated_at=func.now(),
            )
            .returning(Task.id)
            .execution_options(synchronize_session=False)
        )
        count = len(result.all())
        await self._db.commit()
        return count

    async def _compare_and_set(self, current: ModelBatchJob, following: ModelBatchJob) -> None:
        changed = (
            await self._db.execute(
                update(ModelBatchJobRow)
                .where(
                    and_(
                        ModelBatchJobRow.id == current.id,
                        ModelBatchJobRow.status == current.status,
                        ModelBatchJobRow.vertex_job_name.is_not_distinct_from(
                            current.vertex_job_name
                        ),
                    )
                )
                .values(
                    status=following.status,
                    vertex_job_name=following.vertex_job_name,
                    provider_state=following.provider_state,
                    updated_at=func.now(),
                )
                .returning(ModelBatchJobRow.id)
            )
        ).scalar_one_or_none()
        if changed is None:
            raise OutputConflictError


def _job(row: ModelBatchJobRow) -> ModelBatchJob:
    return _StoredJob.model_validate(
        {
            "id": row.id,
            "task_id": row.task_id,
            "model": row.model,
            "status": row.status,
            "vertex_job_name": row.vertex_job_name,
            "provider_state": row.provider_state,
        }
    ).root


def _call(row: ModelCallRow) -> ModelCall:
    outcome = (
        None
        if row.outcome_ciphertext is None
        else _StoredOutcome.model_validate_json(decrypt_cache(row.outcome_ciphertext)).root
    )
    return _StoredCall.model_validate(
        {
            "id": row.id,
            "task_id": row.task_id,
            "request_sha256": ModelRequestHash(row.request_sha256),
            "occurrence": row.occurrence,
            "model": row.model,
            "status": row.status,
            "job_id": row.job_id,
            "outcome": outcome,
        }
    ).root


class ModelBatchCoordinator:
    def __init__(
        self, *, store: ModelBatchStore, gateway: ModelBatchGateway, enqueuer: TaskEnqueuer
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._enqueuer = enqueuer

    async def advance(self, job_id: ModelBatchJobId) -> JobAdvance:
        job = await self._store.job(job_id)
        match job:
            case None:
                return JobUnknown(job_id)
            case PreparedModelBatchJob():
                return await self._after_submission(await self._submit(job))
            case SubmittingModelBatchJob():
                recovered = recover_submission(job, await self._gateway.find(job))
                await self._store.transition(job, recovered)
                match recovered:
                    case PreparedModelBatchJob():
                        return await self._after_submission(await self._submit(recovered))
                    case RunningModelBatchJob():
                        return JobStillRunning(job_id)
                    case unreachable:
                        assert_never(unreachable)
            case RunningModelBatchJob():
                return await self._observe(job)
            case IngestedModelBatchJob() | FailedModelBatchJob():
                return JobSettled(job_id, task_resumed=await self._resume_if_ready(job))
            case unreachable:
                assert_never(unreachable)

    async def _submit(
        self, job: PreparedModelBatchJob
    ) -> RunningModelBatchJob | FailedModelBatchJob:
        requests = await self._store.queued_requests(job.id)
        await self._gateway.write_input(
            job.id, [request.vertex_batch_line(call_id) for call_id, request in requests]
        )
        submitting = begin_submission(job)
        await self._store.transition(job, submitting)
        try:
            name = await self._gateway.create(submitting)
        except ModelBatchSubmissionRejectedError:
            failed = fail_job(submitting, "rejected")
            await self._store.fail(submitting, failed)
            logger.warning("model batch job rejected", model_batch_job_id=job.id)
            return failed
        running = record_submission(submitting, name)
        await self._store.transition(submitting, running)
        logger.info(
            "model batch job submitted",
            model_batch_job_id=job.id,
            task_id=job.task_id,
            model_id=job.model,
            model_batch_requests=len(requests),
        )
        return running

    async def _observe(self, job: RunningModelBatchJob) -> JobAdvance:
        observed = await self._gateway.observe(job)
        settled: IngestedModelBatchJob | FailedModelBatchJob
        match observed:
            case ProviderJobActive():
                return JobStillRunning(job.id)
            case ProviderJobSucceeded():
                outcomes = outcomes_from_output(
                    await self._gateway.read_output(observed.output_directory)
                )
                settled = ingest_job(job, observed)
                await self._store.ingest(job, settled, outcomes)
            case ProviderJobTerminated(state=state):
                settled = fail_job(job, state)
                await self._store.fail(job, settled)
            case unreachable:
                assert_never(unreachable)
        logger.info(
            "model batch job settled",
            model_batch_job_id=job.id,
            task_id=job.task_id,
            model_batch_state=settled.provider_state,
        )
        return JobSettled(job.id, task_resumed=await self._resume_if_ready(settled))

    async def _after_submission(
        self, job: RunningModelBatchJob | FailedModelBatchJob
    ) -> JobAdvance:
        match job:
            case RunningModelBatchJob():
                return JobStillRunning(job.id)
            case FailedModelBatchJob():
                return JobSettled(job.id, task_resumed=await self._resume_if_ready(job))
            case unreachable:
                assert_never(unreachable)

    async def _resume_if_ready(self, job: IngestedModelBatchJob | FailedModelBatchJob) -> bool:
        if (ready_at := await self._store.resume(job.task_id)) is None:
            return False
        await self._enqueuer.enqueue_document_transform(job.task_id, resumed_at=ready_at)
        return True

    async def reconcile(self, *, limit: int = 20) -> ReconcileReport:
        expired = await self._store.expire_awaiting_tasks(limit=limit)
        advanced = 0
        for job_id in await self._store.stale_active_jobs(limit=limit):
            try:
                await self.advance(job_id)
                advanced += 1
            except RECOVERABLE_BATCH_ERRORS:
                logger.exception("model batch reconciliation failed", model_batch_job_id=job_id)
        # Backstop for a lost resume delivery; the name is stable per resume.
        for task_id, ready_at in await self._store.stale_ready_tasks(limit=limit):
            await self._enqueuer.enqueue_document_transform(task_id, resumed_at=ready_at)
        return {"expired_tasks": expired, "advanced_jobs": advanced}
