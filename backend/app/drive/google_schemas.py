from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _GoogleModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class GoogleShortcutDetails(_GoogleModel):
    target_id: str | None = Field(default=None, alias="targetId")
    target_mime_type: str | None = Field(default=None, alias="targetMimeType")


class GoogleFileCapabilities(_GoogleModel):
    can_edit: bool = Field(default=False, alias="canEdit")


class GoogleFile(_GoogleModel):
    id: str
    name: str
    mime_type: str = Field(alias="mimeType")  # sarj-noqa: SARJ006 — raw Google API MIME type
    drive_id: str | None = Field(default=None, alias="driveId")
    parents: list[str] = Field(default_factory=list)
    modified_time: str | None = Field(default=None, alias="modifiedTime")
    shortcut_details: GoogleShortcutDetails | None = Field(default=None, alias="shortcutDetails")
    capabilities: GoogleFileCapabilities = Field(default_factory=GoogleFileCapabilities)


class GoogleDrive(_GoogleModel):
    id: str
    name: str
    hidden: bool = False


class GoogleFileProbe(_GoogleModel):
    trashed: bool = False
    drive_id: str | None = Field(default=None, alias="driveId")


class GoogleFileMetadata(_GoogleModel):
    id: str
    mime_type: str = Field(alias="mimeType")  # sarj-noqa: SARJ006 — raw Google API MIME type
