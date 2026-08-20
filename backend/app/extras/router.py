from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.extras.dependencies import extras_service_dep
from app.extras.errors import DocIconNotFoundError
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
from app.identifiers import DriveId, FileId


router = APIRouter(prefix="/extras", tags=["extras"])


@router.get("/doc-icons")
async def list_doc_icons(
    svc: Annotated[ExtrasService, Depends(extras_service_dep)],
    files: Annotated[FilesService, Depends(files_service_dep)],
) -> list[DocIconRead]:
    return await list_accessible_doc_icons(svc, files)


@router.get("/doc-icons/{drive_id}", dependencies=[Depends(require_accessible_doc)])
async def read_doc_icon(
    drive_id: FileId,
    svc: Annotated[ExtrasService, Depends(extras_service_dep)],
) -> DocIconRead:
    try:
        return await svc.get_doc_icon(drive_id)
    except DocIconNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No icon") from e


@router.put("/doc-icons/{drive_id}", dependencies=[Depends(require_accessible_doc)])
async def set_doc_icon(
    drive_id: FileId,
    payload: DocIconWrite,
    svc: Annotated[ExtrasService, Depends(extras_service_dep)],
) -> DocIconRead:
    return await svc.upsert_doc_icon(drive_id, payload)


@router.get("/folder-icons")
async def list_my_folder_icons(
    svc: Annotated[ExtrasService, Depends(extras_service_dep)],
) -> list[FolderIconRead]:
    return await svc.list_folder_icons()


@router.put("/folder-icons/{folder_drive_id}")
async def set_folder_icon(
    folder_drive_id: FileId,
    payload: FolderIconWrite,
    svc: Annotated[ExtrasService, Depends(extras_service_dep)],
) -> FolderIconRead:
    return await svc.upsert_folder_icon(folder_drive_id, payload)


@router.get("/sort-order")
async def list_my_sort_orders(
    svc: Annotated[ExtrasService, Depends(extras_service_dep)],
) -> list[SortOrderRead]:
    return await svc.list_sort_orders()


@router.put("/sort-order")
async def bulk_set_sort_orders(
    payload: SortOrderBulkWrite,
    svc: Annotated[ExtrasService, Depends(extras_service_dep)],
) -> SortOrderBulkResult:
    await svc.upsert_sort_orders(payload.items)
    return SortOrderBulkResult(updated=len(payload.items))


@router.delete("/sort-order/{drive_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sort_order(
    drive_id: DriveId,
    svc: Annotated[ExtrasService, Depends(extras_service_dep)],
) -> None:
    await svc.clear_sort_order(drive_id)


@router.get("/preferences")
async def read_preferences(
    svc: Annotated[ExtrasService, Depends(extras_service_dep)],
) -> PreferencesRead:
    return await svc.get_preferences()


@router.put("/preferences")
async def write_preferences(
    payload: PreferencesWrite,
    svc: Annotated[ExtrasService, Depends(extras_service_dep)],
) -> PreferencesRead:
    return await svc.upsert_preferences(payload)
