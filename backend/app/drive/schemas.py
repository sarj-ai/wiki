"""Drive DTOs used across the Navigator backend.

Plain Pydantic models with no SQLAlchemy coupling so they can be serialized
straight into ``drive_tree_cache.tree`` JSONB and returned from API
endpoints without translation.
"""

from __future__ import annotations

from pydantic import BaseModel

# Google Workspace mime types the Navigator displays.
MIME_FOLDER = "application/vnd.google-apps.folder"
MIME_DOC = "application/vnd.google-apps.document"
MIME_SHEET = "application/vnd.google-apps.spreadsheet"
MIME_SLIDE = "application/vnd.google-apps.presentation"
MIME_DRAWING = "application/vnd.google-apps.drawing"
MIME_FORM = "application/vnd.google-apps.form"

NAVIGATOR_SUPPORTED_MIME_TYPES: tuple[str, ...] = (
    MIME_FOLDER,
    MIME_DOC,
    MIME_SHEET,
    MIME_SLIDE,
    MIME_DRAWING,
    MIME_FORM,
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
)


class DriveSummary(BaseModel):
    """A shared drive the user is a member of."""

    id: str
    name: str


class DriveNode(BaseModel):
    """A single node (file or folder) inside a drive's tree cache.

    ``parent_id`` is None for drive-root children (where the Drive API
    returns the drive id itself as the parent). Callers should treat
    root-level children as top-level items inside the drive.
    """

    id: str
    name: str
    mime_type: str
    parent_id: str | None = None
    modified_time: str | None = None


class DriveTree(BaseModel):
    """Serialized tree of a single shared drive.

    ``nodes`` is a flat list — the client rebuilds the hierarchy by
    joining ``id`` to ``parent_id``. Flat storage keeps the JSONB compact
    and avoids recursive serialization cost.
    """

    drive_id: str
    folders: list[DriveNode] = []
    files: list[DriveNode] = []


class SearchResult(BaseModel):
    """One hit from a Drive Search API query.

    ``drive_name`` is populated when the result is returned from the
    multi-drive ``search()`` call (which already iterates the user's
    drives) so the client doesn't need a second round-trip to render
    the drive label next to each hit. Per-drive ``search_drive`` calls
    leave it ``None``.
    """

    id: str
    name: str
    mime_type: str
    drive_id: str | None = None
    drive_name: str | None = None
    parent_id: str | None = None
    modified_time: str | None = None


class SnippetResult(BaseModel):
    """Snippet fetched from a file's plain-text export."""

    file_id: str
    mime_type: str
    snippet: str | None = None
    match_term: str | None = None
