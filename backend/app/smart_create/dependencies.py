from __future__ import annotations

from functools import cache
from typing import Annotated

from fastapi import Depends
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import http_client_dep
from app.cache.dependencies import cache_service_dep
from app.cache.service import CacheService
from app.database import async_session, get_db
from app.drive.dependencies import drive_service_dep
from app.drive.service import DriveService
from app.files.dependencies import files_service_dep
from app.files.service import FilesService
from app.sidebar.cache import SidebarCacheService
from app.sidebar.dependencies import sidebar_cache_service_dep
from app.smart_create.executor import DriveSmartCreateTaskExecutor, SmartCreateTaskExecutor
from app.smart_create.service import (
    GeminiReorganizationService,
    GeminiSmartProposalService,
    ProposalGenerator,
    ReorganizationGenerator,
    ReorganizationService,
    SmartProposalService,
    VertexProposalGenerator,
    VertexReorganizationGenerator,
)
from app.tasks.dependencies import task_service_dep
from app.tasks.service import TaskService
from app.templates.dependencies import template_catalog_service_dep
from app.templates.service import TemplateCatalogService


@cache
def proposal_generator_dep() -> ProposalGenerator:
    return VertexProposalGenerator()


@cache
def reorganization_generator_dep() -> ReorganizationGenerator:
    return VertexReorganizationGenerator()


async def reorganization_service_dep(
    db: Annotated[AsyncSession, Depends(get_db)],
    drive: Annotated[DriveService, Depends(drive_service_dep)],
    files: Annotated[FilesService, Depends(files_service_dep)],
    sidebar_cache: Annotated[SidebarCacheService, Depends(sidebar_cache_service_dep)],
    generator: Annotated[ReorganizationGenerator, Depends(reorganization_generator_dep)],
) -> ReorganizationService:
    return GeminiReorganizationService(
        db=db,
        drive=drive,
        files=files,
        sidebar_cache=sidebar_cache,
        generator=generator,
    )


async def smart_proposal_service_dep(
    db: Annotated[AsyncSession, Depends(get_db)],
    drive: Annotated[DriveService, Depends(drive_service_dep)],
    sidebar_cache: Annotated[SidebarCacheService, Depends(sidebar_cache_service_dep)],
    templates: Annotated[TemplateCatalogService, Depends(template_catalog_service_dep)],
    generator: Annotated[ProposalGenerator, Depends(proposal_generator_dep)],
) -> SmartProposalService:
    return GeminiSmartProposalService(
        db=db,
        drive=drive,
        sidebar_cache=sidebar_cache,
        templates=templates,
        generator=generator,
    )


async def smart_create_task_executor_dep(
    db: Annotated[AsyncSession, Depends(get_db)],
    tasks: Annotated[TaskService, Depends(task_service_dep)],
    cache: Annotated[CacheService, Depends(cache_service_dep)],
    http: Annotated[httpx.AsyncClient, Depends(http_client_dep)],
) -> SmartCreateTaskExecutor:
    return DriveSmartCreateTaskExecutor(
        db=db,
        tasks=tasks,
        cache=cache,
        http=http,
        sessionmaker=async_session,
    )
