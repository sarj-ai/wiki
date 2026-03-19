"""Folder operations: ensure, create, check empty."""

from __future__ import annotations

import threading
import time

from app.drive._helpers import _escape_drive_query
from app.drive.credentials import get_sa_drive_service
from app.models.user import User

# In-memory cache for folder IDs with TTL to avoid stale entries.
# Entries expire after _CACHE_TTL seconds so trashed/recreated folders
# are eventually picked up without a restart.
_CACHE_TTL = 600  # 10 minutes
_CACHE_MAX_SIZE = 500  # evict oldest entries when exceeded
_folder_cache: dict[tuple[str, str], tuple[str, float]] = {}  # key -> (folder_id, timestamp)
_cache_lock = threading.Lock()


def _evict_oldest() -> None:
    """Remove oldest entries if cache exceeds max size (thread-safe)."""
    with _cache_lock:
        if len(_folder_cache) > _CACHE_MAX_SIZE:
            sorted_keys = sorted(_folder_cache, key=lambda k: _folder_cache[k][1])
            for key in sorted_keys[: len(_folder_cache) - _CACHE_MAX_SIZE]:
                del _folder_cache[key]


def ensure_folder(service, name: str, parent_id: str) -> str:
    """Find or create a folder with the given name under parent_id.

    Results are cached in memory to avoid repeated API calls.
    Returns the folder's Drive ID.
    """
    cache_key = (name, parent_id)
    cached = _folder_cache.get(cache_key)
    if cached and (time.monotonic() - cached[1]) < _CACHE_TTL:
        return cached[0]

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
        _folder_cache[cache_key] = (files[0]["id"], time.monotonic())
        _evict_oldest()
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
    _folder_cache[cache_key] = (folder["id"], time.monotonic())
    _evict_oldest()
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
