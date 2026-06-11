"""DTOs for the files router."""

from __future__ import annotations

from pydantic import BaseModel


class FileCreate(BaseModel):
    """Create a Google file of a given type inside a folder."""

    parent_folder_id: str
    name: str
    mime_type: str  # sarj-noqa: SARJ006 — open-ended Google Drive MIME type, not a fixed choice set


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
    mime_type: str  # sarj-noqa: SARJ006 — open-ended Google Drive MIME type, not a fixed choice set
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


class BreadcrumbItem(BaseModel):
    """One hop in a file's breadcrumb chain (drive → folders → file)."""

    id: str
    name: str


class FileDetail(FileResponse):
    """``FileResponse`` augmented with breadcrumb + drive name.

    Returned only by ``GET /files/{id}`` so the page can render the
    toolbar trail without a separate ``/sidebar/drives`` round-trip
    or a client-side parent walk over the cached tree.
    """

    drive_name: str | None = None
    breadcrumb: list[BreadcrumbItem] = []
