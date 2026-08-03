from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.identifiers import DriveId, FileId


class _GoogleModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class GoogleShortcutDetails(_GoogleModel):
    target_id: FileId | None = Field(default=None, alias="targetId")
    target_mime_type: str | None = Field(default=None, alias="targetMimeType")


class GoogleFileCapabilities(_GoogleModel):
    can_edit: bool = Field(default=False, alias="canEdit")
    can_add_children: bool = Field(default=False, alias="canAddChildren")


class GoogleFile(_GoogleModel):
    id: FileId
    name: str
    mime_type: str = Field(alias="mimeType")
    drive_id: DriveId | None = Field(default=None, alias="driveId")
    parents: list[FileId] = Field(default_factory=list)
    modified_time: str | None = Field(default=None, alias="modifiedTime")
    description: str | None = None
    shortcut_details: GoogleShortcutDetails | None = Field(default=None, alias="shortcutDetails")
    capabilities: GoogleFileCapabilities = Field(default_factory=GoogleFileCapabilities)


class GoogleDrive(_GoogleModel):
    id: DriveId
    name: str
    hidden: bool = False


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
