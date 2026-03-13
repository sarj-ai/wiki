"""Google Drive helpers — create docs, folders, move files.

Write operations (create, move, rename, trash) use the service account
which is a Manager of the Shared Drive. Read operations (content, export)
use the user's own OAuth token.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading

from fastapi import HTTPException
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.auth.encryption import decrypt_token
from app.config import settings
from app.models.user import User

log = logging.getLogger(__name__)

_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]

# In-memory cache for folder IDs to avoid repeated Drive API lookups
_folder_cache: dict[tuple[str, str], str] = {}  # (name, parent_id) -> folder_id

# Thread-local service instances AND credentials — httplib2 is NOT thread-safe
# for concurrent requests, and Credentials are mutated during token refresh.
# Each thread gets its own isolated copies.
_thread_local = threading.local()

# Semaphore to cap concurrent Google API threads (prevents resource exhaustion)
google_api_semaphore = asyncio.Semaphore(3)

# Dedicated executor for content fetches so sync can't starve them
content_executor = concurrent.futures.ThreadPoolExecutor(max_workers=3, thread_name_prefix="content")


def build_credentials(user: User) -> Credentials:
    """Build Google OAuth credentials from a user's encrypted tokens."""
    access_token = decrypt_token(user.encrypted_access_token)
    refresh_token = (
        decrypt_token(user.encrypted_refresh_token)
        if user.encrypted_refresh_token
        else None
    )
    return Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
    )


def get_drive_service(user: User):
    """Build an authenticated Drive v3 service for the given user (reads only)."""
    return build("drive", "v3", credentials=build_credentials(user), static_discovery=True)


def _get_sa_credentials():
    """Get thread-local service account credentials.

    Each thread gets its own Credentials instance because token refresh
    mutates the object (self.token, self.expiry) which is not thread-safe.
    """
    creds = getattr(_thread_local, "sa_credentials", None)
    if creds is not None:
        return creds
    creds = service_account.Credentials.from_service_account_file(
        settings.SERVICE_ACCOUNT_KEY_PATH,
        scopes=_DRIVE_SCOPES,
    )
    _thread_local.sa_credentials = creds
    return creds


def get_sa_drive_service():
    """Build a Drive v3 service using the service account's own credentials.

    Used for all write operations (create, move, rename, trash) on the
    Shared Drive where the service account is a Manager.

    Returns a thread-local instance to avoid httplib2 concurrency crashes.
    """
    svc = getattr(_thread_local, "sa_drive_service", None)
    if svc is not None:
        return svc
    svc = build("drive", "v3", credentials=_get_sa_credentials(), static_discovery=True)
    _thread_local.sa_drive_service = svc
    return svc


def _escape_drive_query(value: str) -> str:
    """Escape a value for use in a Drive API query string."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def ensure_folder(service, name: str, parent_id: str) -> str:
    """Find or create a folder with the given name under parent_id.

    Results are cached in memory to avoid repeated API calls.
    Returns the folder's Drive ID.
    """
    cache_key = (name, parent_id)
    if cache_key in _folder_cache:
        return _folder_cache[cache_key]

    safe_name = _escape_drive_query(name)
    safe_parent = _escape_drive_query(parent_id)
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
        _folder_cache[cache_key] = files[0]["id"]
        return files[0]["id"]

    # Create the folder
    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = service.files().create(
        body=metadata, fields="id", supportsAllDrives=True,
    ).execute()
    _folder_cache[cache_key] = folder["id"]
    return folder["id"]


def create_folder(user: User, name: str, parent_folder_id: str) -> str:
    """Create a new folder in Drive via service account.

    Returns the new folder's Drive ID.
    """
    service = get_sa_drive_service()
    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_folder_id],
    }
    folder = service.files().create(
        body=metadata, fields="id", supportsAllDrives=True,
    ).execute()
    return folder["id"]


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
            private_root = ensure_folder(service, "Private", root_id)
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


def rename_drive_file(user: User, file_id: str, new_name: str) -> None:
    """Rename a Drive file via service account."""
    service = get_sa_drive_service()
    service.files().update(
        fileId=file_id, body={"name": new_name}, supportsAllDrives=True,
    ).execute()


def rename_child_folder(user: User, old_name: str, new_name: str, parent_folder_id: str) -> None:
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


def move_drive_file(user: User, file_id: str, new_parent_folder_id: str) -> None:
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


def trash_drive_file(user: User, file_id: str) -> None:
    """Move a Drive file to the trash via service account."""
    service = get_sa_drive_service()
    service.files().update(
        fileId=file_id, body={"trashed": True}, supportsAllDrives=True,
    ).execute()


def is_drive_folder_empty(folder_id: str) -> bool:
    """Check if a Drive folder contains any non-trashed files.

    Returns True if the folder is empty (or doesn't exist), False otherwise.
    """
    service = get_sa_drive_service()
    try:
        resp = service.files().list(
            q=f"'{_escape_drive_query(folder_id)}' in parents and trashed = false",
            fields="files(id)",
            spaces="drive",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageSize=1,
        ).execute()
        return len(resp.get("files", [])) == 0
    except Exception:
        return False  # On error, assume not empty (don't trash)


def is_drive_file_trashed(user: User, file_id: str) -> bool | None:
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


def batch_check_trashed(user: User, file_ids: list[str]) -> dict[str, bool]:
    """Check multiple Drive files for trashed status.

    Returns a dict of {file_id: is_trashed_or_deleted}.
    Only files confirmed as 404 (not found) are treated as deleted.
    Transient errors (rate limits, timeouts) are excluded from results
    so the caller does NOT delete those pages.
    """
    service = get_sa_drive_service()
    results: dict[str, bool] = {}

    batch = service.new_batch_http_request()

    def make_callback(fid: str):
        def callback(request_id, response, exception):
            if exception:
                if isinstance(exception, HttpError) and exception.resp.status == 404:
                    results[fid] = True  # confirmed deleted
                else:
                    # Transient error — do NOT mark as deleted, just skip
                    log.warning("batch_check_trashed: transient error for %s: %s", fid, exception)
            else:
                results[fid] = response.get("trashed", False)
        return callback

    for fid in file_ids:
        batch.add(
            service.files().get(fileId=fid, fields="trashed", supportsAllDrives=True),
            callback=make_callback(fid),
        )

    try:
        batch.execute()
    except Exception:
        log.warning("batch_check_trashed: batch.execute() failed", exc_info=True)
        return {}
    return results


def get_drive_changes_start_token() -> str:
    """Get the current start page token for Drive changes tracking.

    Uses the shared drive ID so we only track changes within our Shared Drive.
    """
    service = get_sa_drive_service()
    response = service.changes().getStartPageToken(
        supportsAllDrives=True,
        driveId=settings.DRIVE_ROOT_FOLDER_ID,
    ).execute()
    return response["startPageToken"]


def get_drive_changes(start_token: str) -> tuple[list[dict], str]:
    """Fetch all changes since the given start token.

    Returns (list_of_changes, new_start_token).
    Each change has: fileId, file (metadata or None if removed), removed (bool).
    """
    service = get_sa_drive_service()
    changes: list[dict] = []
    page_token = start_token

    while True:
        response = service.changes().list(
            pageToken=page_token,
            driveId=settings.DRIVE_ROOT_FOLDER_ID,
            spaces="drive",
            fields="nextPageToken,newStartPageToken,changes(fileId,file(id,name,mimeType,parents,trashed),removed,time)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageSize=100,
        ).execute()

        changes.extend(response.get("changes", []))

        if "newStartPageToken" in response:
            return changes, response["newStartPageToken"]

        next_token = response.get("nextPageToken")
        if not next_token:
            # Shouldn't happen, but avoid infinite loop
            log.warning("Drive Changes API returned neither newStartPageToken nor nextPageToken")
            return changes, start_token
        page_token = next_token


def _get_sa_docs_service():
    """Build a Docs v1 service using the service account.

    Returns a thread-local instance to avoid httplib2 concurrency crashes.
    """
    svc = getattr(_thread_local, "sa_docs_service", None)
    if svc is not None:
        return svc
    svc = build("docs", "v1", credentials=_get_sa_credentials(), static_discovery=True)
    _thread_local.sa_docs_service = svc
    return svc


def get_doc_content(user: User, google_drive_id: str) -> dict:
    """Fetch a Google Doc's full content via the Docs API (service account).

    Returns the raw document JSON from documents.get().
    """
    docs_service = _get_sa_docs_service()
    try:
        return docs_service.documents().get(documentId=google_drive_id).execute()
    except HttpError as exc:
        if exc.resp.status == 404:
            raise HTTPException(404, "Document not found or deleted") from exc
        if exc.resp.status == 403:
            raise HTTPException(403, "Permission denied") from exc
        log.error("Failed to fetch document %s: %s", google_drive_id, exc)
        raise HTTPException(502, "Failed to fetch document content") from exc


def export_doc_html(user: User, google_drive_id: str) -> str:
    """Export a Google Doc as HTML via the Drive API (service account).

    The HTML export renders smart chips (Person, Status, File, Date, etc.)
    as actual text — unlike the Docs API which returns opaque placeholders.
    """
    service = get_sa_drive_service()
    response = service.files().export(
        fileId=google_drive_id,
        mimeType="text/html",
    ).execute()
    if isinstance(response, bytes):
        return response.decode("utf-8")
    return response
