from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, status

from app.extras.dependencies import extras_service_dep
from app.extras.schemas import (
    DocIconRead,
    DocIconWrite,
    FolderIconRead,
    FolderIconWrite,
    PreferencesRead,
    PreferencesWrite,
    SortOrderBulkResult,
    SortOrderBulkWrite,
    SortOrderRead,
)
from app.extras.service import ExtrasService, list_accessible_doc_icons
from app.files.dependencies import files_service_dep, require_accessible_doc
from app.files.service import FilesService
from app.identifiers import FileId
from app.openapi import authentication_responses


router = APIRouter(prefix="/extras", tags=["extras"], responses=authentication_responses())


@router.get(
    "/doc-icons",
    status_code=status.HTTP_200_OK,
    operation_id="list_doc_icons",
)
async def list_doc_icons(
    svc: Annotated[ExtrasService, Depends(extras_service_dep)],
    files: Annotated[FilesService, Depends(files_service_dep)],
) -> list[DocIconRead]:
    return await list_accessible_doc_icons(svc, files)


@router.put(
    "/doc-icons/{drive_id}",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_accessible_doc)],
    operation_id="set_doc_icon",
)
async def set_doc_icon(
    drive_id: Annotated[FileId, Path(min_length=1)],
    payload: Annotated[DocIconWrite, Body()],
    svc: Annotated[ExtrasService, Depends(extras_service_dep)],
) -> DocIconRead:
    return await svc.upsert_doc_icon(drive_id, payload)


@router.get(
    "/folder-icons",
    status_code=status.HTTP_200_OK,
    operation_id="list_folder_icons",
)
async def list_my_folder_icons(
    svc: Annotated[ExtrasService, Depends(extras_service_dep)],
) -> list[FolderIconRead]:
    return await svc.list_folder_icons()


@router.put(
    "/folder-icons/{folder_drive_id}",
    status_code=status.HTTP_200_OK,
    operation_id="set_folder_icon",
)
async def set_folder_icon(
    folder_drive_id: Annotated[FileId, Path(min_length=1)],
    payload: Annotated[FolderIconWrite, Body()],
    svc: Annotated[ExtrasService, Depends(extras_service_dep)],
) -> FolderIconRead:
    return await svc.upsert_folder_icon(folder_drive_id, payload)


@router.get(
    "/sort-order",
    status_code=status.HTTP_200_OK,
    operation_id="list_sort_orders",
)
async def list_my_sort_orders(
    svc: Annotated[ExtrasService, Depends(extras_service_dep)],
) -> list[SortOrderRead]:
    return await svc.list_sort_orders()


@router.put(
    "/sort-order",
    status_code=status.HTTP_200_OK,
    operation_id="set_sort_orders",
)
async def bulk_set_sort_orders(
    payload: Annotated[SortOrderBulkWrite, Body()],
    svc: Annotated[ExtrasService, Depends(extras_service_dep)],
) -> SortOrderBulkResult:
    await svc.upsert_sort_orders(payload.items)
    return SortOrderBulkResult(updated=len(payload.items))


@router.get(
    "/preferences",
    status_code=status.HTTP_200_OK,
    operation_id="read_preferences",
)
async def read_preferences(
    svc: Annotated[ExtrasService, Depends(extras_service_dep)],
) -> PreferencesRead:
    return await svc.get_preferences()


@router.put(
    "/preferences",
    status_code=status.HTTP_200_OK,
    operation_id="write_preferences",
)
async def write_preferences(
    payload: Annotated[PreferencesWrite, Body()],
    svc: Annotated[ExtrasService, Depends(extras_service_dep)],
) -> PreferencesRead:
    return await svc.upsert_preferences(payload)
