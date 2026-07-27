from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.auth.dependencies import get_current_user
from app.drive.dependencies import drive_service_dep
from app.drive.service import DriveService
from app.files.service import FilesService, GoogleFilesService
from app.models.user import User


def files_service_dep(
    user: Annotated[User, Depends(get_current_user)],
    drive: Annotated[DriveService, Depends(drive_service_dep)],
) -> FilesService:
    return GoogleFilesService(user=user, drive=drive)


async def require_accessible_doc(
    drive_id: str,
    files: Annotated[FilesService, Depends(files_service_dep)],
) -> str:
    """IDOR guard for the global ``doc_icons`` table, keyed by document id.

    The ``{drive_id}`` path param is historically named but holds a Google
    *document* id, not a shared-drive id. ``FilesService.get`` resolves the
    doc and enforces member-drive scope, raising ``FileNotInScopeError``
    (404) for anything the caller can't reach — so a user can only write a
    doc's global icon if they can actually open the doc. (The prior
    ``require_member_drive`` tested the doc id against the shared-drive
    list, which never matched, so every doc-icon write 404'd.)
    """
    await files.get(drive_id)
    return drive_id
