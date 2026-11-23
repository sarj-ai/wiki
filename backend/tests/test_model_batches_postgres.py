from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import uuid

from pydantic_settings import BaseSettings, SettingsConfigDict
import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.document_transform.schemas import (
    DocumentTransformRequest,
    MarkdownSource,
    TransformMode,
)
from app.identifiers import (
    FileId,
    GoogleUserId,
    IdempotencyKey,
    ModelId,
    TaskId,
    UserId,
    VertexBatchJobName,
)
from app.model_batches.ledger import MissedModelCall
from app.model_batches.requests import ModelRequest
from app.model_batches.service import (
    ModelBatchBudgetExhausted,
    PostgresModelBatchStore,
    SuspendedForModelBatch,
)
from app.model_batches.states import (
    MAX_FAILED_JOBS_PER_TASK,
    PreparedModelBatchJob,
    ProviderJobSucceeded,
    SucceededModelOutcome,
    begin_submission,
    ingest_job,
    record_submission,
)
from app.models.model_batch import ModelBatchJobRow, ModelCallRow
from app.models.task import Task
from app.models.user import User
from app.tasks.errors import TaskNotClaimableError
from app.tasks.schemas import RunningTaskRead, TaskKind, TaskStatus
from app.tasks.service import PostgresTaskService


class _QASettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WIKI_POLISH29_QA_")
    database_url: str | None = None


def _qa_engine() -> AsyncEngine:
    dsn = _QASettings().database_url
    if not dsn:
        pytest.skip("Requires an explicitly configured disposable PostgreSQL QA database")
    url = make_url(dsn)
    if (
        url.host not in {"127.0.0.1", "localhost", "::1"}
        or not (url.database or "").startswith("wiki_polish29_qa")
        or url.drivername != "postgresql+asyncpg"
    ):
        pytest.fail("Only a loopback PostgreSQL database named wiki_polish29_qa* is allowed")
    return create_async_engine(dsn, pool_size=4, max_overflow=0)


def _request(model: str, contents: str) -> ModelRequest:
    return ModelRequest(
        operation="unslop",
        model=ModelId(model),
        contents=contents,
        system_instruction="Edit.",
        response_json_schema={"type": "object"},
        max_output_tokens=1024,
        thinking_level="HIGH",
    )


def _payload() -> DocumentTransformRequest:
    return DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown",
            content="Plain synthetic content.",
            name="Batch",
            destination_folder_id=FileId("qa-folder"),
        ),
        mode=TransformMode.UNSLOP,
    )


@dataclass(frozen=True, slots=True)
class _Reserved:
    tasks: PostgresTaskService
    store: PostgresModelBatchStore
    task_id: TaskId
    idempotency_key: IdempotencyKey


async def _reserve(session: AsyncSession, owner: UserId) -> _Reserved:
    session.add(User(id=owner, email=f"qa-{owner}@sarj.ai", google_id=GoogleUserId(str(owner))))
    await session.commit()
    tasks = PostgresTaskService(db=session)
    idempotency_key = IdempotencyKey(uuid.uuid7())
    reservation = await tasks.create_or_replay(
        kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
        owner_user_id=owner,
        idempotency_key=idempotency_key,
        payload=_payload(),
    )
    return _Reserved(tasks, PostgresModelBatchStore(session), reservation.task.id, idempotency_key)


async def _assert_public_running(reserved: _Reserved, owner: UserId) -> None:
    awaiting = await reserved.tasks.get_for_owner(reserved.task_id, owner)
    assert isinstance(awaiting, RunningTaskRead)
    assert awaiting.execution_state == "awaiting_model"
    admission = await reserved.tasks.get_admission(
        owner, TaskKind.FILE_DOCUMENT_TRANSFORM, reserved.idempotency_key
    )
    assert admission.status is TaskStatus.RUNNING


async def test_real_postgres_suspension_blocks_claims_until_every_job_settles() -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = UserId(uuid.uuid7())
    try:
        async with sessions() as session:
            reserved = await _reserve(session, owner)
            tasks, store, task_id = reserved.tasks, reserved.store, reserved.task_id
            claim = await tasks.claim(task_id, lease_duration=timedelta(minutes=1))

            suspended = await store.suspend(
                task_id,
                lease_token=claim.lease_token,
                misses=(
                    MissedModelCall(_request("gemini-3.1-pro-preview", "a"), 1),
                    MissedModelCall(_request("gemini-4-argon", "b"), 1),
                ),
            )

            assert isinstance(suspended, SuspendedForModelBatch)
            assert {job.model for job in suspended.jobs} == {
                "gemini-3.1-pro-preview",
                "gemini-4-argon",
            }
            await _assert_public_running(reserved, owner)
            with pytest.raises(TaskNotClaimableError):
                await tasks.claim(task_id, lease_duration=timedelta(minutes=1))

            first, second = suspended.jobs
            (queued,) = await store.queued_requests(first.id)
            assert queued[1].model == first.model
            await _run_and_ingest(
                store, first, {queued[0].hex: SucceededModelOutcome(response={"candidates": []})}
            )
            assert await store.resume(task_id) is None
            with pytest.raises(TaskNotClaimableError):
                await tasks.claim(task_id, lease_duration=timedelta(minutes=1))

            # The second job omits its request from the output entirely.
            await _run_and_ingest(store, second, {})
            ready_at = await store.resume(task_id)
            assert ready_at is not None
            assert await store.resume(task_id) == ready_at

            resumed = await tasks.claim(task_id, lease_duration=timedelta(minutes=1))
            assert resumed.task.attempt_count == 1
            assert resumed.active_since > claim.active_since
            # A request absent from output is a retryable failure, never a
            # silently missing answer.
            statuses = await session.execute(
                select(ModelCallRow.status, ModelBatchJobRow.status)
                .join(ModelBatchJobRow, ModelBatchJobRow.id == ModelCallRow.job_id)
                .where(ModelCallRow.task_id == task_id)
            )
            assert sorted(tuple(row) for row in statuses) == [
                ("failed", "ingested"),
                ("succeeded", "ingested"),
            ]
    finally:
        async with sessions() as session:
            await session.execute(delete(User).where(User.id == owner))
            await session.commit()
        await engine.dispose()


async def test_real_postgres_failed_jobs_bound_resubmission() -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = UserId(uuid.uuid7())
    try:
        async with sessions() as session:
            session.add(
                User(id=owner, email=f"qa-{owner}@sarj.ai", google_id=GoogleUserId(str(owner)))
            )
            await session.commit()
            tasks = PostgresTaskService(db=session)
            store = PostgresModelBatchStore(session)
            reservation = await tasks.create_or_replay(
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                owner_user_id=owner,
                idempotency_key=IdempotencyKey(uuid.uuid7()),
                payload=_payload(),
            )
            task_id = reservation.task.id
            for index in range(MAX_FAILED_JOBS_PER_TASK):
                session.add(
                    ModelBatchJobRow(
                        id=uuid.uuid7(),
                        task_id=task_id,
                        model="gemini-3.1-pro-preview",
                        status="failed",
                        vertex_job_name=_name(index),
                        provider_state="failed",
                    )
                )
            await session.commit()
            claim = await tasks.claim(task_id, lease_duration=timedelta(minutes=1))

            result = await store.suspend(
                task_id,
                lease_token=claim.lease_token,
                misses=(MissedModelCall(_request("gemini-3.1-pro-preview", "a"), 1),),
            )

            assert result == ModelBatchBudgetExhausted(failed_jobs=MAX_FAILED_JOBS_PER_TASK)
            still_running = (
                await session.execute(select(Task.status).where(Task.id == task_id))
            ).scalar_one()
            assert still_running == TaskStatus.RUNNING
            assert not (
                await session.execute(
                    select(ModelCallRow.id).where(ModelCallRow.task_id == task_id)
                )
            ).first()
    finally:
        async with sessions() as session:
            await session.execute(delete(User).where(User.id == owner))
            await session.commit()
        await engine.dispose()


async def test_real_postgres_expired_suspension_fails_the_task() -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = UserId(uuid.uuid7())
    try:
        async with sessions() as session:
            session.add(
                User(id=owner, email=f"qa-{owner}@sarj.ai", google_id=GoogleUserId(str(owner)))
            )
            await session.commit()
            tasks = PostgresTaskService(db=session)
            store = PostgresModelBatchStore(session)
            reservation = await tasks.create_or_replay(
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                owner_user_id=owner,
                idempotency_key=IdempotencyKey(uuid.uuid7()),
                payload=_payload(),
            )
            task_id = reservation.task.id
            claim = await tasks.claim(task_id, lease_duration=timedelta(minutes=1))
            await store.suspend(
                task_id,
                lease_token=claim.lease_token,
                misses=(MissedModelCall(_request("gemini-3.1-pro-preview", "a"), 1),),
            )
            await session.execute(
                update(Task)
                .where(Task.id == task_id)
                .values(expires_at=Task.created_at + timedelta(microseconds=1))
            )
            await session.commit()

            assert await store.expire_awaiting_tasks(limit=10) == 1
            row = (
                await session.execute(
                    select(Task.status, Task.error_code).where(Task.id == task_id)
                )
            ).one()
            assert tuple(row) == (TaskStatus.FAILED, "model_batch_expired")
    finally:
        async with sessions() as session:
            await session.execute(delete(User).where(User.id == owner))
            await session.commit()
        await engine.dispose()


async def _run_and_ingest(
    store: PostgresModelBatchStore,
    job: PreparedModelBatchJob,
    outcomes: dict[str, SucceededModelOutcome],
) -> None:
    submitting = begin_submission(job)
    running = record_submission(submitting, _name(job.id.int % 1000))
    await store.transition(job, submitting)
    await store.transition(submitting, running)
    observed = ProviderJobSucceeded(partial=not outcomes, output_directory="gs://b/o")
    await store.ingest(running, ingest_job(running, observed), outcomes)


def _name(index: int) -> VertexBatchJobName:
    return VertexBatchJobName(f"projects/p/locations/global/batchPredictionJobs/{index}")
