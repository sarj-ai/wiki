from __future__ import annotations

from typing import Annotated, Literal, assert_never, overload

from google.genai import types as genai_types
from pydantic import BaseModel, ConfigDict, Field, JsonValue, PositiveInt

from app.document_transform.errors import OutputConflictError
from app.identifiers import (
    ModelBatchJobId,
    ModelCallId,
    ModelId,
    ModelRequestHash,
    TaskId,
    VertexBatchJobName,
)


MAX_FAILED_JOBS_PER_TASK = 3
_SHA256_PATTERN = r"^[a-f0-9]{64}$"


class _State(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )


class ModelCallFailure(_State):
    code: Annotated[int, Field(ge=100, le=599)]
    message: Annotated[str, Field(max_length=2_000)]


class SucceededModelOutcome(_State):
    kind: Literal["succeeded"] = "succeeded"
    response: dict[str, JsonValue]


class FailedModelOutcome(_State):
    kind: Literal["failed"] = "failed"
    failure: ModelCallFailure


type ModelOutcome = Annotated[
    SucceededModelOutcome | FailedModelOutcome, Field(discriminator="kind")
]


class _ModelCallBase(_State):
    id: ModelCallId
    task_id: TaskId
    request_sha256: Annotated[ModelRequestHash, Field(pattern=_SHA256_PATTERN)]
    occurrence: PositiveInt
    model: ModelId


class PendingModelCall(_ModelCallBase):
    status: Literal["pending"] = "pending"
    job_id: None = None
    outcome: None = None


class QueuedModelCall(_ModelCallBase):
    status: Literal["queued"] = "queued"
    job_id: ModelBatchJobId
    outcome: None = None


class SucceededModelCall(_ModelCallBase):
    status: Literal["succeeded"] = "succeeded"
    job_id: ModelBatchJobId
    outcome: SucceededModelOutcome


class FailedModelCall(_ModelCallBase):
    status: Literal["failed"] = "failed"
    job_id: ModelBatchJobId
    outcome: FailedModelOutcome


type ResolvedModelCall = SucceededModelCall | FailedModelCall
type ModelCall = Annotated[
    PendingModelCall | QueuedModelCall | SucceededModelCall | FailedModelCall,
    Field(discriminator="status"),
]


def queue_call(call: PendingModelCall, job_id: ModelBatchJobId) -> QueuedModelCall:
    return QueuedModelCall.model_validate(
        {**call.model_dump(), "status": "queued", "job_id": job_id}
    )


@overload
def resolve_call(
    call: QueuedModelCall, *, job_id: ModelBatchJobId, outcome: SucceededModelOutcome
) -> SucceededModelCall: ...
@overload
def resolve_call(
    call: QueuedModelCall, *, job_id: ModelBatchJobId, outcome: FailedModelOutcome
) -> FailedModelCall: ...
def resolve_call(
    call: QueuedModelCall,
    *,
    job_id: ModelBatchJobId,
    outcome: SucceededModelOutcome | FailedModelOutcome,
) -> ResolvedModelCall:
    if call.job_id != job_id:
        raise OutputConflictError
    fields = {**call.model_dump(), "outcome": outcome}
    match outcome:
        case SucceededModelOutcome():
            return SucceededModelCall.model_validate({**fields, "status": "succeeded"})
        case FailedModelOutcome():
            return FailedModelCall.model_validate({**fields, "status": "failed"})
        case unreachable:
            assert_never(unreachable)


def requeue_call(call: QueuedModelCall, *, job_id: ModelBatchJobId) -> PendingModelCall:
    if call.job_id != job_id:
        raise OutputConflictError
    return PendingModelCall.model_validate(
        {**call.model_dump(), "status": "pending", "job_id": None}
    )


class ProviderJobActive(_State):
    kind: Literal["active"] = "active"


class ProviderJobSucceeded(_State):
    kind: Literal["succeeded"] = "succeeded"
    partial: bool
    output_directory: Annotated[str, Field(pattern=r"^gs://[^/]+/.+")]


class ProviderJobTerminated(_State):
    kind: Literal["terminated"] = "terminated"
    state: Literal["failed", "cancelled", "expired"]


type ProviderJobState = ProviderJobActive | ProviderJobSucceeded | ProviderJobTerminated


def provider_job_state(job: genai_types.BatchJob) -> ProviderJobState:
    state = job.state
    if state is None:
        msg = "Vertex batch job reported no state"
        raise ValueError(msg)
    match state:
        case (
            genai_types.JobState.JOB_STATE_QUEUED
            | genai_types.JobState.JOB_STATE_PENDING
            | genai_types.JobState.JOB_STATE_RUNNING
            | genai_types.JobState.JOB_STATE_UPDATING
            | genai_types.JobState.JOB_STATE_PAUSED
            | genai_types.JobState.JOB_STATE_CANCELLING
        ):
            return ProviderJobActive()
        case (
            genai_types.JobState.JOB_STATE_SUCCEEDED
            | genai_types.JobState.JOB_STATE_PARTIALLY_SUCCEEDED
        ):
            directory = job.output_info.gcs_output_directory if job.output_info else None
            if directory is None:
                msg = "Vertex batch job finished without an output directory"
                raise ValueError(msg)
            return ProviderJobSucceeded(
                partial=state is genai_types.JobState.JOB_STATE_PARTIALLY_SUCCEEDED,
                output_directory=directory,
            )
        case genai_types.JobState.JOB_STATE_FAILED:
            return ProviderJobTerminated(state="failed")
        case genai_types.JobState.JOB_STATE_CANCELLED:
            return ProviderJobTerminated(state="cancelled")
        case genai_types.JobState.JOB_STATE_EXPIRED:
            return ProviderJobTerminated(state="expired")
        case genai_types.JobState.JOB_STATE_UNSPECIFIED:
            msg = "Vertex batch job reported an unspecified state"
            raise ValueError(msg)
        case unreachable:
            assert_never(unreachable)


class _ModelBatchJobBase(_State):
    id: ModelBatchJobId
    task_id: TaskId
    model: ModelId


class PreparedModelBatchJob(_ModelBatchJobBase):
    status: Literal["prepared"] = "prepared"
    vertex_job_name: None = None
    provider_state: None = None


class SubmittingModelBatchJob(_ModelBatchJobBase):
    status: Literal["submitting"] = "submitting"
    vertex_job_name: None = None
    provider_state: None = None


class RunningModelBatchJob(_ModelBatchJobBase):
    status: Literal["running"] = "running"
    vertex_job_name: VertexBatchJobName
    provider_state: None = None


class IngestedModelBatchJob(_ModelBatchJobBase):
    status: Literal["ingested"] = "ingested"
    vertex_job_name: VertexBatchJobName
    provider_state: Literal["succeeded", "partially_succeeded"]


class FailedModelBatchJob(_ModelBatchJobBase):
    status: Literal["failed"] = "failed"
    vertex_job_name: VertexBatchJobName | None
    provider_state: Literal["failed", "cancelled", "expired", "rejected"]


type ActiveModelBatchJob = PreparedModelBatchJob | SubmittingModelBatchJob | RunningModelBatchJob
type TerminalModelBatchJob = IngestedModelBatchJob | FailedModelBatchJob
type ModelBatchJob = Annotated[
    PreparedModelBatchJob
    | SubmittingModelBatchJob
    | RunningModelBatchJob
    | IngestedModelBatchJob
    | FailedModelBatchJob,
    Field(discriminator="status"),
]


def begin_submission(job: PreparedModelBatchJob) -> SubmittingModelBatchJob:
    return SubmittingModelBatchJob.model_validate({**job.model_dump(), "status": "submitting"})


def record_submission(
    job: SubmittingModelBatchJob, name: VertexBatchJobName
) -> RunningModelBatchJob:
    return RunningModelBatchJob.model_validate(
        {**job.model_dump(), "status": "running", "vertex_job_name": name}
    )


def recover_submission(
    job: SubmittingModelBatchJob, found: VertexBatchJobName | None
) -> RunningModelBatchJob | PreparedModelBatchJob:
    if found is not None:
        return record_submission(job, found)
    return PreparedModelBatchJob.model_validate({**job.model_dump(), "status": "prepared"})


def ingest_job(job: RunningModelBatchJob, observed: ProviderJobSucceeded) -> IngestedModelBatchJob:
    return IngestedModelBatchJob.model_validate(
        {
            **job.model_dump(),
            "status": "ingested",
            "provider_state": "partially_succeeded" if observed.partial else "succeeded",
        }
    )


def fail_job(
    job: SubmittingModelBatchJob | RunningModelBatchJob,
    state: Literal["failed", "cancelled", "expired", "rejected"],
) -> FailedModelBatchJob:
    match job:
        case SubmittingModelBatchJob():
            if state != "rejected":
                raise OutputConflictError
        case RunningModelBatchJob():
            if state == "rejected":
                raise OutputConflictError
        case unreachable:
            assert_never(unreachable)
    return FailedModelBatchJob.model_validate(
        {**job.model_dump(), "status": "failed", "provider_state": state}
    )
