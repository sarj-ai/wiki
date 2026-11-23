from __future__ import annotations

from typing import Literal
import uuid

from google.genai import types as genai_types
from pydantic import ValidationError
import pytest

from app.document_transform.errors import OutputConflictError
from app.identifiers import (
    ModelBatchJobId,
    ModelCallId,
    ModelId,
    ModelRequestHash,
    TaskId,
    VertexBatchJobName,
)
from app.model_batches.states import (
    FailedModelBatchJob,
    FailedModelCall,
    FailedModelOutcome,
    IngestedModelBatchJob,
    ModelCallFailure,
    PendingModelCall,
    PreparedModelBatchJob,
    ProviderJobActive,
    ProviderJobSucceeded,
    ProviderJobTerminated,
    QueuedModelCall,
    RunningModelBatchJob,
    SubmittingModelBatchJob,
    SucceededModelCall,
    SucceededModelOutcome,
    begin_submission,
    fail_job,
    ingest_job,
    provider_job_state,
    queue_call,
    record_submission,
    recover_submission,
    requeue_call,
    resolve_call,
)


_TASK = TaskId(uuid.uuid7())
_MODEL = ModelId("gemini-3.1-pro-preview")
_NAME = VertexBatchJobName("projects/p/locations/global/batchPredictionJobs/1")


def _pending() -> PendingModelCall:
    return PendingModelCall(
        id=ModelCallId(uuid.uuid7()),
        task_id=_TASK,
        request_sha256=ModelRequestHash("a" * 64),
        occurrence=1,
        model=_MODEL,
    )


def _prepared() -> PreparedModelBatchJob:
    return PreparedModelBatchJob(id=ModelBatchJobId(uuid.uuid7()), task_id=_TASK, model=_MODEL)


def test_model_call_moves_pending_queued_succeeded() -> None:
    job_id = ModelBatchJobId(uuid.uuid7())
    queued = queue_call(_pending(), job_id)
    succeeded = resolve_call(
        queued, job_id=job_id, outcome=SucceededModelOutcome(response={"candidates": []})
    )

    assert isinstance(queued, QueuedModelCall)
    assert isinstance(succeeded, SucceededModelCall)
    assert succeeded.job_id == job_id


def test_model_call_records_failure_outcome() -> None:
    job_id = ModelBatchJobId(uuid.uuid7())
    failed = resolve_call(
        queue_call(_pending(), job_id),
        job_id=job_id,
        outcome=FailedModelOutcome(failure=ModelCallFailure(code=429, message="quota")),
    )

    assert isinstance(failed, FailedModelCall)
    assert failed.outcome.failure.code == 429


def test_model_call_rejects_outcome_from_another_job() -> None:
    queued = queue_call(_pending(), ModelBatchJobId(uuid.uuid7()))

    with pytest.raises(OutputConflictError):
        resolve_call(
            queued,
            job_id=ModelBatchJobId(uuid.uuid7()),
            outcome=SucceededModelOutcome(response={}),
        )


def test_requeued_call_drops_its_job() -> None:
    job_id = ModelBatchJobId(uuid.uuid7())

    pending = requeue_call(queue_call(_pending(), job_id), job_id=job_id)

    assert isinstance(pending, PendingModelCall)
    assert pending.job_id is None


@pytest.mark.parametrize(
    "fields",
    [
        {"status": "pending", "job_id": uuid.uuid7()},
        {"status": "queued", "job_id": None},
        {"status": "succeeded", "outcome": None},
    ],
)
def test_call_variants_reject_contradictory_fields(
    fields: dict[str, str | uuid.UUID | None],
) -> None:
    variant = {
        "pending": PendingModelCall,
        "queued": QueuedModelCall,
        "succeeded": SucceededModelCall,
    }[str(fields["status"])]

    with pytest.raises(ValidationError):
        variant.model_validate({**_pending().model_dump(), **fields})


def test_batch_job_moves_prepared_submitting_running_ingested() -> None:
    submitting = begin_submission(_prepared())
    running = record_submission(submitting, _NAME)
    ingested = ingest_job(
        running, ProviderJobSucceeded(partial=True, output_directory="gs://bucket/out")
    )

    assert isinstance(submitting, SubmittingModelBatchJob)
    assert isinstance(running, RunningModelBatchJob)
    assert isinstance(ingested, IngestedModelBatchJob)
    assert ingested.provider_state == "partially_succeeded"


def test_interrupted_submission_recovers_found_job_or_retries() -> None:
    submitting = begin_submission(_prepared())

    assert isinstance(recover_submission(submitting, _NAME), RunningModelBatchJob)
    assert isinstance(recover_submission(submitting, None), PreparedModelBatchJob)


def test_only_a_submission_can_be_rejected() -> None:
    submitting = begin_submission(_prepared())
    running = record_submission(submitting, _NAME)

    rejected = fail_job(submitting, "rejected")

    assert isinstance(rejected, FailedModelBatchJob)
    assert rejected.vertex_job_name is None
    with pytest.raises(OutputConflictError):
        fail_job(running, "rejected")
    with pytest.raises(OutputConflictError):
        fail_job(submitting, "expired")


def test_running_job_requires_a_provider_name() -> None:
    with pytest.raises(ValidationError):
        RunningModelBatchJob.model_validate(
            {**_prepared().model_dump(), "status": "running", "vertex_job_name": None}
        )


_ACTIVE = (
    genai_types.JobState.JOB_STATE_QUEUED,
    genai_types.JobState.JOB_STATE_PENDING,
    genai_types.JobState.JOB_STATE_RUNNING,
    genai_types.JobState.JOB_STATE_UPDATING,
    genai_types.JobState.JOB_STATE_PAUSED,
    genai_types.JobState.JOB_STATE_CANCELLING,
)


@pytest.mark.parametrize("state", _ACTIVE)
def test_provider_active_states(state: genai_types.JobState) -> None:
    assert isinstance(provider_job_state(genai_types.BatchJob(state=state)), ProviderJobActive)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (genai_types.JobState.JOB_STATE_FAILED, "failed"),
        (genai_types.JobState.JOB_STATE_CANCELLED, "cancelled"),
        (genai_types.JobState.JOB_STATE_EXPIRED, "expired"),
    ],
)
def test_provider_terminal_states(
    state: genai_types.JobState, expected: Literal["failed", "cancelled", "expired"]
) -> None:
    observed = provider_job_state(genai_types.BatchJob(state=state))

    assert observed == ProviderJobTerminated(state=expected)


def test_provider_success_requires_output_directory() -> None:
    job = genai_types.BatchJob(state=genai_types.JobState.JOB_STATE_SUCCEEDED)

    with pytest.raises(ValueError, match="output directory"):
        provider_job_state(job)

    finished = provider_job_state(
        job.model_copy(
            update={
                "output_info": genai_types.BatchJobOutputInfo(
                    gcs_output_directory="gs://bucket/model-batches/x/output/prediction-1"
                )
            }
        )
    )
    assert finished == ProviderJobSucceeded(
        partial=False, output_directory="gs://bucket/model-batches/x/output/prediction-1"
    )


def test_provider_unspecified_state_is_rejected() -> None:
    with pytest.raises(ValueError, match="unspecified"):
        provider_job_state(genai_types.BatchJob(state=genai_types.JobState.JOB_STATE_UNSPECIFIED))
