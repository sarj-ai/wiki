"""Aggregate DTO for the app shell's first paint."""

from __future__ import annotations

from pydantic import BaseModel

from app.auth.schemas import UserProfile
from app.drive.schemas import DriveSummary
from app.extras.schemas import DocIconRead, FolderIconRead, PreferencesRead, SortOrderRead


class BootstrapRead(BaseModel):
    """Everything the sidebar shell needs before it can paint.

    Mirrors the six per-resource GETs (``/sidebar/drives``,
    ``/extras/preferences``, ``/extras/folder-icons``,
    ``/extras/doc-icons``, ``/extras/sort-order``, ``/auth/me``) —
    those endpoints stay authoritative for client-side revalidation;
    this aggregate exists so the server-side prefetch pays one origin
    round-trip instead of six.
    """

    drives: list[DriveSummary]
    preferences: PreferencesRead
    folder_icons: list[FolderIconRead]
    doc_icons: list[DocIconRead]
    sort_orders: list[SortOrderRead]
    me: UserProfile
