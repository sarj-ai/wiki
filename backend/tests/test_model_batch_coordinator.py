from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import override
import uuid

from pydantic import JsonValue
import pytest

from app.document_transform.errors import OutputConflictError
from app.identifiers import (
    ModelBatchJobId,
    ModelCallId,
    ModelId,
    TaskId,
    VertexBatchJobName,
)
from app.model_batches.gateway import (
    ModelBatchGateway,
    ModelBatchSubmissionRejectedError,
    job_id_from_output_object,
)
from app.model_batches.ledger import MissedModelCall, ModelCallLedger
from app.model_batches.requests import ModelRequest
from app.model_batches.service import (
    JobSettled,
    JobStillRunning,
    JobUnknown,
    ModelBatchCoordinator,
    ModelBatchStore,
    SuspensionResult,
    outcomes_from_output,
)
from app.model_batches.states import (
    FailedModelBatchJob,
    FailedModelOutcome,
    IngestedModelBatchJob,
    ModelBatchJob,
    ModelOutcome,
    PreparedModelBatchJob,
    ProviderJobActive,
    ProviderJobState,
    ProviderJobSucceeded,
    ProviderJobTerminated,
    RunningModelBatchJob,
    SubmittingModelBatchJob,
    SucceededModelOutcome,
)
from app.tasks.queue import TaskEnqueuer


_TASK = TaskId(uuid.uuid7())
_READY_AT = datetime(2026, 11, 22, 12, tzinfo=UTC)
_NAME = VertexBatchJobName("projects/p/locations/global/batchPredictionJobs/7")
_OUTPUT = "gs://bucket/model-batches/job/output/prediction-1"
_REQUEST = ModelRequest(
    operation="unslop",
    model=ModelId("gemini-3.1-pro-preview"),
    contents="{}",
    system_instruction="Edit.",
    response_json_schema={"type": "object"},
    max_output_tokens=1024,
    thinking_level="HIGH",
)


@dataclass
class _Store(ModelBatchStore):
    jobs: dict[ModelBatchJobId, ModelBatchJob] = field(default_factory=dict)
    calls: dict[ModelCallId, ModelBatchJobId] = field(default_factory=dict)
    ingested: dict[ModelBatchJobId, Mapping[str, ModelOutcome]] = field(default_factory=dict)
    requeued: list[ModelBatchJobId] = field(default_factory=list)
    awaiting: bool = True
    ready_at: datetime | None = None

    @override
    async def load_ledger(self, task_id: TaskId) -> ModelCallLedger:
        return ModelCallLedger({})

    @override
    async def suspend(
        self, task_id: TaskId, *, lease_token: uuid.UUID, misses: Sequence[MissedModelCall]
    ) -> SuspensionResult:
        raise NotImplementedError

    @override
    async def job(self, job_id: ModelBatchJobId) -> ModelBatchJob | None:
        return self.jobs.get(job_id)

    @override
    async def queued_requests(
        self, job_id: ModelBatchJobId
    ) -> list[tuple[ModelCallId, ModelRequest]]:
        return [(call_id, _REQUEST) for call_id, owner in self.calls.items() if owner == job_id]

    @override
    async def transition(self, current: ModelBatchJob, following: ModelBatchJob) -> None:
        if self.jobs.get(current.id) != current:
            raise OutputConflictError
        self.jobs[current.id] = following

    @override
    async def ingest(
        self,
        current: RunningModelBatchJob,
        following: IngestedModelBatchJob,
        outcomes: Mapping[str, ModelOutcome],
    ) -> None:
        await self.transition(current, following)
        self.ingested[current.id] = outcomes

    @override
    async def fail(
        self,
        current: SubmittingModelBatchJob | RunningModelBatchJob,
        following: FailedModelBatchJob,
    ) -> None:
        await self.transition(current, following)
        self.requeued.append(current.id)

    @override
    async def resume(self, task_id: TaskId) -> datetime | None:
        if self.awaiting and all(
            isinstance(job, IngestedModelBatchJob | FailedModelBatchJob)
            for job in self.jobs.values()
        ):
            self.awaiting = False
            self.ready_at = _READY_AT
        return self.ready_at

    @override
    async def stale_ready_tasks(self, *, limit: int) -> list[tuple[TaskId, datetime]]:
        return [] if self.ready_at is None else [(_TASK, self.ready_at)]

    @override
    async def stale_active_jobs(self, *, limit: int) -> list[ModelBatchJobId]:
        return [
            job.id
            for job in self.jobs.values()
            if isinstance(
                job, PreparedModelBatchJob | SubmittingModelBatchJob | RunningModelBatchJob
            )
        ][:limit]

    @override
    async def expire_awaiting_tasks(self, *, limit: int) -> int:
        return 0


@dataclass
class _Gateway(ModelBatchGateway):
    observed: ProviderJobState = field(default_factory=ProviderJobActive)
    output: list[dict[str, JsonValue]] = field(default_factory=list)
    reject: bool = False
    found: VertexBatchJobName | None = None
    written: dict[ModelBatchJobId, int] = field(default_factory=dict)
    created: list[ModelBatchJobId] = field(default_factory=list)

    @override
    async def write_input(
        self, job_id: ModelBatchJobId, lines: Sequence[dict[str, JsonValue]]
    ) -> None:
        self.written[job_id] = len(lines)

    @override
    async def create(self, job: SubmittingModelBatchJob) -> VertexBatchJobName:
        self.created.append(job.id)
        if self.reject:
            raise ModelBatchSubmissionRejectedError
        return _NAME

    @override
    async def find(self, job: SubmittingModelBatchJob) -> VertexBatchJobName | None:
        return self.found

    @override
    async def observe(self, job: RunningModelBatchJob) -> ProviderJobState:
        return self.observed

    @override
    async def read_output(self, output_directory: str) -> list[dict[str, JsonValue]]:
        assert output_directory == _OUTPUT
        return self.output


@dataclass
class _Enqueuer(TaskEnqueuer):
    resumed: list[tuple[TaskId, datetime | None]] = field(default_factory=list)

    @override
    async def enqueue_smart_create(self, task_id: TaskId) -> None:
        raise NotImplementedError

    @override
    async def enqueue_document_transform(
        self, task_id: TaskId, *, resumed_at: datetime | None = None
    ) -> None:
        self.resumed.append((task_id, resumed_at))


@dataclass(frozen=True)
class _Harness:
    coordinator: ModelBatchCoordinator
    store: _Store
    gateway: _Gateway
    enqueuer: _Enqueuer


def _harness(job: ModelBatchJob, gateway: _Gateway | None = None) -> _Harness:
    store = _Store(jobs={job.id: job}, calls={ModelCallId(uuid.uuid7()): job.id})
    gateway = gateway or _Gateway()
    enqueuer = _Enqueuer()
    return _Harness(
        ModelBatchCoordinator(store=store, gateway=gateway, enqueuer=enqueuer),
        store,
        gateway,
        enqueuer,
    )


def _prepared() -> PreparedModelBatchJob:
    return PreparedModelBatchJob(
        id=ModelBatchJobId(uuid.uuid7()), task_id=_TASK, model=ModelId("gemini-3.1-pro-preview")
    )


def _running() -> RunningModelBatchJob:
    return RunningModelBatchJob.model_validate(
        {**_prepared().model_dump(), "status": "running", "vertex_job_name": _NAME}
    )


async def test_prepared_job_is_written_then_submitted() -> None:
    job = _prepared()
    harness = _harness(job)
    coordinator = harness.coordinator
    store = harness.store
    gateway = harness.gateway
    enqueuer = harness.enqueuer

    advance = await coordinator.advance(job.id)

    assert advance == JobStillRunning(job.id)
    assert gateway.written == {job.id: 1}
    assert isinstance(store.jobs[job.id], RunningModelBatchJob)
    assert enqueuer.resumed == []


async def test_rejected_submission_fails_the_job_and_resumes_the_task() -> None:
    job = _prepared()
    harness = _harness(job, _Gateway(reject=True))
    coordinator = harness.coordinator
    store = harness.store
    enqueuer = harness.enqueuer

    advance = await coordinator.advance(job.id)

    assert advance == JobSettled(job.id, task_resumed=True)
    assert isinstance(store.jobs[job.id], FailedModelBatchJob)
    assert store.requeued == [job.id]
    assert enqueuer.resumed == [(_TASK, _READY_AT)]


async def test_interrupted_submission_adopts_the_existing_provider_job() -> None:
    submitting = SubmittingModelBatchJob.model_validate(
        {**_prepared().model_dump(), "status": "submitting"}
    )
    harness = _harness(submitting, _Gateway(found=_NAME))
    coordinator = harness.coordinator
    store = harness.store
    gateway = harness.gateway

    advance = await coordinator.advance(submitting.id)

    assert advance == JobStillRunning(submitting.id)
    assert gateway.created == []
    assert store.jobs[submitting.id] == RunningModelBatchJob.model_validate(
        {**submitting.model_dump(), "status": "running", "vertex_job_name": _NAME}
    )


async def test_interrupted_submission_without_a_provider_job_resubmits() -> None:
    submitting = SubmittingModelBatchJob.model_validate(
        {**_prepared().model_dump(), "status": "submitting"}
    )
    harness = _harness(submitting)
    coordinator = harness.coordinator
    store = harness.store
    gateway = harness.gateway

    await coordinator.advance(submitting.id)

    assert gateway.created == [submitting.id]
    assert isinstance(store.jobs[submitting.id], RunningModelBatchJob)


async def test_active_provider_job_keeps_waiting() -> None:
    job = _running()
    harness = _harness(job)
    coordinator = harness.coordinator
    enqueuer = harness.enqueuer

    assert await coordinator.advance(job.id) == JobStillRunning(job.id)
    assert enqueuer.resumed == []


async def test_succeeded_job_is_ingested_and_resumes_the_task() -> None:
    job = _running()
    call_id = ModelCallId(uuid.uuid7())
    gateway = _Gateway(
        observed=ProviderJobSucceeded(partial=False, output_directory=_OUTPUT),
        output=[
            {
                "request": {"labels": {"wiki_call": call_id.hex}},
                "response": {"candidates": []},
                "status": "",
            }
        ],
    )
    harness = _harness(job, gateway)
    coordinator = harness.coordinator
    store = harness.store
    enqueuer = harness.enqueuer

    advance = await coordinator.advance(job.id)

    assert advance == JobSettled(job.id, task_resumed=True)
    assert isinstance(store.jobs[job.id], IngestedModelBatchJob)
    assert store.ingested[job.id] == {
        call_id.hex: SucceededModelOutcome(response={"candidates": []})
    }
    assert enqueuer.resumed == [(_TASK, _READY_AT)]


async def test_terminated_job_requeues_its_calls() -> None:
    job = _running()
    harness = _harness(job, _Gateway(observed=ProviderJobTerminated(state="expired")))
    coordinator = harness.coordinator
    store = harness.store

    await coordinator.advance(job.id)

    failed = store.jobs[job.id]
    assert isinstance(failed, FailedModelBatchJob)
    assert failed.provider_state == "expired"
    assert store.requeued == [job.id]


async def test_resume_waits_for_every_job_of_the_task() -> None:
    job = _running()
    other = _running()
    harness = _harness(
        job, _Gateway(observed=ProviderJobSucceeded(partial=False, output_directory=_OUTPUT))
    )
    coordinator = harness.coordinator
    store = harness.store
    enqueuer = harness.enqueuer
    store.jobs[other.id] = other

    assert await coordinator.advance(job.id) == JobSettled(job.id, task_resumed=False)
    assert enqueuer.resumed == []


async def test_unknown_job_is_reported() -> None:
    harness = _harness(_prepared())
    coordinator = harness.coordinator
    missing = ModelBatchJobId(uuid.uuid7())

    assert await coordinator.advance(missing) == JobUnknown(missing)


def test_output_lines_map_to_typed_outcomes() -> None:
    ok, quota, prefixed, unlabeled = (uuid.uuid7().hex for _ in range(4))

    outcomes = outcomes_from_output(
        [
            {"request": {"labels": {"wiki_call": ok}}, "response": {"candidates": []}},
            {
                "request": {"labels": {"wiki_call": quota}},
                "status": '{"error": {"code": 429, "message": "Resource exhausted"}}',
            },
            {"request": {"labels": {"wiki_call": prefixed}}, "status": "400 Bad Request"},
            {"request": {}, "response": {}},
        ]
    )

    assert outcomes[ok] == SucceededModelOutcome(response={"candidates": []})
    quota_outcome = outcomes[quota]
    prefixed_outcome = outcomes[prefixed]
    assert isinstance(quota_outcome, FailedModelOutcome)
    assert isinstance(prefixed_outcome, FailedModelOutcome)
    assert quota_outcome.failure.code == 429
    assert prefixed_outcome.failure.code == 400
    assert unlabeled not in outcomes


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("model-batches/0196f0a2-0000-7000-8000-000000000001/output/p-1/predictions.jsonl", True),
        ("model-batches/0196f0a2-0000-7000-8000-000000000001/input.jsonl", False),
        ("other/0196f0a2-0000-7000-8000-000000000001/output/p.jsonl", False),
    ],
)
def test_only_output_objects_route_to_a_job(name: str, *, expected: bool) -> None:
    assert (job_id_from_output_object(name) is not None) is expected


async def test_reconcile_redelivers_a_ready_task_under_the_same_name() -> None:
    job = _running()
    harness = _harness(job)
    coordinator = harness.coordinator
    store = harness.store
    enqueuer = harness.enqueuer
    store.ready_at = _READY_AT

    await coordinator.reconcile()

    assert enqueuer.resumed == [(_TASK, _READY_AT)]
