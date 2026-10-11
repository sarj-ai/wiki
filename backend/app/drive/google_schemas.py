from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.identifiers import DriveId, FileId


class _GoogleModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class GoogleShortcutDetails(_GoogleModel):
    target_id: FileId | None = Field(default=None, alias="targetId")
    target_mime_type: str | None = Field(default=None, alias="targetMimeType")


class GoogleFileCapabilities(_GoogleModel):
    model_config = ConfigDict(frozen=True)

    can_edit: bool = Field(default=False, alias="canEdit")
    can_add_children: bool = Field(default=False, alias="canAddChildren")


_EMPTY_CAPABILITIES = GoogleFileCapabilities()


class GoogleFile(_GoogleModel):
    id: FileId
    name: str
    mime_type: str = Field(alias="mimeType")
    drive_id: DriveId | None = Field(default=None, alias="driveId")
    parents: tuple[FileId, ...] = ()
    modified_time: str | None = Field(default=None, alias="modifiedTime")
    version: str | None = None
    description: str | None = None
    trashed: bool = False
    shortcut_details: GoogleShortcutDetails | None = Field(default=None, alias="shortcutDetails")
    capabilities: GoogleFileCapabilities = Field(default=_EMPTY_CAPABILITIES)


class GoogleDrive(_GoogleModel):
    id: DriveId
    name: str
    hidden: bool = False


class GooglePermissionDetail(_GoogleModel):
    permission_type: Literal["file", "member"] = Field(alias="permissionType")
    role: Literal["owner", "organizer", "fileOrganizer", "writer", "commenter", "reader"]
    inherited: bool


class GooglePermission(_GoogleModel):
    id: str
    type: str
    role: str
    display_name: str | None = Field(default=None, alias="displayName")
    email_address: str | None = Field(default=None, alias="emailAddress")
    domain: str | None = None
    allow_file_discovery: bool | None = Field(default=None, alias="allowFileDiscovery")
    expiration_time: str | None = Field(default=None, alias="expirationTime")
    deleted: bool = False
    pending_owner: bool = Field(default=False, alias="pendingOwner")
    permission_details: list[GooglePermissionDetail] = Field(
        default_factory=list,
        alias="permissionDetails",
    )


class GoogleOwner(_GoogleModel):
    email_address: str = Field(alias="emailAddress")


class GoogleFilePermissionMetadata(_GoogleModel):
    id: FileId
    mime_type: str = Field(alias="mimeType")
    drive_id: DriveId | None = Field(default=None, alias="driveId")
    parents: list[FileId] = Field(default_factory=list)
    version: str
    owned_by_me: bool = Field(alias="ownedByMe")
    shared: bool
    capabilities: GoogleFileCapabilities
    owners: list[GoogleOwner] = Field(default_factory=list)
    app_properties: dict[str, str] = Field(default_factory=dict, alias="appProperties")


class GoogleFileProbe(_GoogleModel):
    trashed: bool = False
    drive_id: DriveId | None = Field(default=None, alias="driveId")


class GoogleFileMetadata(_GoogleModel):
    id: FileId
    mime_type: str = Field(alias="mimeType")


class GoogleFileLocation(_GoogleModel):
    id: FileId
    drive_id: DriveId | None = Field(default=None, alias="driveId")
    parents: list[FileId] = Field(default_factory=list)


class GoogleFileAccessLocation(GoogleFileLocation):
    trashed: bool = False
