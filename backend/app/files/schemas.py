from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, StringConstraints


FileName = Annotated[str, StringConstraints(min_length=1, max_length=255)]


class FileCreate(BaseModel):
    parent_folder_id: str
    name: FileName
    mime_type: str  # sarj-noqa: SARJ006 — open-ended Google Drive MIME type, not a fixed choice set


class FileRename(BaseModel):
    name: FileName


class FileMove(BaseModel):
    new_parent_folder_id: str


class FileCopy(BaseModel):
    name: FileName


class FileResponse(BaseModel):
    id: str
    name: str
    mime_type: str  # sarj-noqa: SARJ006 — open-ended Google Drive MIME type, not a fixed choice set
    parent_folder_id: str | None
    drive_id: str | None
    modified_time: str | None
    can_edit: bool = False


class MoveResult(BaseModel):
    source: FileResponse
    moved: FileResponse


class DeleteResult(BaseModel):
    source: FileResponse


class BreadcrumbItem(BaseModel):
    id: str
    name: str


class FileDetail(FileResponse):
    drive_name: str | None
    breadcrumb: list[BreadcrumbItem]
