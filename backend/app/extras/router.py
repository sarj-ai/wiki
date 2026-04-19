"""HTTP surface for Navigator UI overlays.

Every route is a thin pass-through into ``ExtrasService``. No Drive
calls here — this surface is DB-only.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.extras.dependencies import extras_service_dep
from app.extras.schemas import (
    DocIconRead,
    DocIconWrite,
    FolderIconRead,
    FolderIconWrite,
    PreferencesRead,
    PreferencesWrite,
    SortOrderBulkWrite,
    SortOrderRead,
)
from app.extras.service import ExtrasService

router = APIRouter(prefix="/extras", tags=["extras"])


@router.get("/doc-icons")
async def list_doc_icons(
    svc: ExtrasService = Depends(extras_service_dep),
) -> list[DocIconRead]:
    return await svc.list_doc_icons()


@router.get("/doc-icons/{drive_id}")
async def read_doc_icon(
    drive_id: str,
    svc: ExtrasService = Depends(extras_service_dep),
) -> DocIconRead:
    icon = await svc.get_doc_icon(drive_id)
    if icon is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No icon")
    return icon


@router.put("/doc-icons/{drive_id}")
async def set_doc_icon(
    drive_id: str,
    payload: DocIconWrite,
    svc: ExtrasService = Depends(extras_service_dep),
) -> DocIconRead:
    return await svc.upsert_doc_icon(drive_id, payload)


@router.get("/folder-icons")
async def list_my_folder_icons(
    svc: ExtrasService = Depends(extras_service_dep),
) -> list[FolderIconRead]:
    return await svc.list_folder_icons()


@router.put("/folder-icons/{folder_drive_id}")
async def set_folder_icon(
    folder_drive_id: str,
    payload: FolderIconWrite,
    svc: ExtrasService = Depends(extras_service_dep),
) -> FolderIconRead:
    return await svc.upsert_folder_icon(folder_drive_id, payload)


@router.get("/sort-order")
async def list_my_sort_orders(
    svc: ExtrasService = Depends(extras_service_dep),
) -> list[SortOrderRead]:
    return await svc.list_sort_orders()


@router.put("/sort-order")
async def bulk_set_sort_orders(
    payload: SortOrderBulkWrite,
    svc: ExtrasService = Depends(extras_service_dep),
) -> dict[str, int]:
    await svc.upsert_sort_orders(payload.items)
    return {"updated": len(payload.items)}


@router.delete("/sort-order/{drive_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sort_order(
    drive_id: str,
    svc: ExtrasService = Depends(extras_service_dep),
) -> None:
    await svc.clear_sort_order(drive_id)


@router.get("/preferences")
async def read_preferences(
    svc: ExtrasService = Depends(extras_service_dep),
) -> PreferencesRead:
    return await svc.get_preferences()


@router.put("/preferences")
async def write_preferences(
    payload: PreferencesWrite,
    svc: ExtrasService = Depends(extras_service_dep),
) -> PreferencesRead:
    return await svc.upsert_preferences(payload)
