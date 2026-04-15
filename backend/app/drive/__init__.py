"""Google Drive helpers — create docs, folders, move files.

Re-exports all public names so ``from app.drive import X`` still works.
"""

from app.drive._helpers import _escape_drive_query
from app.drive.changes import get_drive_changes, get_drive_changes_start_token
from app.drive.content import export_doc_html, get_doc_content
from app.drive.credentials import (
    build_credentials,
    get_drive_service,
    get_sa_drive_service,
)
from app.drive.files import (
    batch_check_trashed,
    copy_google_doc,
    create_google_doc,
    is_drive_file_trashed,
    move_drive_file,
    rename_child_folder,
    rename_drive_file,
    trash_drive_file,
)
from app.drive.folders import (
    create_folder,
    ensure_folder,
    is_drive_folder_empty,
)

__all__ = [
    "_escape_drive_query",
    "batch_check_trashed",
    "build_credentials",
    "copy_google_doc",
    "create_folder",
    "create_google_doc",
    "ensure_folder",
    "export_doc_html",
    "get_doc_content",
    "get_drive_changes",
    "get_drive_changes_start_token",
    "get_drive_service",
    "get_sa_drive_service",
    "is_drive_file_trashed",
    "is_drive_folder_empty",
    "move_drive_file",
    "rename_child_folder",
    "rename_drive_file",
    "trash_drive_file",
]
