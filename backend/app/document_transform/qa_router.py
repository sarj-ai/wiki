from dataclasses import dataclass
from datetime import datetime
import hmac
from typing import Annotated, Literal
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
from app.document_transform.qa_grants import (
    QaAuthority,
    QaBootstrapResult,
    QaCapability,
    QaGrantError,
    authorize_qa_capability,
    bind_qa_bootstrap_execution,
    bootstrap_attested_qa_execution,
    consume_qa_grant,
    fail_qa_bootstrap_launch,
    issue_qa_grant,
    prepare_idempotent_qa_launch,
    read_active_qa_execution,
    renew_qa_capability,
)
from app.document_transform.qa_launcher import (
    ApplicationDefaultAccessTokenProvider,
    CloudRunQaLauncher,
    QaLaunchError,
)
from app.document_transform.schemas import TransformMode
from app.identifiers import FileId, QaPlanId, QaRunId, TaskId
from app.models.user import User
from app.tasks.dependencies import task_enqueuer_dep, task_service_dep
from app.tasks.errors import TaskError
from app.tasks.internal_auth import GoogleOidcTaskRequestAuthenticator
from app.tasks.queue import TaskEnqueuer
from app.tasks.schemas import TaskStatus
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
    output_strategy: Literal["copy"] = "copy"


class QaCanarySubmissionAuthorization(QaCapabilityAuthorization):
    plan_id: QaPlanId


class QaCanarySubmissionResponse(QaCanarySessionResponse):
    plan_id: QaPlanId
    task_id: TaskId
    task_status: TaskStatus
    replayed: bool
    output_strategy: Literal["copy"] = "copy"


@dataclass(frozen=True, slots=True)
class _AuthorizedCanaryRequest[QaAuthorization: QaCapabilityAuthorization]:
    authority: QaAuthority
    body: QaAuthorization


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


def _require_legacy_qa_flow() -> None:
    if settings.DOCUMENT_TRANSFORM_QA_JOB is not None:
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
        launch_key = uuid.UUID(idempotency_key or "")
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
    dependencies=[Depends(_require_legacy_qa_flow), Depends(_require_qa_workload)],
)
async def exchange_document_transform_qa_grant(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
) -> QaCapabilityResponse:
    body = await _parse_legacy_body(request, QaGrantExchange)
    _configured_binding(body)
    try:
        capability = (
            await consume_qa_grant(
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
            if body.operation == "consume"
            else await renew_qa_capability(
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
        )
        await db.commit()
    except QaGrantError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="QA credential is invalid"
        ) from exc
    _prevent_token_caching(response)
    return _capability_response(capability)


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
    )


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
    except QaGrantError as exc:
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


def _prevent_token_caching(response: Response) -> None:
    response.headers.update(_NO_STORE_HEADERS)


def _invalid_qa_credential() -> HTTPException:
    return _no_store_http_error(status.HTTP_403_FORBIDDEN, "QA credential is invalid")


def _no_store_http_error(status_code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail, headers=_NO_STORE_HEADERS)
