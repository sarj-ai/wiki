from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import Annotated

from fastapi import Depends, HTTPException, Response, status
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, http_client_dep
from app.auth.google_tokens import (
    GoogleReauthenticationRequiredError,
    GoogleTokenManager,
    GoogleTokenUnavailableError,
)
from app.cache.dependencies import cache_service_dep
from app.cache.service import CacheService
from app.config import settings
from app.database import get_db
from app.document_transform.application import CurrentDocumentRevisionReader, DestinationFileReader
from app.document_transform.google_gateway import (
    GoogleDocumentRevisionReader,
)
from app.document_transform.models import (
    GeminiModelPolicy,
    PatchGenerator,
    VertexGeminiPatchGenerator,
)
from app.document_transform.task_executor import (
    DocumentTransformTaskExecutor,
    DriveDocumentTransformTaskExecutor,
)
from app.drive.service import GoogleDriveService
from app.files.service import GoogleFilesService
from app.identifiers import UserId
from app.models.user import User
from app.tasks.dependencies import task_service_dep
from app.tasks.service import TaskService


@dataclass(frozen=True, slots=True)
class CurrentPolishRuntime:
    files: DestinationFileReader
    revisions: CurrentDocumentRevisionReader


@dataclass(frozen=True, slots=True)
class CurrentPolishContext:
    owner_user_id: UserId
    tasks: TaskService
    files: DestinationFileReader
    revisions: CurrentDocumentRevisionReader


async def current_polish_runtime_dep(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    http: Annotated[httpx.AsyncClient, Depends(http_client_dep)],
    cache_service: Annotated[CacheService, Depends(cache_service_dep)],
    response: Response,
) -> CurrentPolishRuntime:
    try:
        prepared = await GoogleTokenManager(db, http).prepare(user)
        await db.commit()
    except GoogleReauthenticationRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Google login required"
        ) from exc
    except GoogleTokenUnavailableError as exc:
        if exc.retry_after is not None:
            response.headers["Retry-After"] = str(exc.retry_after)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google authorization is temporarily unavailable",
        ) from exc
    drive = GoogleDriveService(
        prepared.user,
        cache_service,
        refresh_handler=prepared.refresh_handler,
    )
    files = GoogleFilesService(user=prepared.user, drive=drive)
    revisions = GoogleDocumentRevisionReader(
        user=prepared.user,
        files=files,
        logo_uri="",
        refresh_handler=prepared.refresh_handler,
    )
    return CurrentPolishRuntime(files=files, revisions=revisions)


async def current_polish_context_dep(
    user: Annotated[User, Depends(get_current_user)],
    tasks: Annotated[TaskService, Depends(task_service_dep)],
    runtime: Annotated[CurrentPolishRuntime, Depends(current_polish_runtime_dep)],
) -> CurrentPolishContext:
    return CurrentPolishContext(
        owner_user_id=UserId(user.id),
        tasks=tasks,
        files=runtime.files,
        revisions=runtime.revisions,
    )


@cache
def document_patch_generator_dep() -> PatchGenerator:
    project = settings.GOOGLE_CLOUD_PROJECT
    if project is None:
        msg = "GOOGLE_CLOUD_PROJECT is required for Document Transform"
        raise RuntimeError(msg)
    policy = GeminiModelPolicy(
        project=project,
        location="global",
        unslop_model=settings.DOCUMENT_TRANSFORM_UNSLOP_MODEL,
        fallback_model=settings.DOCUMENT_TRANSFORM_FALLBACK_MODEL,
        restyle_model=settings.DOCUMENT_TRANSFORM_RESTYLE_MODEL,
    )
    return VertexGeminiPatchGenerator(policy=policy)


async def document_transform_task_executor_dep(
    db: Annotated[AsyncSession, Depends(get_db)],
    tasks: Annotated[TaskService, Depends(task_service_dep)],
    cache_service: Annotated[CacheService, Depends(cache_service_dep)],
    http: Annotated[httpx.AsyncClient, Depends(http_client_dep)],
    generator: Annotated[PatchGenerator, Depends(document_patch_generator_dep)],
) -> DocumentTransformTaskExecutor:
    return DriveDocumentTransformTaskExecutor(
        db=db,
        tasks=tasks,
        cache=cache_service,
        http=http,
        generator=generator,
        logo_uri=str(settings.DOCUMENT_TRANSFORM_LOGO_URI),
    )
