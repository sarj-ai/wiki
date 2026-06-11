"""Request/response DTOs for the extras router."""

from __future__ import annotations

from pydantic import BaseModel


class DocIconWrite(BaseModel):
    """Set a Google Doc's icon and/or cover (global)."""

    icon: str | None = None
    cover_url: str | None = None


class DocIconRead(BaseModel):
    """Response shape — decoupled from the Write payload so fields are required."""

    drive_id: str
    icon: str | None
    cover_url: str | None


class FolderIconWrite(BaseModel):
    """Per-user folder icon override."""

    icon: str | None = None


class FolderIconRead(BaseModel):
    folder_drive_id: str
    icon: str | None


class SortOrderWrite(BaseModel):
    """Single fractional-index drag-drop update."""

    drive_id: str
    sort_order: float


class SortOrderBulkWrite(BaseModel):
    """Bulk reorder: one update per moved item."""

    items: list[SortOrderWrite] = []


class SortOrderRead(BaseModel):
    drive_id: str
    sort_order: float


class SortOrderBulkResult(BaseModel):
    """Typed response for the bulk reorder endpoint."""

    updated: int


class PreferencesWrite(BaseModel):
    show_non_doc_files: bool | None = None
    hidden_workspace_ids: list[str] | None = None


class PreferencesRead(BaseModel):
    show_non_doc_files: bool
    hidden_workspace_ids: list[str]
