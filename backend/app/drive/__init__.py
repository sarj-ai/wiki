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
    # credentials
    "build_credentials",
    "get_drive_service",
    "get_sa_drive_service",
    # helpers
    "_escape_drive_query",
    # folders
    "ensure_folder",
    "create_folder",
    "is_drive_folder_empty",
    # files
    "create_google_doc",
    "rename_drive_file",
    "rename_child_folder",
    "move_drive_file",
    "trash_drive_file",
    "is_drive_file_trashed",
    "batch_check_trashed",
    # changes
    "get_drive_changes_start_token",
    "get_drive_changes",
    # content
    "get_doc_content",
    "export_doc_html",
]
