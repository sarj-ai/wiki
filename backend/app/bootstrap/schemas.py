from __future__ import annotations

from pydantic import BaseModel

from app.auth.schemas import UserProfile
from app.drive.schemas import DriveSummary
from app.extras.schemas import DocIconRead, FolderIconRead, PreferencesRead, SortOrderRead


class BootstrapRead(BaseModel):
    drives: list[DriveSummary]
    preferences: PreferencesRead
    folder_icons: list[FolderIconRead]
    doc_icons: list[DocIconRead]
    sort_orders: list[SortOrderRead]
    me: UserProfile
