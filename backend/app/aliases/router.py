from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Response, status
from loguru import logger

from app.aliases.dependencies import alias_service_dep
from app.aliases.schemas import (
    DocumentAliasForFile,
    DocumentAliasRead,
    DocumentAliasValue,
    DocumentAliasWrite,
    RecentDocumentAlias,
    RecentDocumentAliasesRequest,
)
from app.aliases.service import AliasService
from app.auth.dependencies import get_current_user
from app.identifiers import FileId
from app.openapi import authentication_responses


router = APIRouter(
    prefix="/aliases",
    tags=["aliases"],
    dependencies=[Depends(get_current_user)],
    responses=authentication_responses(),
)


@router.get(
    "/files/{file_id}",
    status_code=status.HTTP_200_OK,
    operation_id="get_file_alias",
)
async def get_file_alias(
    file_id: Annotated[FileId, Path(min_length=1)],
    svc: Annotated[AliasService, Depends(alias_service_dep)],
) -> DocumentAliasForFile:
    return await svc.get_for_file(file_id)


@router.put(
    "/files/{file_id}",
    status_code=status.HTTP_200_OK,
    operation_id="save_file_alias",
)
async def save_file_alias(
    file_id: Annotated[FileId, Path(min_length=1)],
    payload: Annotated[DocumentAliasWrite, Body()],
    svc: Annotated[AliasService, Depends(alias_service_dep)],
) -> DocumentAliasRead:
    result = await svc.save(file_id, payload.alias)
    logger.info("document alias saved")
    return result


@router.post(
    "/files/{file_id}/ensure",
    status_code=status.HTTP_200_OK,
    operation_id="ensure_file_alias",
)
async def ensure_file_alias(
    file_id: Annotated[FileId, Path(min_length=1)],
    svc: Annotated[AliasService, Depends(alias_service_dep)],
    response: Response,
) -> DocumentAliasRead:
    response.headers["Cache-Control"] = "private, no-store"
    result = await svc.ensure_polish_for_file(file_id)
    logger.info("document alias ensured")
    return result


@router.delete(
    "/files/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="retire_file_alias",
)
async def retire_file_alias(
    file_id: Annotated[FileId, Path(min_length=1)],
    svc: Annotated[AliasService, Depends(alias_service_dep)],
) -> None:
    await svc.retire(file_id)
    logger.info("document alias retired")


@router.post(
    "/recent",
    status_code=status.HTTP_200_OK,
    operation_id="get_recent_aliases",
)
async def get_recent_aliases(
    payload: Annotated[RecentDocumentAliasesRequest, Body()],
    svc: Annotated[AliasService, Depends(alias_service_dep)],
    response: Response,
) -> list[RecentDocumentAlias]:
    response.headers["Cache-Control"] = "private, no-store"
    return await svc.resolve_recent(payload.aliases)


@router.get(
    "/{alias}",
    status_code=status.HTTP_200_OK,
    operation_id="resolve_alias",
)
async def resolve_alias(
    alias: Annotated[DocumentAliasValue, Path(min_length=1)],
    svc: Annotated[AliasService, Depends(alias_service_dep)],
) -> DocumentAliasRead:
    result = await svc.resolve(alias)
    logger.info("document alias resolved")
    return result
