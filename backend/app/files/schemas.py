from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, StringConstraints

from app.identifiers import DriveId, FileId


FileName = Annotated[str, StringConstraints(min_length=1, max_length=255)]


class FileCreate(BaseModel):
    parent_folder_id: FileId
    name: FileName
    mime_type: str  # sarj-noqa: SARJ006 — open-ended Google Drive MIME type, not a fixed choice set


class FileRename(BaseModel):
    name: FileName


class FileMove(BaseModel):
    new_parent_folder_id: FileId


class FileCopy(BaseModel):
    name: FileName


class FileResponse(BaseModel):
    id: FileId
    name: str
    mime_type: str  # sarj-noqa: SARJ006 — open-ended Google Drive MIME type, not a fixed choice set
    parent_folder_id: FileId | None
    drive_id: DriveId | None
    modified_time: str | None
    can_edit: bool = False


class MoveResult(BaseModel):
    source: FileResponse
    moved: FileResponse


class DeleteResult(BaseModel):
    source: FileResponse


class BreadcrumbItem(BaseModel):
    id: FileId
    name: str


class FileDetail(FileResponse):
    drive_name: str | None
    breadcrumb: list[BreadcrumbItem]
