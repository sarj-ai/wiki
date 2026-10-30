from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.identifiers import DriveId, FileId


FileName = Annotated[str, StringConstraints(min_length=1, max_length=255)]
RecentFileId = Annotated[
    FileId, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
]


class FileCreate(BaseModel):
    parent_folder_id: FileId
    name: FileName
    mime_type: str


class FileRename(BaseModel):
    name: FileName


class FileMove(BaseModel):
    new_parent_folder_id: FileId


class FileCopy(BaseModel):
    name: FileName


class FileResponse(BaseModel):
    id: FileId
    name: str
    mime_type: str
    parent_folder_id: FileId | None
    drive_id: DriveId | None
    modified_time: str | None
    version: str | None = Field(default=None, exclude=True)
    can_edit: bool = False
    can_add_children: bool = False
    description: str | None = None


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


class RecentFile(BaseModel):
    id: FileId
    name: str
    mime_type: str
    drive_name: str | None


class RecentFilesResponse(BaseModel):
    pages: list[RecentFile] = Field(max_length=5)
