from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json
from typing import Annotated, Literal
from urllib.parse import quote
import uuid

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, Response, status
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, http_client_dep
from app.auth.jwt import decode_token
from app.cache.dependencies import cache_service_dep
from app.cache.service import CacheService
from app.config import settings
from app.database import get_db
from app.document_transform.qa_broker import QaCanaryBroker
from app.document_transform.qa_campaigns import (
    CreatedQaCorpusCampaign,
    QaCorpusCampaignError,
    QaCorpusCampaignRead,
    create_or_load_qa_corpus_campaign,
    load_qa_corpus_campaign_for_authority,
)
from app.document_transform.qa_contract import (
    QA_MAX_STORED_FINDINGS,
    QA_MAX_STORED_MODEL_IDS,
    QA_MAX_STORED_RESULT_BYTES,
    QA_MAX_STORED_TOOL_VERSIONS,
)
from app.document_transform.qa_corpus import QaCorpusPlan
from app.document_transform.qa_grants import (
    QaAuthority,
    QaBootstrapResult,
    QaCapability,
    QaGrantError,
    QaSubmittedCanary,
    authorize_qa_capability,
    bind_qa_bootstrap_execution,
    bind_qa_canary_submission,
    bootstrap_attested_qa_execution,
    consume_qa_grant,
    fail_qa_bootstrap_launch,
    issue_qa_grant,
    load_qa_canary_submission,
    prepare_idempotent_qa_launch,
    read_active_qa_execution,
    renew_qa_capability,
)
from app.document_transform.qa_launcher import (
    ApplicationDefaultAccessTokenProvider,
    CloudRunQaLauncher,
    QaLaunchError,
)
from app.document_transform.schemas import (
    DocumentTransformResult,
    FindingSeverity,
    QaCopyPermissionEvidence,
    TransformFinding,
    TransformMode,
    ValidationSummary,
)
from app.drive.schemas import MIME_DOC, MIME_FOLDER
from app.files.schemas import FileResponse
from app.identifiers import DocsRevisionId, FileId, IdempotencyKey, QaPlanId, QaRunId, TaskId
from app.models.document_transform_qa_corpus import (
    DocumentTransformQaCorpusCampaignId,
    DocumentTransformQaCorpusMemberId,
)
from app.models.user import User
from app.tasks.dependencies import task_enqueuer_dep, task_service_dep
from app.tasks.errors import TaskError
from app.tasks.internal_auth import GoogleOidcTaskRequestAuthenticator
from app.tasks.queue import TaskEnqueuer
from app.tasks.schemas import SucceededTaskRead, TaskRead, TaskStatus
from app.tasks.service import TaskService


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QaGrantBinding(_StrictModel):
    release_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_folder_id: FileId = Field(min_length=1, max_length=255)
    destination_folder_id: FileId = Field(min_length=1, max_length=255)
    mode: TransformMode


class QaGrantIssueResponse(_StrictModel):
    grant_id: str
    grant_token: str
    expires_at: datetime


class QaLaunchResponse(_StrictModel):
    run_id: QaRunId
    execution_name: str
    status: Literal["execution_bound"] = "execution_bound"


class QaGrantExchange(QaGrantBinding):
    credential: str = Field(min_length=32, max_length=256)
    execution_name: str = Field(min_length=1, max_length=255)
    operation: Literal["consume", "renew"]
    request_nonce: uuid.UUID


class QaLauncherRenewal(QaGrantBinding):
    credential: str = Field(min_length=32, max_length=256)
    execution_name: str = Field(min_length=1, max_length=255)
    request_nonce: uuid.UUID


class QaCapabilityResponse(_StrictModel):
    capability_token: str
    expires_at: datetime
    job_expires_at: datetime
    user_email: str


class QaCapabilityAuthorization(QaGrantBinding):
    execution_name: str = Field(min_length=1, max_length=255)


class QaBootstrapRequest(_StrictModel):
    execution_name: str = Field(min_length=1, max_length=255)
    request_nonce: uuid.UUID


class QaBootstrapResponse(QaCapabilityResponse, QaGrantBinding):
    execution_name: str


class QaCanarySessionResponse(QaGrantBinding):
    authorized: Literal[True] = True
    capability_expires_at: datetime
    job_expires_at: datetime
    user_email: str


class QaCanaryPlanResponse(QaCanarySessionResponse):
    plan_id: QaPlanId
    idempotency_key: uuid.UUID
    source_file_id: FileId
    source_name: str
    source_modified_time: str
    destination_version: str
    destination_permission_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_strategy: Literal["copy"] = "copy"


class QaCanarySubmissionAuthorization(QaCapabilityAuthorization):
    plan_id: QaPlanId


class QaCanarySubmissionResponse(QaCanarySessionResponse):
    plan_id: QaPlanId
    task_id: TaskId
    task_status: TaskStatus
    replayed: bool
    output_strategy: Literal["copy"] = "copy"


class QaCanaryReadAuthorization(QaCapabilityAuthorization):
    pass


class QaCorpusCampaignMemberResponse(_StrictModel):
    id: DocumentTransformQaCorpusMemberId
    ordinal: int = Field(ge=0, le=19)
    source_file_id: FileId
    source_name: str = Field(min_length=1, max_length=1024)
    mime_type: str
    source_parent_folder_id: FileId
    source_version: str
    source_modified_time: str
    task_plan_id: QaPlanId


class QaCorpusCampaignResponse(_StrictModel):
    campaign_id: DocumentTransformQaCorpusCampaignId
    corpus_plan_id: QaPlanId
    state: str
    source_folder_id: FileId
    destination_folder_id: FileId
    mode: TransformMode
    created_at: datetime
    members: tuple[QaCorpusCampaignMemberResponse, ...]


class QaCorpusCampaignCreateResponse(QaCorpusCampaignResponse):
    replayed: bool


class QaCanaryTaskResponse(_StrictModel):
    task_id: TaskId
    task_status: TaskStatus
    attempt_count: int = Field(ge=0)
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class QaCanaryResultOutput(_StrictModel):
    file_id: FileId = Field(min_length=1, max_length=255)
    web_url: str = Field(max_length=2_048)


class QaCanaryResultResponse(QaCanaryTaskResponse):
    operation_id: uuid.UUID
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output: QaCanaryResultOutput
    output_strategy: Literal["copy"] = "copy"
    changed: bool
    needs_review: bool
    applied_patch_count: int = Field(ge=0)
    deterministic_patch_count: int = Field(ge=0)
    model_patch_count: int = Field(ge=0)
    promoted_heading_count: int = Field(ge=0, le=20)
    short_link_alias: str | None = Field(default=None, max_length=63)
    short_link_status: Literal["ready", "unavailable"] | None


class QaCanaryFinding(_StrictModel):
    code: str = Field(min_length=1, max_length=128)
    block_id: str | None = Field(default=None, max_length=255)
    detail: str = Field(max_length=512)
    severity: FindingSeverity


class QaCanaryEvidenceResponse(QaCanaryTaskResponse):
    operation_id: uuid.UUID
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_revision_id: DocsRevisionId = Field(max_length=255)
    output_revision_id: DocsRevisionId = Field(max_length=255)
    validation: ValidationSummary
    findings: list[QaCanaryFinding] = Field(max_length=100)
    finding_total_count: int = Field(ge=0)
    review_finding_count: int = Field(ge=0)
    findings_truncated: bool
    artifact_kind: str = Field(min_length=1, max_length=32)
    qa_permission_evidence: QaCopyPermissionEvidence


@dataclass(frozen=True, slots=True)
class _AuthorizedCanaryRequest[QaAuthorization: QaCapabilityAuthorization]:
    authority: QaAuthority
    body: QaAuthorization


@dataclass(frozen=True, slots=True)
class _ReadCanaryTask:
    authorized: _AuthorizedCanaryRequest[QaCanaryReadAuthorization]
    submission: QaSubmittedCanary
    task: TaskRead


issuer_router = APIRouter(prefix="/document-transforms/qa", tags=["document-transform-qa"])
workload_router = APIRouter(
    prefix="/internal/document-transform-qa",
    tags=["internal-document-transform-qa"],
    include_in_schema=False,
)

_NO_STORE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_MIN_CAPABILITY_TOKEN_LENGTH = 32
_MAX_CAPABILITY_TOKEN_LENGTH = 256
_UUID_VERSION_7 = 7
_MAX_QA_TASK_RESPONSE_BYTES = 4 * 1024
_MAX_QA_RESULT_RESPONSE_BYTES = 8 * 1024
_MAX_QA_EVIDENCE_RESPONSE_BYTES = 64 * 1024
_MAX_QA_CORPUS_CAMPAIGN_RESPONSE_BYTES = 64 * 1024
_MAX_QA_EVIDENCE_FINDINGS = 100
_MAX_QA_STORED_METADATA_UTF8_BYTES = 128 * 1024
_MAX_QA_STORED_METADATA_NODES = 10_000


def qa_canary_broker_dep(
    db: Annotated[AsyncSession, Depends(get_db)],
    http: Annotated[httpx.AsyncClient, Depends(http_client_dep)],
    cache: Annotated[CacheService, Depends(cache_service_dep)],
) -> QaCanaryBroker:
    return QaCanaryBroker(db=db, http=http, cache=cache)


def qa_launcher_dep(
    http: Annotated[httpx.AsyncClient, Depends(http_client_dep)],
) -> CloudRunQaLauncher:
    project = settings.GOOGLE_CLOUD_PROJECT
    job = settings.DOCUMENT_TRANSFORM_QA_JOB
    image = settings.DOCUMENT_TRANSFORM_QA_IMAGE
    release = settings.DOCUMENT_TRANSFORM_QA_RELEASE_SHA
    worker = settings.DOCUMENT_TRANSFORM_QA_SERVICE_ACCOUNT
    backend = settings.DOCUMENT_TRANSFORM_QA_BACKEND_SERVICE_ACCOUNT
    bootstrap_url = settings.DOCUMENT_TRANSFORM_QA_BOOTSTRAP_URL
    if project is None or job is None or image is None or release is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="QA launcher is disabled",
        )
    if worker is None or backend is None or bootstrap_url is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="QA launcher is disabled",
        )
    return CloudRunQaLauncher(
        http=http,
        token_provider=ApplicationDefaultAccessTokenProvider(),
        job_name=job,
        image=image,
        release_sha=release,
        worker_service_account=worker,
        backend_service_account=backend,
        project=project,
        bootstrap_url=bootstrap_url,
    )


def _configured_binding(body: QaGrantBinding) -> None:
    if (
        settings.DOCUMENT_TRANSFORM_QA_IMAGE_DIGEST is None
        or settings.DOCUMENT_TRANSFORM_QA_AUDIENCE is None
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="QA grants are disabled"
        )
    configured_release = settings.DOCUMENT_TRANSFORM_QA_RELEASE_SHA or settings.RELEASE_SHA
    if (
        body.release_sha != configured_release
        or body.image_digest != settings.DOCUMENT_TRANSFORM_QA_IMAGE_DIGEST
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="QA release binding mismatch"
        )


def _require_configured_corpus_source(authority: QaAuthority) -> None:
    configured_source = settings.DOCUMENT_TRANSFORM_QA_CORPUS_SOURCE_FOLDER_ID
    if configured_source is None:
        raise _no_store_http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE, "QA corpus planning is disabled"
        )
    if not hmac.compare_digest(str(authority.source_folder_id), configured_source):
        raise _no_store_http_error(status.HTTP_409_CONFLICT, "QA corpus source binding mismatch")


def _require_legacy_qa_flow() -> None:
    if settings.DOCUMENT_TRANSFORM_QA_JOB is not None:
        raise _no_store_http_error(
            status.HTTP_410_GONE,
            "Legacy QA grant flow is disabled when the backend launcher is configured",
        )


def _require_exchange_flow(request: Request) -> None:
    if settings.DOCUMENT_TRANSFORM_QA_JOB is None:
        return
    if list(request.query_params.multi_items()) != [("operation", "renew")]:
        raise _no_store_http_error(
            status.HTTP_410_GONE,
            "Legacy QA grant flow is disabled when the backend launcher is configured",
        )


async def _parse_legacy_body[Body: BaseModel](request: Request, schema: type[Body]) -> Body:
    try:
        return schema.model_validate(await request.json())
    except (ValueError, ValidationError) as exc:
        raise _no_store_http_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Legacy QA request is invalid"
        ) from exc


def _require_execution_selector(identity: str, requested: str) -> None:
    if not hmac.compare_digest(identity, requested):
        raise QaLaunchError


async def _require_qa_workload(authorization: Annotated[str | None, Header()] = None) -> None:
    audience = settings.DOCUMENT_TRANSFORM_QA_AUDIENCE
    service_account = settings.DOCUMENT_TRANSFORM_QA_SERVICE_ACCOUNT
    if audience is None or service_account is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="QA workload exchange is disabled",
        )
    await GoogleOidcTaskRequestAuthenticator(
        audience=audience,
        service_account_email=service_account,
    ).authenticate(authorization)


@issuer_router.post(
    "/grants",
    response_model=QaGrantIssueResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_require_legacy_qa_flow)],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": {"$ref": "#/components/schemas/QaGrantBinding"}}
            },
        }
    },
    responses={
        401: {"description": "A same-site login is required"},
        409: {"description": "Binding mismatch"},
        410: {"description": "The legacy grant flow is disabled in launcher mode"},
        422: {"description": "The legacy grant request is invalid"},
    },
)
async def issue_document_transform_qa_grant(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
) -> QaGrantIssueResponse:
    body = await _parse_legacy_body(request, QaGrantBinding)
    _configured_binding(body)
    token = request.cookies.get("access_token")
    if token is None or request.headers.get("origin") != settings.frontend_origin:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    payload = decode_token(token)
    if (
        payload.type != "access"
        or payload.sub != user.id
        or not hmac.compare_digest(str(payload.tv), str(user.token_version))
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    try:
        grant = await issue_qa_grant(
            db,
            issuer_user_id=user.id,
            user_id=user.id,
            session_id=payload.sid,
            release_sha=body.release_sha,
            image_digest=body.image_digest,
            source_folder_id=body.source_folder_id,
            destination_folder_id=body.destination_folder_id,
            mode=body.mode,
        )
        await db.commit()
    except QaGrantError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="QA grant could not be issued"
        ) from exc
    _prevent_token_caching(response)
    return QaGrantIssueResponse(
        grant_id=str(grant.grant_id), grant_token=grant.token, expires_at=grant.expires_at
    )


@issuer_router.post(
    "/launch",
    response_model=QaLaunchResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        401: {"description": "A same-site login is required"},
        403: {"description": "The authenticated user is not an allowed QA operator"},
        409: {"description": "Binding mismatch"},
        422: {"description": "A UUIDv7 idempotency key is required"},
        502: {"description": "The execution could not be safely launched or attested"},
        503: {"description": "The default-off launcher is disabled"},
    },
)
async def launch_document_transform_qa_run(
    *,
    body: Annotated[QaGrantBinding, Body()],
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    launcher: Annotated[CloudRunQaLauncher, Depends(qa_launcher_dep)],
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> QaLaunchResponse:
    """Launch only the configured job and bind its server-issued execution identity."""
    _configured_binding(body)
    if user.email.casefold() not in settings.DOCUMENT_TRANSFORM_QA_OPERATOR_EMAILS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="QA operator required")
    try:
        launch_key = IdempotencyKey(uuid.UUID(idempotency_key or ""))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A UUIDv7 Idempotency-Key is required",
        ) from exc
    if launch_key.version != _UUID_VERSION_7:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A UUIDv7 Idempotency-Key is required",
        )
    token = request.cookies.get("access_token")
    if token is None or request.headers.get("origin") != settings.frontend_origin:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    payload = decode_token(token)
    if (
        payload.type != "access"
        or payload.sub != user.id
        or not hmac.compare_digest(str(payload.tv), str(user.token_version))
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    try:
        prepared = await prepare_idempotent_qa_launch(
            db,
            idempotency_key=launch_key,
            issuer_user_id=user.id,
            user_id=user.id,
            session_id=payload.sid,
            release_sha=body.release_sha,
            image_digest=body.image_digest,
            source_folder_id=body.source_folder_id,
            destination_folder_id=body.destination_folder_id,
            mode=body.mode,
        )
        await db.commit()
    except (IntegrityError, QaGrantError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="QA run could not be prepared"
        ) from exc
    if prepared.state in {"execution_bound", "bootstrapped"} and prepared.execution_name:
        _prevent_token_caching(response)
        return QaLaunchResponse(run_id=prepared.run_id, execution_name=prepared.execution_name)
    if not prepared.owns_launch_claim:
        if prepared.state == "launch_claimed":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="QA launch is in progress",
                headers={**_NO_STORE_HEADERS, "Retry-After": "2"},
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="QA launch is not retryable"
        )
    try:
        attestation = await launcher.launch()
    except QaLaunchError as exc:
        await _record_launch_failure(db, prepared.run_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="QA execution could not be safely attested",
        ) from exc
    try:
        await bind_qa_bootstrap_execution(
            db,
            run_id=prepared.run_id,
            execution_name=attestation.execution_name,
            execution_uid=attestation.execution_uid,
            execution_job_name=attestation.job_name,
            release_sha=body.release_sha,
            image_digest=body.image_digest,
            source_folder_id=body.source_folder_id,
            destination_folder_id=body.destination_folder_id,
            mode=body.mode,
        )
        await db.commit()
    except (IntegrityError, QaGrantError) as exc:
        await db.rollback()
        await _record_launch_failure(db, prepared.run_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="QA execution could not be bound",
        ) from exc
    _prevent_token_caching(response)
    return QaLaunchResponse(run_id=prepared.run_id, execution_name=attestation.execution_name)


@workload_router.post(
    "/exchange",
    response_model=QaCapabilityResponse,
    dependencies=[Depends(_require_exchange_flow), Depends(_require_qa_workload)],
)
async def exchange_document_transform_qa_grant(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
) -> QaCapabilityResponse:
    launcher_renewal = settings.DOCUMENT_TRANSFORM_QA_JOB is not None
    body = (
        await _parse_legacy_body(request, QaLauncherRenewal)
        if launcher_renewal
        else await _parse_legacy_body(request, QaGrantExchange)
    )
    _configured_binding(body)
    try:
        capability = await _exchange_capability(db, body)
        await db.commit()
    except QaGrantError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="QA credential is invalid"
        ) from exc
    _prevent_token_caching(response)
    return _capability_response(capability)


async def _exchange_capability(
    db: AsyncSession,
    body: QaGrantExchange | QaLauncherRenewal,
) -> QaCapability:
    if isinstance(body, QaLauncherRenewal):
        return await renew_qa_capability(
            db,
            capability_token=body.credential,
            request_nonce=body.request_nonce,
            derivation_key=settings.SECRET_KEY.get_secret_value().encode(),
            execution_name=body.execution_name,
            release_sha=body.release_sha,
            image_digest=body.image_digest,
            source_folder_id=body.source_folder_id,
            destination_folder_id=body.destination_folder_id,
            mode=body.mode,
            require_submitted_task=True,
        )
    if body.operation == "consume":
        return await consume_qa_grant(
            db,
            token=body.credential,
            request_nonce=body.request_nonce,
            derivation_key=settings.SECRET_KEY.get_secret_value().encode(),
            execution_name=body.execution_name,
            release_sha=body.release_sha,
            image_digest=body.image_digest,
            source_folder_id=body.source_folder_id,
            destination_folder_id=body.destination_folder_id,
            mode=body.mode,
        )
    return await renew_qa_capability(
        db,
        capability_token=body.credential,
        request_nonce=body.request_nonce,
        derivation_key=settings.SECRET_KEY.get_secret_value().encode(),
        execution_name=body.execution_name,
        release_sha=body.release_sha,
        image_digest=body.image_digest,
        source_folder_id=body.source_folder_id,
        destination_folder_id=body.destination_folder_id,
        mode=body.mode,
    )


@workload_router.post("/bootstrap", response_model=QaBootstrapResponse)
async def bootstrap_document_transform_qa_run(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    launcher: Annotated[CloudRunQaLauncher, Depends(qa_launcher_dep)],
    response: Response,
) -> QaBootstrapResponse:
    """Bootstrap the sole bound execution without accepting caller-selected IDs."""
    try:
        await _require_qa_workload(request.headers.get("Authorization"))
    except HTTPException as exc:
        raise _no_store_http_error(exc.status_code, str(exc.detail)) from exc
    try:
        body = QaBootstrapRequest.model_validate(await request.json())
    except (ValueError, ValidationError) as exc:
        raise _no_store_http_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "QA request is invalid"
        ) from exc
    try:
        identity = await read_active_qa_execution(db)
        _require_execution_selector(identity.execution_name, body.execution_name)
        attestation = await launcher.attest_execution(identity.execution_name)
        result = await bootstrap_attested_qa_execution(
            db,
            execution_uid=attestation.execution_uid,
            execution_name=body.execution_name,
            execution_job_name=attestation.job_name,
            request_nonce=body.request_nonce,
            derivation_key=settings.SECRET_KEY.get_secret_value().encode(),
            release_sha=attestation.release_sha,
            image_digest=attestation.image.rsplit("@", 1)[1],
        )
        await db.commit()
    except HTTPException as exc:
        raise _no_store_http_error(exc.status_code, str(exc.detail)) from exc
    except QaLaunchError as exc:
        await db.rollback()
        raise _invalid_qa_credential() from exc
    except QaGrantError as exc:
        await db.rollback()
        raise _no_store_http_error(
            status.HTTP_409_CONFLICT, "QA execution is not bound yet"
        ) from exc
    _prevent_token_caching(response)
    return _bootstrap_response(result)


async def _record_launch_failure(db: AsyncSession, run_id: QaRunId) -> None:
    try:
        await fail_qa_bootstrap_launch(db, run_id=run_id)
        await db.commit()
    except QaGrantError:
        await db.rollback()


@workload_router.post(
    "/canary/session",
    response_model=QaCanarySessionResponse,
)
async def authorize_document_transform_qa_canary_session(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
) -> QaCanarySessionResponse:
    """Prove the immutable scope for future canary-only broker operations.

    This endpoint intentionally performs no Drive read and no mutation. The Cloud
    Run entrypoint remains fail-closed until the separately reviewed brokered
    Drive and transform operations are implemented.
    """
    authority = await _authorize_canary_request(request, db)
    _prevent_token_caching(response)
    return _canary_session_response(authority)


@workload_router.post("/canary/plan", response_model=QaCanaryPlanResponse)
async def plan_document_transform_qa_canary(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    broker: Annotated[QaCanaryBroker, Depends(qa_canary_broker_dep)],
    response: Response,
) -> QaCanaryPlanResponse:
    """Select the only canary source without accepting a caller-chosen file ID."""
    authority = await _authorize_canary_request(request, db)
    try:
        plan = await broker.plan(authority)
    except QaGrantError as exc:
        raise _no_store_http_error(
            status.HTTP_409_CONFLICT, "QA canary could not be planned"
        ) from exc
    _prevent_token_caching(response)
    return QaCanaryPlanResponse(
        **_canary_session_response(authority).model_dump(),
        plan_id=plan.plan_id,
        idempotency_key=plan.idempotency_key,
        source_file_id=plan.source_file_id,
        source_name=plan.source_name,
        source_modified_time=plan.source_modified_time,
        destination_version=plan.destination_version,
        destination_permission_sha256=plan.destination_permission_sha256,
    )


@workload_router.post("/corpus/plan", response_model=QaCorpusPlan)
async def plan_document_transform_qa_corpus(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    broker: Annotated[QaCanaryBroker, Depends(qa_canary_broker_dep)],
    response: Response,
) -> QaCorpusPlan:
    """Snapshot the complete source corpus bound into the current capability."""
    authority = await _authorize_canary_request(request, db)
    _require_configured_corpus_source(authority)
    try:
        plan = await broker.plan_corpus(authority)
    except QaGrantError as exc:
        raise _no_store_http_error(
            status.HTTP_409_CONFLICT, "QA corpus could not be planned"
        ) from exc
    _prevent_token_caching(response)
    return plan


@workload_router.post("/corpus/campaign", response_model=QaCorpusCampaignCreateResponse)
async def create_document_transform_qa_corpus_campaign(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    broker: Annotated[QaCanaryBroker, Depends(qa_canary_broker_dep)],
    response: Response,
) -> QaCorpusCampaignCreateResponse:
    """Freeze one exact campaign after reauthorizing its Drive-derived plan."""
    authorized = await _authorize_canary_request_as(request, db, QaCapabilityAuthorization)
    _require_configured_corpus_source(authorized.authority)
    try:
        created = await _create_or_replay_corpus_campaign(
            request=request,
            db=db,
            broker=broker,
            initial_authority=authorized.authority,
        )
        campaign_response = _bounded_campaign_response(
            _campaign_create_response(created.campaign, replayed=created.replayed)
        )
        await db.commit()
    except (IntegrityError, QaCorpusCampaignError, QaGrantError) as exc:
        await db.rollback()
        raise _no_store_http_error(
            status.HTTP_409_CONFLICT, "QA corpus campaign could not be created"
        ) from exc
    _prevent_token_caching(response)
    return campaign_response


@workload_router.post("/corpus/campaign/read", response_model=QaCorpusCampaignResponse)
async def read_document_transform_qa_corpus_campaign(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
) -> QaCorpusCampaignResponse:
    """Read one campaign only through the current capability and owner binding."""
    authorized = await _authorize_canary_request_as(request, db, QaCapabilityAuthorization)
    _require_configured_corpus_source(authorized.authority)
    try:
        campaign = await load_qa_corpus_campaign_for_authority(db, authority=authorized.authority)
        campaign = _require_corpus_campaign(campaign)
        campaign_response = _bounded_campaign_response(_campaign_response(campaign))
    except (QaCorpusCampaignError, QaGrantError) as exc:
        await db.rollback()
        raise _no_store_http_error(
            status.HTTP_409_CONFLICT, "QA corpus campaign is unavailable"
        ) from exc
    _prevent_token_caching(response)
    return campaign_response


@workload_router.post(
    "/canary/submit",
    response_model=QaCanarySubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_document_transform_qa_canary(
    *,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    broker: Annotated[QaCanaryBroker, Depends(qa_canary_broker_dep)],
    tasks: Annotated[TaskService, Depends(task_service_dep)],
    enqueuer: Annotated[TaskEnqueuer, Depends(task_enqueuer_dep)],
    response: Response,
) -> QaCanarySubmissionResponse:
    """Recompute a server-selected plan and submit only its forced-copy task."""
    authorized = await _authorize_canary_request_as(request, db, QaCanarySubmissionAuthorization)
    try:
        submission = await broker.submit(
            authorized.authority,
            plan_id=authorized.body.plan_id,
            tasks=tasks,
            enqueuer=enqueuer,
        )
        await bind_qa_canary_submission(
            db,
            authority=authorized.authority,
            plan_id=authorized.body.plan_id,
            task_id=submission.task.id,
        )
        await db.commit()
        authorized = await _authorize_canary_request_as(
            request, db, QaCanarySubmissionAuthorization
        )
    except QaGrantError as exc:
        await db.rollback()
        raise _no_store_http_error(
            status.HTTP_409_CONFLICT, "QA canary plan is no longer current"
        ) from exc
    except HTTPException as exc:
        raise _no_store_http_error(exc.status_code, "QA canary could not be submitted") from exc
    except TaskError as exc:
        raise _no_store_http_error(exc.http_status, "QA canary could not be submitted") from exc
    _prevent_token_caching(response)
    return QaCanarySubmissionResponse(
        **_canary_session_response(authorized.authority).model_dump(),
        plan_id=authorized.body.plan_id,
        task_id=submission.task.id,
        task_status=submission.task.status,
        replayed=submission.replayed,
    )


@workload_router.post("/canary/task", response_model=QaCanaryTaskResponse)
async def read_document_transform_qa_canary_task(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    broker: Annotated[QaCanaryBroker, Depends(qa_canary_broker_dep)],
    tasks: Annotated[TaskService, Depends(task_service_dep)],
    response: Response,
) -> QaCanaryTaskResponse:
    """Read the task derived from the current authority and its server plan."""
    read = await _read_canary_task(request, db, broker, tasks)
    _prevent_token_caching(response)
    return _bounded_response(_task_response(read.task), _MAX_QA_TASK_RESPONSE_BYTES)


@workload_router.post("/canary/result", response_model=QaCanaryResultResponse)
async def read_document_transform_qa_canary_result(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    broker: Annotated[QaCanaryBroker, Depends(qa_canary_broker_dep)],
    tasks: Annotated[TaskService, Depends(task_service_dep)],
    response: Response,
) -> QaCanaryResultResponse:
    """Return a bounded result only for this capability's successful canary."""
    read = await _read_canary_task(request, db, broker, tasks)
    result = _require_succeeded_transform(
        read.task,
        authority=read.authorized.authority,
    )
    verified_output = await _authorize_canary_output(request, db, broker, read, result)
    permission_evidence = await _read_canary_permission_evidence(request, db, broker, tasks, read)
    _require_permission_evidence_binding(
        result,
        permission_evidence,
        authority=read.authorized.authority,
    )
    _prevent_token_caching(response)
    try:
        result_response = QaCanaryResultResponse(
            **_task_response(read.task).model_dump(),
            operation_id=result.operation_id,
            result_sha256=_result_sha256(result),
            output=QaCanaryResultOutput(
                file_id=verified_output.id,
                web_url=(
                    "https://docs.google.com/document/d/"
                    f"{quote(str(verified_output.id), safe='')}/edit"
                ),
            ),
            changed=result.changed,
            needs_review=result.needs_review,
            applied_patch_count=result.applied_patch_count,
            deterministic_patch_count=result.deterministic_patch_count,
            model_patch_count=result.model_patch_count,
            promoted_heading_count=result.promoted_heading_count,
            short_link_alias=result.short_link_alias,
            short_link_status=result.short_link_status,
        )
    except ValidationError as exc:
        raise _canary_state_unavailable() from exc
    return _bounded_response(result_response, _MAX_QA_RESULT_RESPONSE_BYTES)


@workload_router.post("/canary/evidence", response_model=QaCanaryEvidenceResponse)
async def read_document_transform_qa_canary_evidence(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    broker: Annotated[QaCanaryBroker, Depends(qa_canary_broker_dep)],
    tasks: Annotated[TaskService, Depends(task_service_dep)],
    response: Response,
) -> QaCanaryEvidenceResponse:
    """Return bounded structured evidence; raw PDF artifacts are never transported."""
    read = await _read_canary_task(request, db, broker, tasks)
    result = _require_succeeded_transform(
        read.task,
        authority=read.authorized.authority,
    )
    await _authorize_canary_output(request, db, broker, read, result)
    permission_evidence = await _read_canary_permission_evidence(request, db, broker, tasks, read)
    _require_permission_evidence_binding(
        result,
        permission_evidence,
        authority=read.authorized.authority,
    )
    result_sha256 = _result_sha256(result)
    _prevent_token_caching(response)
    return _evidence_response(
        read.task,
        result,
        permission_evidence=permission_evidence,
        result_sha256=result_sha256,
    )


async def _read_canary_task(
    request: Request,
    db: AsyncSession,
    broker: QaCanaryBroker,
    tasks: TaskService,
) -> _ReadCanaryTask:
    authorized = await _authorize_canary_request_as(request, db, QaCanaryReadAuthorization)
    try:
        submission = await load_qa_canary_submission(db, authority=authorized.authority)
        task = await broker.read_task(
            authorized.authority,
            submission=submission,
            tasks=tasks,
        )
        authorized = await _authorize_canary_request_as(request, db, QaCanaryReadAuthorization)
    except (QaGrantError, TaskError) as exc:
        raise _canary_state_unavailable() from exc
    return _ReadCanaryTask(authorized=authorized, submission=submission, task=task)


async def _authorize_canary_output(
    request: Request,
    db: AsyncSession,
    broker: QaCanaryBroker,
    read: _ReadCanaryTask,
    result: DocumentTransformResult,
) -> FileResponse:
    try:
        output = await broker.authorize_output(read.authorized.authority, output=result.output)
        await _authorize_canary_request_as(request, db, QaCanaryReadAuthorization)
    except (QaGrantError, TaskError) as exc:
        raise _canary_state_unavailable() from exc
    return output


async def _read_canary_permission_evidence(
    request: Request,
    db: AsyncSession,
    broker: QaCanaryBroker,
    tasks: TaskService,
    read: _ReadCanaryTask,
) -> QaCopyPermissionEvidence:
    try:
        evidence = await broker.read_permission_evidence(
            read.authorized.authority,
            submission=read.submission,
            tasks=tasks,
        )
        await _authorize_canary_request_as(request, db, QaCanaryReadAuthorization)
    except (QaGrantError, TaskError) as exc:
        raise _canary_state_unavailable() from exc
    return evidence


def _task_response(task: TaskRead) -> QaCanaryTaskResponse:
    try:
        return QaCanaryTaskResponse(
            task_id=task.id,
            task_status=task.status,
            attempt_count=task.attempt_count,
            created_at=task.created_at,
            started_at=task.started_at,
            finished_at=task.finished_at,
        )
    except ValidationError as exc:
        raise _canary_state_unavailable() from exc


def _require_succeeded_transform(
    task: TaskRead,
    *,
    authority: QaAuthority,
) -> DocumentTransformResult:
    if not isinstance(task, SucceededTaskRead) or not isinstance(
        task.result, DocumentTransformResult
    ):
        raise _canary_state_unavailable()
    result = task.result
    _require_bounded_stored_result(result)
    resource_id = task.resource_id
    binding_mismatch = (
        not hmac.compare_digest(result.operation_id.bytes, task.id.bytes)
        or resource_id is None
        or not hmac.compare_digest(str(resource_id).encode(), str(result.output.file_id).encode())
        or result.mode is not authority.mode
        or result.output_strategy.value != "copy"
        or result.output.parent_folder_id != authority.destination_folder_id
        or result.access_scope != "owner_only"
    )
    missing_revision = result.source_revision_id is None or result.output_revision_id is None
    if binding_mismatch or missing_revision:
        raise _canary_state_unavailable()
    return result


def _require_bounded_stored_result(result: DocumentTransformResult) -> None:
    if (
        len(result.findings) > QA_MAX_STORED_FINDINGS
        or len(result.model_ids) > QA_MAX_STORED_MODEL_IDS
        or len(result.tool_versions) > QA_MAX_STORED_TOOL_VERSIONS
    ):
        raise _canary_state_unavailable()
    remaining_bytes = _MAX_QA_STORED_METADATA_UTF8_BYTES
    remaining_nodes = _MAX_QA_STORED_METADATA_NODES
    pending: list[object] = [result]
    while pending:
        remaining_nodes -= 1
        if remaining_nodes < 0:
            raise _canary_state_unavailable()
        value = pending.pop()
        match value:
            case str():
                # UTF-8 is never shorter than its character count. This avoids
                # an unbounded encoding allocation before the byte budget.
                if len(value) > remaining_bytes:
                    raise _canary_state_unavailable()
                remaining_bytes -= len(value.encode("utf-8", errors="strict"))
                if remaining_bytes < 0:
                    raise _canary_state_unavailable()
            case BaseModel():
                pending.extend(value.__dict__.values())
            case dict():
                pending.extend(value.keys())
                pending.extend(value.values())
            case list() | tuple():
                pending.extend(value)
            case _:
                pass


def _result_sha256(result: DocumentTransformResult) -> str:
    _require_bounded_stored_result(result)
    serialized = result.model_dump(mode="json")
    canonical = json.dumps(
        serialized, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    if len(canonical) > QA_MAX_STORED_RESULT_BYTES:
        raise _canary_state_unavailable()
    return hashlib.sha256(canonical).hexdigest()


def _evidence_response(
    task: TaskRead,
    result: DocumentTransformResult,
    *,
    permission_evidence: QaCopyPermissionEvidence,
    result_sha256: str,
) -> QaCanaryEvidenceResponse:
    source_revision_id = result.source_revision_id
    output_revision_id = result.output_revision_id
    if source_revision_id is None or output_revision_id is None:
        raise _canary_state_unavailable()
    total_count = len(result.findings)
    review_count = sum(finding.severity is FindingSeverity.REVIEW for finding in result.findings)
    findings = [_finding_response(item) for item in result.findings[:_MAX_QA_EVIDENCE_FINDINGS]]
    while True:
        try:
            evidence = QaCanaryEvidenceResponse(
                **_task_response(task).model_dump(),
                operation_id=result.operation_id,
                result_sha256=result_sha256,
                source_sha256=result.source_sha256,
                source_revision_id=source_revision_id,
                output_revision_id=output_revision_id,
                validation=result.validation,
                findings=findings,
                finding_total_count=total_count,
                review_finding_count=review_count,
                findings_truncated=len(findings) < total_count,
                artifact_kind=result.artifact_kind,
                qa_permission_evidence=permission_evidence,
            )
        except ValidationError as exc:
            raise _canary_state_unavailable() from exc
        if len(evidence.model_dump_json().encode()) <= _MAX_QA_EVIDENCE_RESPONSE_BYTES:
            return evidence
        if not findings:
            raise _canary_state_unavailable()
        findings.pop()


def _require_permission_evidence_binding(
    result: DocumentTransformResult,
    permission_evidence: QaCopyPermissionEvidence,
    *,
    authority: QaAuthority,
) -> None:
    destination_binding = (
        permission_evidence.destination.file_id,
        permission_evidence.destination.mime_type,
    ) == (authority.destination_folder_id, MIME_FOLDER)
    output_binding = (
        permission_evidence.output.file_id,
        permission_evidence.output.mime_type,
        permission_evidence.output.parent_folder_id,
    ) == (result.output.file_id, MIME_DOC, authority.destination_folder_id)
    if (
        permission_evidence.operation_id != result.operation_id
        or permission_evidence.source_sha256 != result.source_sha256
        or permission_evidence.output_revision_id != result.output_revision_id
        or not destination_binding
        or not output_binding
    ):
        raise _canary_state_unavailable()


def _finding_response(finding: TransformFinding) -> QaCanaryFinding:
    return QaCanaryFinding(
        code=finding.code[:128] or "unknown",
        block_id=finding.block_id[:255] if finding.block_id is not None else None,
        detail=finding.detail[:512],
        severity=finding.severity,
    )


def _bounded_response[ResponseModel: BaseModel](
    response: ResponseModel, maximum_bytes: int
) -> ResponseModel:
    if len(response.model_dump_json().encode()) > maximum_bytes:
        raise _canary_state_unavailable()
    return response


async def _authorize_canary_request(request: Request, db: AsyncSession) -> QaAuthority:
    authorized = await _authorize_canary_request_as(request, db, QaCapabilityAuthorization)
    return authorized.authority


async def _authorize_canary_request_as[QaAuthorization: QaCapabilityAuthorization](
    request: Request,
    db: AsyncSession,
    body_type: type[QaAuthorization],
) -> _AuthorizedCanaryRequest[QaAuthorization]:
    try:
        await _require_qa_workload(request.headers.get("Authorization"))
    except HTTPException as exc:
        raise _no_store_http_error(exc.status_code, str(exc.detail)) from exc
    capability_token = request.headers.get("X-Wiki-QA-Capability")
    if capability_token is None or not (
        _MIN_CAPABILITY_TOKEN_LENGTH <= len(capability_token) <= _MAX_CAPABILITY_TOKEN_LENGTH
    ):
        raise _invalid_qa_credential()
    try:
        body = body_type.model_validate(await request.json())
    except (ValueError, ValidationError) as exc:
        raise _no_store_http_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "QA request is invalid"
        ) from exc
    try:
        _configured_binding(body)
        authority = await authorize_qa_capability(
            db,
            capability_token=capability_token,
            execution_name=body.execution_name,
            release_sha=body.release_sha,
            image_digest=body.image_digest,
            source_folder_id=body.source_folder_id,
            destination_folder_id=body.destination_folder_id,
            mode=body.mode,
        )
    except HTTPException as exc:
        raise _no_store_http_error(exc.status_code, str(exc.detail)) from exc
    except QaGrantError as exc:
        await db.rollback()
        raise _invalid_qa_credential() from exc
    return _AuthorizedCanaryRequest(authority=authority, body=body)


def _capability_response(capability: QaCapability) -> QaCapabilityResponse:
    return QaCapabilityResponse(
        capability_token=capability.token,
        expires_at=capability.expires_at,
        job_expires_at=capability.job_expires_at,
        user_email=capability.user_email,
    )


def _bootstrap_response(result: QaBootstrapResult) -> QaBootstrapResponse:
    capability = result.capability
    return QaBootstrapResponse(
        capability_token=capability.token,
        expires_at=capability.expires_at,
        job_expires_at=capability.job_expires_at,
        user_email=capability.user_email,
        execution_name=result.execution_name,
        release_sha=result.release_sha,
        image_digest=result.image_digest,
        source_folder_id=result.source_folder_id,
        destination_folder_id=result.destination_folder_id,
        mode=result.mode,
    )


def _canary_session_response(authority: QaAuthority) -> QaCanarySessionResponse:
    return QaCanarySessionResponse(
        release_sha=authority.release_sha,
        image_digest=authority.image_digest,
        source_folder_id=authority.source_folder_id,
        destination_folder_id=authority.destination_folder_id,
        mode=authority.mode,
        capability_expires_at=authority.expires_at,
        job_expires_at=authority.job_expires_at,
        user_email=authority.user_email,
    )


async def _create_or_replay_corpus_campaign(
    *,
    request: Request,
    db: AsyncSession,
    broker: QaCanaryBroker,
    initial_authority: QaAuthority,
) -> CreatedQaCorpusCampaign:
    if (
        existing := await load_qa_corpus_campaign_for_authority(db, authority=initial_authority)
    ) is not None:
        return CreatedQaCorpusCampaign(campaign=existing, replayed=True)
    candidate = await broker.plan_corpus_campaign(initial_authority)
    refreshed = await _authorize_canary_request_as(request, db, QaCapabilityAuthorization)
    _require_configured_corpus_source(refreshed.authority)
    if refreshed.authority != initial_authority:
        raise QaGrantError
    return await create_or_load_qa_corpus_campaign(
        db,
        authority=refreshed.authority,
        plan=candidate.plan,
        destination=candidate.destination,
    )


def _campaign_response(campaign: QaCorpusCampaignRead) -> QaCorpusCampaignResponse:
    return QaCorpusCampaignResponse(
        campaign_id=campaign.id,
        corpus_plan_id=campaign.corpus_plan_id,
        state=campaign.state,
        source_folder_id=campaign.source_folder_id,
        destination_folder_id=campaign.destination_folder_id,
        mode=TransformMode(campaign.mode),
        created_at=campaign.created_at,
        members=tuple(
            QaCorpusCampaignMemberResponse(
                id=member.id,
                ordinal=member.ordinal,
                source_file_id=member.source_file_id,
                source_name=member.source_name,
                mime_type=member.mime_type,
                source_parent_folder_id=member.source_parent_folder_id,
                source_version=member.source_version,
                source_modified_time=member.source_modified_time,
                task_plan_id=member.task_plan_id,
            )
            for member in campaign.members
        ),
    )


def _require_corpus_campaign(
    campaign: QaCorpusCampaignRead | None,
) -> QaCorpusCampaignRead:
    if campaign is None:
        raise QaCorpusCampaignError
    return campaign


def _campaign_create_response(
    campaign: QaCorpusCampaignRead, *, replayed: bool
) -> QaCorpusCampaignCreateResponse:
    return QaCorpusCampaignCreateResponse(
        **_campaign_response(campaign).model_dump(),
        replayed=replayed,
    )


def _bounded_campaign_response[CampaignResponse: QaCorpusCampaignResponse](
    response: CampaignResponse,
) -> CampaignResponse:
    if len(response.model_dump_json().encode()) > _MAX_QA_CORPUS_CAMPAIGN_RESPONSE_BYTES:
        raise QaCorpusCampaignError
    return response


def _prevent_token_caching(response: Response) -> None:
    response.headers.update(_NO_STORE_HEADERS)


def _invalid_qa_credential() -> HTTPException:
    return _no_store_http_error(status.HTTP_403_FORBIDDEN, "QA credential is invalid")


def _canary_state_unavailable() -> HTTPException:
    return _no_store_http_error(status.HTTP_409_CONFLICT, "QA canary state is unavailable")


def _no_store_http_error(status_code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail, headers=_NO_STORE_HEADERS)
