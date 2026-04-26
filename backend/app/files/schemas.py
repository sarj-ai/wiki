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


class MoveResult(BaseModel):
    """Composite move result so callers can invalidate both ends.

    ``source`` is the file as it was before the move (carries the
    original ``drive_id``); ``moved`` is the post-move state.
    """

    source: FileResponse
    moved: FileResponse


class DeleteResult(BaseModel):
    source: FileResponse
