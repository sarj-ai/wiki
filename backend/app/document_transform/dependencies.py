from __future__ import annotations

from functools import cache
from typing import Annotated

from fastapi import Depends
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import http_client_dep
from app.cache.dependencies import cache_service_dep
from app.cache.service import CacheService
from app.config import settings
from app.database import get_db
from app.document_transform.models import (
    GeminiModelPolicy,
    PatchGenerator,
    VertexGeminiPatchGenerator,
)
from app.document_transform.task_executor import (
    DocumentTransformTaskExecutor,
    DriveDocumentTransformTaskExecutor,
)
from app.tasks.dependencies import task_service_dep
from app.tasks.service import TaskService


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


def document_transform_task_executor_dep(
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
