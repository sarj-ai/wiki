from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _GoogleModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class GoogleShortcutDetails(_GoogleModel):
    target_id: str | None = Field(default=None, alias="targetId")
    target_mime_type: str | None = Field(default=None, alias="targetMimeType")


class GoogleFile(_GoogleModel):
    id: str
    name: str
    mime_type: str = Field(alias="mimeType")
    drive_id: str | None = Field(default=None, alias="driveId")
    parents: list[str] = Field(default_factory=list)
    modified_time: str | None = Field(default=None, alias="modifiedTime")
    shortcut_details: GoogleShortcutDetails | None = Field(default=None, alias="shortcutDetails")


class GoogleDrive(_GoogleModel):
    id: str | None = None
    name: str | None = None
    hidden: bool = False


class GoogleDriveListPage(_GoogleModel):
    drives: list[GoogleDrive] = Field(default_factory=list)
    next_page_token: str | None = Field(default=None, alias="nextPageToken")


class GoogleFileListPage(_GoogleModel):
    files: list[GoogleFile] = Field(default_factory=list)
    next_page_token: str | None = Field(default=None, alias="nextPageToken")
    incomplete_results: bool = Field(default=False, alias="incompleteResults")


class GoogleFileParents(_GoogleModel):
    parents: list[str] = Field(default_factory=list)


class GoogleFileProbe(_GoogleModel):
    trashed: bool = False
    drive_id: str | None = Field(default=None, alias="driveId")


class GoogleFileMetadata(_GoogleModel):
    id: str
    mime_type: str = Field(alias="mimeType")
