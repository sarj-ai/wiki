"""File operations: create, rename, move, trash, batch check."""

from __future__ import annotations

import logging

from googleapiclient.errors import HttpError

from app.drive._helpers import _escape_drive_query
from app.drive.credentials import get_sa_drive_service
from app.drive.folders import ensure_folder
from app.config import settings
from app.models.user import User  # noqa: F401 — kept for backward-compat signatures

log = logging.getLogger(__name__)


def create_google_doc(
    user_email: str,
    title: str,
    *,
    is_private: bool,
    target_folder_id: str | None = None,
) -> tuple[str, str]:
    """Create a Google Doc in the correct Drive folder via service account.

    Returns (google_drive_id, drive_folder_id).
    """
    service = get_sa_drive_service()

    if target_folder_id is not None:
        folder_id = target_folder_id
    else:
        root_id = settings.DRIVE_ROOT_FOLDER_ID
        if is_private:
            private_root = ensure_folder(service, "My Drafts", root_id)
            user_folder = ensure_folder(service, user_email, private_root)
            folder_id = user_folder
        else:
            folder_id = root_id

    # Create the Google Doc
    doc_metadata = {
        "name": title,
        "mimeType": "application/vnd.google-apps.document",
        "parents": [folder_id],
    }
    doc = service.files().create(
        body=doc_metadata, fields="id", supportsAllDrives=True,
    ).execute()

    # Grant the creating user editor access
    doc_id = doc["id"]
    try:
        service.permissions().create(
            fileId=doc_id,
            body={
                "type": "user",
                "role": "writer",
                "emailAddress": user_email,
            },
            supportsAllDrives=True,
            sendNotificationEmail=False,
        ).execute()
    except Exception:
        log.warning("Failed to grant permission on %s to %s", doc_id, user_email)

    return doc_id, folder_id


def copy_google_doc(source_file_id: str, new_title: str, target_folder_id: str) -> str:
    """Copy a Google Doc via service account. Returns the new file ID."""
    service = get_sa_drive_service()
    doc = service.files().copy(
        fileId=source_file_id,
        body={"name": new_title, "parents": [target_folder_id]},
        fields="id",
        supportsAllDrives=True,
    ).execute()
    return doc["id"]


def rename_drive_file(user: User | None, file_id: str, new_name: str) -> None:
    """Rename a Drive file via service account."""
    service = get_sa_drive_service()
    service.files().update(
        fileId=file_id, body={"name": new_name}, supportsAllDrives=True,
    ).execute()


def rename_child_folder(user: User | None, old_name: str, new_name: str, parent_folder_id: str) -> None:
    """Rename a page's children folder in Drive (if it exists)."""
    service = get_sa_drive_service()
    safe_name = _escape_drive_query(old_name)
    safe_parent = _escape_drive_query(parent_folder_id)
    query = (
        f"name = '{safe_name}' "
        f"and '{safe_parent}' in parents "
        f"and mimeType = 'application/vnd.google-apps.folder' "
        f"and trashed = false"
    )
    results = service.files().list(
        q=query, fields="files(id)", spaces="drive",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute()
    files = results.get("files", [])
    if files:
        service.files().update(
            fileId=files[0]["id"], body={"name": new_name}, supportsAllDrives=True,
        ).execute()


def move_drive_file(user: User | None, file_id: str, new_parent_folder_id: str) -> None:
    """Move a Drive file to a different folder via service account."""
    service = get_sa_drive_service()
    file_meta = service.files().get(
        fileId=file_id, fields="parents", supportsAllDrives=True,
    ).execute()
    old_parents = ",".join(file_meta.get("parents", []))
    service.files().update(
        fileId=file_id,
        addParents=new_parent_folder_id,
        removeParents=old_parents,
        fields="id,parents",
        supportsAllDrives=True,
    ).execute()


def trash_drive_file(user: User | None, file_id: str) -> None:
    """Move a Drive file to the trash via service account."""
    service = get_sa_drive_service()
    service.files().update(
        fileId=file_id, body={"trashed": True}, supportsAllDrives=True,
    ).execute()


def is_drive_file_trashed(user: User | None, file_id: str) -> bool | None:
    """Check if a Drive file is trashed or deleted.

    Returns True if trashed, False if not, None if file doesn't exist.
    """
    service = get_sa_drive_service()
    try:
        meta = service.files().get(
            fileId=file_id, fields="trashed", supportsAllDrives=True,
        ).execute()
        return meta.get("trashed", False)
    except Exception:
        return None  # file doesn't exist or not accessible


_BATCH_SIZE = 100  # Google Batch API limit


def batch_check_trashed(user: User | None, file_ids: list[str]) -> dict[str, bool]:
    """Check multiple Drive files for trashed status.

    Returns a dict of {file_id: is_trashed_or_deleted}.
    Only files confirmed as 404 (not found) are treated as deleted.
    Transient errors (rate limits, timeouts) are excluded from results
    so the caller does NOT delete those pages.

    Automatically chunks into batches of 100 (Google Batch API limit).
    """
    service = get_sa_drive_service()
    results: dict[str, bool] = {}

    def make_callback(fid: str):
        def callback(request_id, response, exception):
            if exception:
                if isinstance(exception, HttpError) and exception.resp.status == 404:
                    results[fid] = True  # confirmed deleted
                else:
                    log.warning("batch_check_trashed: transient error for %s: %s", fid, exception)
            else:
                results[fid] = response.get("trashed", False)
        return callback

    for i in range(0, len(file_ids), _BATCH_SIZE):
        chunk = file_ids[i : i + _BATCH_SIZE]
        batch = service.new_batch_http_request()
        for fid in chunk:
            batch.add(
                service.files().get(fileId=fid, fields="trashed", supportsAllDrives=True),
                callback=make_callback(fid),
            )
        try:
            batch.execute()
        except Exception:
            log.warning("batch_check_trashed: batch.execute() failed for chunk %d", i, exc_info=True)

    return results
