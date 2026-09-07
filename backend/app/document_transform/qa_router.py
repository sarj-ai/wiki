from datetime import datetime
import hmac
from typing import Annotated, Literal
import uuid

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.jwt import decode_token
from app.config import settings
from app.database import get_db
from app.document_transform.qa_grants import (
    QaCapability,
    QaGrantError,
    consume_qa_grant,
    issue_qa_grant,
    renew_qa_capability,
)
from app.document_transform.schemas import TransformMode
from app.identifiers import FileId
from app.models.user import User
from app.tasks.internal_auth import GoogleOidcTaskRequestAuthenticator


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


issuer_router = APIRouter(prefix="/document-transforms/qa", tags=["document-transform-qa"])
workload_router = APIRouter(
    prefix="/internal/document-transform-qa",
    tags=["internal-document-transform-qa"],
    include_in_schema=False,
)


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


def _capability_response(capability: QaCapability) -> QaCapabilityResponse:
    return QaCapabilityResponse(
        capability_token=capability.token,
        expires_at=capability.expires_at,
        job_expires_at=capability.job_expires_at,
        user_email=capability.user_email,
    )


def _prevent_token_caching(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
