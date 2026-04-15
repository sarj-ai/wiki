"""HTTP surface for the progressive sidebar loader.

See PIVOT_PLAN.md §4.2 for the UX contract. The frontend calls this
router three times per cold load:

1. ``GET /sidebar/drives`` — lists every shared drive the user is a
   member of. ~1.3s cold. Used as the access-control boundary.
2. ``GET /sidebar/drives/{drive_id}/folders`` — folder tree for one
   drive. Warm ~50ms, cold ~6s on the largest Sarj drive. Fired in
   parallel per drive; no drive waits on another.
3. ``GET /sidebar/folders/{folder_id}/files`` — lazy file listing on
   folder expand. ~1.3s each, coalesced via singleflight.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.drive.drives import list_user_drives
from app.drive.schemas import DriveNode, DriveSummary, DriveTree
from app.models.user import User
from app.sidebar.cache import get_drive_tree, get_folder_children

router = APIRouter(prefix="/sidebar", tags=["sidebar"])


@router.get("/drives")
async def get_drives(
    user: User = Depends(get_current_user),
) -> list[DriveSummary]:
    return await list_user_drives(user)


@router.get("/drives/{drive_id}/folders")
async def get_drive_folders(
    drive_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DriveTree:
    return await get_drive_tree(db, user, drive_id)


@router.get("/folders/{folder_id}/files")
async def get_folder_files(
    folder_id: str,
    user: User = Depends(get_current_user),
) -> list[DriveNode]:
    return await get_folder_children(user, folder_id)
