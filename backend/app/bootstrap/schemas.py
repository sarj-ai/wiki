from __future__ import annotations

from pydantic import BaseModel

from app.auth.schemas import UserProfile
from app.drive.schemas import DriveSummary, DriveTree
from app.extras.schemas import DocIconRead, FolderIconRead, PreferencesRead, SortOrderRead


class BootstrapRead(BaseModel):
    drives: list[DriveSummary]
    preferences: PreferencesRead
    folder_icons: list[FolderIconRead]
    doc_icons: list[DocIconRead]
    sort_orders: list[SortOrderRead]
    first_drive_tree: DriveTree | None
    me: UserProfile
