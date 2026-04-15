"""HTTP surface for Navigator UI overlays.

All routes are user-scoped and thin: every handler maps straight to
a function in ``app.extras.service``. Drive state is not touched
here — these are DB-only endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.extras import service
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
from app.models.user import User

router = APIRouter(prefix="/extras", tags=["extras"])


@router.get("/doc-icons")
async def list_doc_icons(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[DocIconRead]:
    return await service.list_doc_icons(db)


@router.get("/doc-icons/{drive_id}")
async def read_doc_icon(
    drive_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> DocIconRead:
    icon = await service.get_doc_icon(db, drive_id)
    if icon is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No icon")
    return icon


@router.put("/doc-icons/{drive_id}")
async def set_doc_icon(
    drive_id: str,
    payload: DocIconWrite,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DocIconRead:
    return await service.upsert_doc_icon(db, drive_id, payload, user)


@router.get("/folder-icons")
async def list_my_folder_icons(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[FolderIconRead]:
    return await service.list_folder_icons(db, user)


@router.put("/folder-icons/{folder_drive_id}")
async def set_folder_icon(
    folder_drive_id: str,
    payload: FolderIconWrite,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FolderIconRead:
    return await service.upsert_folder_icon(db, user, folder_drive_id, payload)


@router.get("/sort-order")
async def list_my_sort_orders(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[SortOrderRead]:
    return await service.list_sort_orders(db, user)


@router.put("/sort-order")
async def bulk_set_sort_orders(
    payload: SortOrderBulkWrite,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, int]:
    await service.upsert_sort_orders(db, user, payload.items)
    return {"updated": len(payload.items)}


@router.delete("/sort-order/{drive_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sort_order(
    drive_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    await service.clear_sort_order(db, user, drive_id)


@router.get("/preferences")
async def read_preferences(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PreferencesRead:
    return await service.get_preferences(db, user)


@router.put("/preferences")
async def write_preferences(
    payload: PreferencesWrite,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PreferencesRead:
    return await service.upsert_preferences(db, user, payload)
