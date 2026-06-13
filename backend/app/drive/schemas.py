from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


MIME_FOLDER = "application/vnd.google-apps.folder"
MIME_DOC = "application/vnd.google-apps.document"
MIME_SHEET = "application/vnd.google-apps.spreadsheet"
MIME_SLIDE = "application/vnd.google-apps.presentation"
MIME_DRAWING = "application/vnd.google-apps.drawing"
MIME_FORM = "application/vnd.google-apps.form"
MIME_SHORTCUT = "application/vnd.google-apps.shortcut"

NAVIGATOR_SUPPORTED_MIME_TYPES: tuple[str, ...] = (
    MIME_FOLDER,
    MIME_DOC,
    MIME_SHEET,
    MIME_SLIDE,
    MIME_DRAWING,
    MIME_FORM,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
)


@dataclass(frozen=True)
class ShortcutProbe:
    accessible: bool
    target_drive_id: str | None = None


class DriveSummary(BaseModel):
    id: str
    name: str


class DriveNode(BaseModel):
    id: str
    name: str
    mime_type: str  # sarj-noqa: SARJ006 — open-ended Google Drive MIME type, not a fixed choice set
    parent_id: str | None
    modified_time: str | None
    shortcut_target_id: str | None = None
    shortcut_target_mime_type: str | None = None
    shortcut_target_drive_id: str | None = None
    shortcut_target_drive_name: str | None = None
    shortcut_inaccessible: bool = False


class DriveTree(BaseModel):
    drive_id: str
    folders: list[DriveNode]
    files: list[DriveNode]
    files_complete: bool


class SearchResult(BaseModel):
    id: str
    name: str
    mime_type: str  # sarj-noqa: SARJ006 — open-ended Google Drive MIME type, not a fixed choice set
    drive_id: str | None
    drive_name: str | None
    parent_id: str | None
    modified_time: str | None
    shortcut_target_id: str | None = None
    shortcut_target_mime_type: str | None = None
    shortcut_target_drive_name: str | None = None
    shortcut_inaccessible: bool = False


class SnippetResult(BaseModel):
    file_id: str
    mime_type: str  # sarj-noqa: SARJ006 — open-ended Google Drive MIME type, not a fixed choice set
    snippet: str | None
    match_term: str | None
