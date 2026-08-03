from __future__ import annotations

from functools import cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.aliases.dependencies import alias_service_dep
from app.aliases.service import AliasService
from app.cache.dependencies import cache_service_dep
from app.cache.service import CacheService
from app.database import async_session, get_db
from app.drive.dependencies import drive_service_dep
from app.drive.service import DriveService
from app.extras.dependencies import extras_service_dep
from app.extras.service import ExtrasService
from app.files.dependencies import files_service_dep
from app.files.service import FilesService
from app.sidebar.cache import SidebarCacheService
from app.sidebar.dependencies import sidebar_cache_service_dep
from app.smart_create.creation import DriveSmartFileCreationService, SmartFileCreationService
from app.smart_create.executor import DriveSmartCreateTaskExecutor, SmartCreateTaskExecutor
from app.smart_create.service import (
    GeminiSmartProposalService,
    ProposalGenerator,
    SmartProposalService,
    VertexProposalGenerator,
)
from app.tasks.dependencies import task_service_dep
from app.tasks.service import TaskService
from app.templates.dependencies import template_catalog_service_dep
from app.templates.service import TemplateCatalogService


@cache
def proposal_generator_dep() -> ProposalGenerator:
    return VertexProposalGenerator()


def smart_proposal_service_dep(
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


def smart_file_creation_service_dep(
    *,
    db: Annotated[AsyncSession, Depends(get_db)],
    files: Annotated[FilesService, Depends(files_service_dep)],
    aliases: Annotated[AliasService, Depends(alias_service_dep)],
    extras: Annotated[ExtrasService, Depends(extras_service_dep)],
    tasks: Annotated[TaskService, Depends(task_service_dep)],
    templates: Annotated[TemplateCatalogService, Depends(template_catalog_service_dep)],
) -> SmartFileCreationService:
    return DriveSmartFileCreationService(
        files=files,
        aliases=aliases,
        extras=extras,
        tasks=tasks,
        templates=templates,
        db=db,
    )


def smart_create_task_executor_dep(
    db: Annotated[AsyncSession, Depends(get_db)],
    tasks: Annotated[TaskService, Depends(task_service_dep)],
    cache: Annotated[CacheService, Depends(cache_service_dep)],
) -> SmartCreateTaskExecutor:
    return DriveSmartCreateTaskExecutor(
        db=db,
        tasks=tasks,
        cache=cache,
        sessionmaker=async_session,
    )
