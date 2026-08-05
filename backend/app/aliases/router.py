from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from loguru import logger

from app.aliases.dependencies import alias_service_dep
from app.aliases.schemas import (
    DocumentAliasForFile,
    DocumentAliasRead,
    DocumentAliasValue,
    DocumentAliasWrite,
    RecentDocumentAlias,
)
from app.aliases.service import AliasService
from app.auth.dependencies import get_current_user
from app.identifiers import FileId
from app.models.user import User


router = APIRouter(
    prefix="/aliases",
    tags=["aliases"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/files/{file_id}")
async def get_file_alias(
    file_id: FileId,
    svc: Annotated[AliasService, Depends(alias_service_dep)],
) -> DocumentAliasForFile:
    return await svc.get_for_file(file_id)


@router.put("/files/{file_id}")
async def save_file_alias(
    file_id: FileId,
    payload: DocumentAliasWrite,
    svc: Annotated[AliasService, Depends(alias_service_dep)],
    user: Annotated[User, Depends(get_current_user)],
) -> DocumentAliasRead:
    result = await svc.save(file_id, payload.alias)
    logger.info(
        "document alias saved alias={alias} actor={actor}", alias=result.alias, actor=user.id
    )
    return result


@router.delete("/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def retire_file_alias(
    file_id: FileId,
    svc: Annotated[AliasService, Depends(alias_service_dep)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await svc.retire(file_id)
    logger.info("document alias retired actor={actor}", actor=user.id)


@router.post("/visits/{alias}")
async def record_alias_visit(
    alias: DocumentAliasValue,
    svc: Annotated[AliasService, Depends(alias_service_dep)],
) -> DocumentAliasRead:
    return await svc.record_visit(alias)


@router.get("/recent")
async def get_recent_aliases(
    svc: Annotated[AliasService, Depends(alias_service_dep)],
) -> list[RecentDocumentAlias]:
    return await svc.recent()


@router.get("/{alias}")
async def resolve_alias(
    alias: DocumentAliasValue,
    svc: Annotated[AliasService, Depends(alias_service_dep)],
) -> DocumentAliasRead:
    result = await svc.resolve(alias)
    logger.info("document alias resolved alias={alias}", alias=alias)
    return result
