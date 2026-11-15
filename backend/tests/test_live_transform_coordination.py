import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import override
import uuid

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.document_transform.errors import ModelDeadlineError, ModelUnavailableError
from app.document_transform.model_failures import exhausted_model_failure
from app.document_transform.schemas import (
    DocumentTransformCheckpoint,
    DocumentTransformRequest,
    DocumentTransformResult,
)
from app.document_transform.task_executor import (
    DocumentTransformRuntime,
    DriveDocumentTransformTaskExecutor,
)
from app.identifiers import FileId, GoogleUserId, IdempotencyKey, TaskId, UserId
from app.models.task import Task
from app.models.user import User
from app.tasks.errors import TaskExecutionDeferredError, TaskIdempotencyConflictError
from app.tasks.schemas import ActiveDocumentTransformTaskRecord, TaskKind, TaskRead, TaskStatus
from app.tasks.service import PostgresTaskService, TaskService
from tests import test_document_model_checkpoint as model_checkpoint_tests
from tests.test_task_source_dedupe_postgres import _completed_result, _markdown_payload, _qa_engine


@dataclass(frozen=True)
class _CoordinationCase:
    sessions: async_sessionmaker[AsyncSession]
    owner: UserId
    task_id: TaskId
    key: IdempotencyKey
    request: DocumentTransformRequest
    engine: AsyncEngine


@pytest.fixture
async def coordination_case() -> AsyncIterator[_CoordinationCase]:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = UserId(uuid.uuid7())
    key = IdempotencyKey(uuid.uuid7())
    request = _markdown_payload()
    try:
        async with sessions() as session:
            session.add(
                User(id=owner, email=f"qa-{owner}@sarj.ai", google_id=GoogleUserId(str(owner)))
            )
            await session.commit()
            reservation = await PostgresTaskService(db=session).create_or_replay(
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                owner_user_id=owner,
                idempotency_key=key,
                payload=request,
            )
        yield _CoordinationCase(sessions, owner, reservation.task.id, key, request, engine)
    finally:
        async with sessions() as session:
            await session.execute(delete(User).where(User.id == owner))
            await session.commit()
        await engine.dispose()


class _MutationProbe:
    def __init__(self) -> None:
        self.calls = 0
        self.failure: ModelUnavailableError | None = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def execute(
        self, *, operation_id: uuid.UUID, request: DocumentTransformRequest
    ) -> DocumentTransformResult:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        if self.failure is not None:
            raise self.failure
        return _completed_result(operation_id, request.mode)


@pytest.fixture
def mutation_probe() -> _MutationProbe:
    return _MutationProbe()


class _ProbeWorker(DriveDocumentTransformTaskExecutor):
    def __init__(self, tasks: TaskService, probe: _MutationProbe) -> None:
        self._tasks = tasks
        self._in_place_enabled = True
        self._probe = probe

    @override
    async def _runtime(
        self,
        owner_user_id: uuid.UUID,
        payload: DocumentTransformRequest,
        *,
        task_id: TaskId,
        lease_token: uuid.UUID,
        checkpoint: DocumentTransformCheckpoint | None,
        checkpoint_generation: int,
    ) -> DocumentTransformRuntime:
        return DocumentTransformRuntime(transform=self._probe)


async def _deliver(case: _CoordinationCase, probe: _MutationProbe) -> TaskRead:
    async with case.sessions() as session:
        return await _ProbeWorker(PostgresTaskService(db=session), probe).execute(case.task_id)


async def test_25_duplicate_queue_delivery_executes_the_mutation_boundary_once(
    coordination_case: _CoordinationCase, mutation_probe: _MutationProbe
) -> None:
    first = await _deliver(coordination_case, mutation_probe)
    second = await _deliver(coordination_case, mutation_probe)
    assert first == second
    assert first.status is TaskStatus.SUCCEEDED
    assert first.attempt_count == 1
    assert mutation_probe.calls == 1


async def test_26_two_workers_race_one_task_and_only_one_reaches_mutation(
    coordination_case: _CoordinationCase, mutation_probe: _MutationProbe
) -> None:
    mutation_probe.release.clear()
    workers = [asyncio.create_task(_deliver(coordination_case, mutation_probe)) for _ in range(2)]
    try:
        async with asyncio.timeout(10):
            await mutation_probe.entered.wait()
            completed, pending = await asyncio.wait(workers, return_when=asyncio.FIRST_COMPLETED)
            assert len(completed) == len(pending) == 1
            assert isinstance(next(iter(completed)).exception(), TaskExecutionDeferredError)
            assert mutation_probe.calls == 1
            mutation_probe.release.set()
            result = await next(iter(pending))
            assert result.status is TaskStatus.SUCCEEDED
            assert result.attempt_count == 1
    finally:
        mutation_probe.release.set()
        await asyncio.gather(*workers, return_exceptions=True)
    assert mutation_probe.calls == 1


@pytest.mark.parametrize(
    "scenario", ["27-same-request", "28-different-request", "29-lost-response"]
)
async def test_admission_replay_conflict_and_lookup_keep_one_task(
    coordination_case: _CoordinationCase, scenario: str
) -> None:
    case = coordination_case
    async with case.sessions() as session:
        service = PostgresTaskService(db=session)
        match scenario:
            case "28-different-request":
                with pytest.raises(TaskIdempotencyConflictError):
                    await service.create_or_replay(
                        kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                        owner_user_id=case.owner,
                        idempotency_key=case.key,
                        payload=case.request.model_copy(
                            update={"output_name": "A different request"}
                        ),
                    )
            case "29-lost-response":
                recovered = await service.get_admission(
                    case.owner, TaskKind.FILE_DOCUMENT_TRANSFORM, case.key
                )
                assert recovered.task_id == case.task_id
            case _:
                replayed = await service.create_or_replay(
                    kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                    owner_user_id=case.owner,
                    idempotency_key=case.key,
                    payload=case.request,
                )
                assert replayed.task.id == case.task_id
        assert (
            await session.scalar(
                select(func.count()).select_from(Task).where(Task.owner_user_id == case.owner)
            )
            == 1
        )


async def test_30_owner_cancellation_before_execution_prevents_mutation_on_later_delivery(
    coordination_case: _CoordinationCase, mutation_probe: _MutationProbe
) -> None:
    async with coordination_case.sessions() as session:
        cancelled = await PostgresTaskService(db=session).cancel(
            coordination_case.task_id, owner_user_id=coordination_case.owner
        )
    delivered = await _deliver(coordination_case, mutation_probe)
    assert delivered == cancelled
    assert delivered.status is TaskStatus.CANCELLED
    assert mutation_probe.calls == 0
    assert not mutation_probe.entered.is_set()


async def test_exhausted_provider_budget_is_terminal_across_duplicate_queue_delivery(
    coordination_case: _CoordinationCase, mutation_probe: _MutationProbe
) -> None:
    mutation_probe.failure = exhausted_model_failure(ModelUnavailableError(retry_after=60))
    first = await _deliver(coordination_case, mutation_probe)
    second = await _deliver(coordination_case, mutation_probe)
    assert first == second
    assert first.status is TaskStatus.FAILED
    assert first.error_code == "model_unavailable"
    assert first.attempt_count == 1
    assert mutation_probe.calls == 1


@pytest.mark.parametrize("has_resource", [False, True])
async def test_model_checkpoint_failure_reports_only_evidenced_external_effects(
    coordination_case: _CoordinationCase, *, has_resource: bool
) -> None:
    case = coordination_case
    async with case.sessions() as session:
        service = PostgresTaskService(db=session)
        reservation = await service.create_or_replay(
            kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
            owner_user_id=case.owner,
            idempotency_key=IdempotencyKey(uuid.uuid7()),
            payload=model_checkpoint_tests._request(),
        )
        task_id = reservation.task.id
        claim = await service.claim(task_id, lease_duration=timedelta(minutes=1))
        await service.save_document_transform_checkpoint(
            task_id,
            lease_token=claim.lease_token,
            expected_generation=0,
            checkpoint=await model_checkpoint_tests._saved_plan(),
        )
        if has_resource:
            await service.record_resource(
                task_id, lease_token=claim.lease_token, resource_id=FileId("qa-unexpected-output")
            )
        result = await service.fail(
            task_id,
            lease_token=claim.lease_token,
            error_code=ModelDeadlineError.code,
            error_detail=ModelDeadlineError.public_detail,
        )
        assert result.status is (TaskStatus.NEEDS_REVIEW if has_resource else TaskStatus.FAILED)
        assert result.error_code == (
            "external_effect_uncertain" if has_resource else ModelDeadlineError.code
        )


class _CheckpointCommitSession(AsyncSession):
    commit_gate: asyncio.Event | None = None
    commit_started: asyncio.Event

    @override
    async def commit(self) -> None:
        if self.commit_gate is not None:
            self.commit_started.set()
            await self.commit_gate.wait()
        await super().commit()


async def test_cancelled_checkpoint_transaction_rolls_back_before_owner_defer(
    coordination_case: _CoordinationCase,
) -> None:
    case = coordination_case
    sessions = async_sessionmaker(
        case.engine, class_=_CheckpointCommitSession, expire_on_commit=False
    )
    async with sessions() as session:
        service = PostgresTaskService(db=session)
        reservation = await service.create_or_replay(
            kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
            owner_user_id=case.owner,
            idempotency_key=IdempotencyKey(uuid.uuid7()),
            payload=model_checkpoint_tests._request(),
        )
        task_id = reservation.task.id
        claim = await service.claim(task_id, lease_duration=timedelta(minutes=1))
        checkpoint = await model_checkpoint_tests._saved_plan()
        session.commit_started = asyncio.Event()
        session.commit_gate = asyncio.Event()
        saving = asyncio.create_task(
            service.save_document_transform_checkpoint(
                task_id,
                lease_token=claim.lease_token,
                expected_generation=0,
                checkpoint=checkpoint,
            )
        )
        await asyncio.wait_for(session.commit_started.wait(), timeout=2)
        saving.cancel()
        with pytest.raises(asyncio.CancelledError):
            await saving
        session.commit_gate = None
        deferred = await service.defer(task_id, lease_token=claim.lease_token)
        record = await service.load_document_transform(task_id)
        assert isinstance(record, ActiveDocumentTransformTaskRecord)
        assert deferred.finished_at is None
        assert record.checkpoint_generation == 0
        assert record.checkpoint is None
        assert record.task.resource_id is None
        resumed = await service.claim(task_id, lease_duration=timedelta(minutes=1))
        assert resumed.task.attempt_count == 2
        assert resumed.lease_token != claim.lease_token
