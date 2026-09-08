from dataclasses import dataclass
from datetime import datetime
import hmac
from typing import Annotated, Literal
import uuid

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, Response, status
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
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
    QaCapability,
    QaGrantError,
    authorize_qa_capability,
    bootstrap_qa_run,
    consume_qa_grant,
    issue_qa_grant,
    renew_qa_capability,
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


class QaBootstrapRequest(QaCapabilityAuthorization):
    run_id: uuid.UUID
    request_nonce: uuid.UUID


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


def qa_canary_broker_dep(
    db: Annotated[AsyncSession, Depends(get_db)],
    http: Annotated[httpx.AsyncClient, Depends(http_client_dep)],
    cache: Annotated[CacheService, Depends(cache_service_dep)],
) -> QaCanaryBroker:
    return QaCanaryBroker(db=db, http=http, cache=cache)


def _configured_binding(body: QaGrantBinding) -> None:
    if (
        settings.DOCUMENT_TRANSFORM_QA_IMAGE_DIGEST is None
        or settings.DOCUMENT_TRANSFORM_QA_AUDIENCE is None
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="QA grants are disabled"
        )
    if (
        body.release_sha != settings.RELEASE_SHA
        or body.image_digest != settings.DOCUMENT_TRANSFORM_QA_IMAGE_DIGEST
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="QA release binding mismatch"
        )


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
    responses={
        401: {"description": "A same-site login is required"},
        409: {"description": "Binding mismatch"},
    },
)
async def issue_document_transform_qa_grant(
    body: Annotated[QaGrantBinding, Body()],
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
) -> QaGrantIssueResponse:
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


@workload_router.post(
    "/exchange", response_model=QaCapabilityResponse, dependencies=[Depends(_require_qa_workload)]
)
async def exchange_document_transform_qa_grant(
    body: Annotated[QaGrantExchange, Body()],
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
) -> QaCapabilityResponse:
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


@workload_router.post("/bootstrap", response_model=QaCapabilityResponse)
async def bootstrap_document_transform_qa_run(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
) -> QaCapabilityResponse:
    """Bootstrap a previously bound execution without transporting a grant secret.

    No production route can prepare or bind a run in this release. The endpoint
    therefore remains fail-closed until the separately reviewed launcher exists.
    """
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
        _configured_binding(body)
        capability = await bootstrap_qa_run(
            db,
            run_id=QaRunId(body.run_id),
            execution_name=body.execution_name,
            request_nonce=body.request_nonce,
            derivation_key=settings.SECRET_KEY.get_secret_value().encode(),
            release_sha=body.release_sha,
            image_digest=body.image_digest,
            source_folder_id=body.source_folder_id,
            destination_folder_id=body.destination_folder_id,
            mode=body.mode,
        )
        await db.commit()
    except HTTPException as exc:
        raise _no_store_http_error(exc.status_code, str(exc.detail)) from exc
    except QaGrantError as exc:
        await db.rollback()
        raise _invalid_qa_credential() from exc
    _prevent_token_caching(response)
    return _capability_response(capability)


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
