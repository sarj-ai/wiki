"""DTOs for the files router."""

from __future__ import annotations

from pydantic import BaseModel


class FileCreate(BaseModel):
    """Create a Google file of a given type inside a folder."""

    parent_folder_id: str
    name: str
    mime_type: str


class FileRename(BaseModel):
    name: str


class FileMove(BaseModel):
    new_parent_folder_id: str


class FileCopy(BaseModel):
    """Duplicate a file — the copy inherits the source's parent folder."""

    name: str


class FileResponse(BaseModel):
    id: str
    name: str
    mime_type: str
    parent_folder_id: str | None = None
    drive_id: str | None = None
    modified_time: str | None = None
