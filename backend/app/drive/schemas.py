from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.identifiers import DriveId, FileId


MIME_FOLDER = "application/vnd.google-apps.folder"
MIME_DOC = "application/vnd.google-apps.document"
MIME_SHEET = "application/vnd.google-apps.spreadsheet"
MIME_SLIDE = "application/vnd.google-apps.presentation"
MIME_DRAWING = "application/vnd.google-apps.drawing"
MIME_FORM = "application/vnd.google-apps.form"
MIME_SHORTCUT = "application/vnd.google-apps.shortcut"
MIME_HTML = "text/html"

NAVIGATOR_SUPPORTED_MIME_TYPES: tuple[str, ...] = (
    MIME_FOLDER,
    MIME_DOC,
    MIME_SHEET,
    MIME_SLIDE,
    MIME_DRAWING,
    MIME_FORM,
    MIME_HTML,
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
    target_drive_id: DriveId | None = None


class DriveSummary(BaseModel):
    id: DriveId
    name: str
    is_personal: bool = False


class DrivePermissionDetail(BaseModel):
    permission_type: str
    role: str
    inherited: bool


class DrivePermission(BaseModel):
    id: str
    principal_type: str
    role: str
    display_name: str | None = None
    email_address: str | None = None
    domain: str | None = None
    allow_file_discovery: bool | None = None
    expiration_time: str | None = None
    deleted: bool = False
    pending_owner: bool = False
    inherited: bool = False
    permission_details: tuple[DrivePermissionDetail, ...] = ()


class DrivePermissionsPage(BaseModel):
    drive_id: DriveId
    access_model: str
    permissions: list[DrivePermission]
    next_page_token: str | None = None


class FilePermissionSnapshot(BaseModel):
    file_id: FileId
    mime_type: str
    drive_id: DriveId | None
    parent_ids: tuple[FileId, ...]
    version: str
    owned_by_me: bool
    shared: bool
    can_add_children: bool
    owner_emails: tuple[str, ...]
    permissions: tuple[DrivePermission, ...]
    app_properties: dict[str, str] = Field(default_factory=dict)


class DriveNode(BaseModel):
    id: FileId
    name: str
    mime_type: str
    parent_id: FileId | None
    modified_time: str | None
    version: str | None = Field(default=None, exclude=True)
    drive_id: DriveId | None = Field(default=None, exclude=True)
    description: str | None = None
    shortcut_target_id: FileId | None = None
    shortcut_target_mime_type: str | None = None
    shortcut_target_drive_id: DriveId | None = None
    shortcut_target_drive_name: str | None = None
    shortcut_inaccessible: bool = False


class DriveTree(BaseModel):
    drive_id: DriveId
    folders: list[DriveNode]
    files: list[DriveNode]
    files_complete: bool


class BoundedFolderMembers(BaseModel):
    members: list[DriveNode]
    page_count: int = Field(ge=1)
    metadata_bytes: int = Field(default=0, ge=0)


class SearchResult(BaseModel):
    id: FileId
    name: str
    mime_type: str
    drive_id: DriveId | None
    drive_name: str | None
    parent_id: FileId | None
    modified_time: str | None
    shortcut_target_id: FileId | None = None
    shortcut_target_mime_type: str | None = None
    shortcut_target_drive_name: str | None = None
    shortcut_inaccessible: bool = False


class SnippetResult(BaseModel):
    file_id: FileId
    mime_type: str
    snippet: str | None
    match_term: str | None
