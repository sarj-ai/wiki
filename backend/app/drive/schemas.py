"""Drive DTOs used across Sarj Wiki backend.

Plain Pydantic models with no SQLAlchemy coupling so they can be serialized
straight into ``drive_tree_cache.tree`` JSONB and returned from API
endpoints without translation.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


# Google Workspace mime types Sarj Wiki displays.
MIME_FOLDER = "application/vnd.google-apps.folder"
MIME_DOC = "application/vnd.google-apps.document"
MIME_SHEET = "application/vnd.google-apps.spreadsheet"
MIME_SLIDE = "application/vnd.google-apps.presentation"
MIME_DRAWING = "application/vnd.google-apps.drawing"
MIME_FORM = "application/vnd.google-apps.form"
# Drive shortcuts: pointers with their own ACL whose target carries the
# real permission. We surface them as native nodes (folder or file
# rendering follows ``shortcut_target_mime_type``) with an overlay glyph.
MIME_SHORTCUT = "application/vnd.google-apps.shortcut"

NAVIGATOR_SUPPORTED_MIME_TYPES: tuple[str, ...] = (
    MIME_FOLDER,
    MIME_DOC,
    MIME_SHEET,
    MIME_SLIDE,
    MIME_DRAWING,
    MIME_FORM,
    # Microsoft Office — both modern (Open XML) and legacy formats.
    # All preview cleanly via Drive's ``/file/d/{id}/preview`` endpoint
    # and can be opened in the native Workspace editor via the
    # toolbar's "Open in Google" button.
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    # Common text formats — readable inline via Drive's preview, useful
    # to surface inside the wiki rather than forcing a Drive round-trip.
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
)


@dataclass(frozen=True)
class ShortcutProbe:
    accessible: bool
    target_drive_id: str | None = None


class DriveSummary(BaseModel):
    """A shared drive the user is a member of."""

    id: str
    name: str


class DriveNode(BaseModel):
    """A single node (file or folder) inside a drive's tree cache.

    ``parent_id`` is None for drive-root children (where the Drive API
    returns the drive id itself as the parent). Callers should treat
    root-level children as top-level items inside the drive.

    Shortcut fields are populated only when ``mime_type == MIME_SHORTCUT``.
    The frontend renders the shortcut using the *target's* mime icon plus
    an overlay glyph; ``shortcut_inaccessible`` flips the row to a greyed
    state when the user lacks permission on the target.
    """

    id: str
    name: str
    mime_type: str  # sarj-noqa: SARJ006 — open-ended Google Drive MIME type, not a fixed choice set
    # Required-but-nullable: always serialized, so the spec marks them required.
    parent_id: str | None
    modified_time: str | None
    shortcut_target_id: str | None = None
    shortcut_target_mime_type: str | None = None
    shortcut_target_drive_id: str | None = None
    shortcut_target_drive_name: str | None = None
    shortcut_inaccessible: bool = False


class DriveTree(BaseModel):
    """Serialized tree of a single shared drive.

    ``nodes`` is a flat list — the client rebuilds the hierarchy by
    joining ``id`` to ``parent_id``. Flat storage keeps the JSONB compact
    and avoids recursive serialization cost.
    """

    drive_id: str
    folders: list[DriveNode]
    files: list[DriveNode]
    files_complete: bool = True


class SearchResult(BaseModel):
    """One hit from a Drive Search API query.

    ``drive_name`` is populated when the result is returned from the
    multi-drive ``search()`` call (which already iterates the user's
    drives) so the client doesn't need a second round-trip to render
    the drive label next to each hit. Per-drive ``search_drive`` calls
    leave it ``None``.

    Shortcut entries (``mime_type == MIME_SHORTCUT``) carry the same
    ``shortcut_target_*`` fields as :class:`DriveNode` so the search UI
    can render the target's icon plus the overlay glyph.
    """

    id: str
    name: str
    mime_type: str  # sarj-noqa: SARJ006 — open-ended Google Drive MIME type, not a fixed choice set
    drive_id: str | None
    drive_name: str | None
    parent_id: str | None
    modified_time: str | None
    shortcut_target_id: str | None = None
    shortcut_target_mime_type: str | None = None
    shortcut_target_drive_name: str | None = None
    shortcut_inaccessible: bool = False


class SnippetResult(BaseModel):
    """Snippet fetched from a file's plain-text export."""

    file_id: str
    mime_type: str  # sarj-noqa: SARJ006 — open-ended Google Drive MIME type, not a fixed choice set
    snippet: str | None
    match_term: str | None
