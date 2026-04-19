"""Per-drive tree walker — the core of the Navigator's sidebar cache.

Strategy is **folders-only first, files lazy** (see PIVOT_PLAN.md §7.1):

* ``walk_drive_folders`` paginates every folder in a single shared drive
  using the user's OAuth token and ``corpora=drive`` scoping. Spike
  measured ~6s for the largest Sarj drive, ~1.4s for the small ones.
  Files inside each folder are fetched later, on demand.

* ``list_folder_children`` fetches direct children of one folder for a
  lazy expand. Returns both files and subfolders so the caller can merge
  new files into the cached tree while still using the folder metadata
  the tree walker already captured.

Both functions wrap the sync Google API client in ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from googleapiclient.errors import HttpError
from loguru import logger

from app.drive.credentials import get_drive_service
from app.drive.schemas import MIME_FOLDER, NAVIGATOR_SUPPORTED_MIME_TYPES, DriveNode, DriveTree
from app.models.user import User

if TYPE_CHECKING:
    from googleapiclient._apis.drive.v3.schemas import File


def _build_mime_clause() -> str:
    """Drive query clause restricting results to types the Navigator shows.

    Folders must always be included so the tree is walkable. Everything
    else is filtered down to Google Workspace files + PDFs + common
    images so we don't haul down random binary blobs users happen to
    have in a drive.
    """
    quoted = " or ".join(f"mimeType='{m}'" for m in NAVIGATOR_SUPPORTED_MIME_TYPES)
    return f"({quoted})"


def _node_from_api(raw: File, drive_id: str) -> DriveNode:
    """Convert a Drive API file dict into a ``DriveNode``.

    Drive returns ``parents`` as a list where files/folders inside the
    drive root have the drive id itself as the parent; we normalize that
    to ``None`` so tree reconstruction treats it as a top-level node.
    """
    parents = raw.get("parents") or []
    parent_id: str | None = parents[0] if parents else None
    if parent_id == drive_id:
        parent_id = None
    return DriveNode(
        id=raw["id"],
        name=raw["name"],
        mime_type=raw["mimeType"],
        parent_id=parent_id,
        modified_time=raw.get("modifiedTime"),
    )


async def walk_drive_folders(user: User, drive_id: str) -> DriveTree:
    """Walk every folder in ``drive_id`` using ``user``'s OAuth token.

    Files are NOT fetched here — call ``list_folder_children`` lazily
    when a user expands a folder. Spike-measured cold-walk latency:
    ~6s for Engineering (1624 folders, 4 pages), ~1.4s for small drives.
    """

    def _sync() -> DriveTree:
        service = get_drive_service(user)
        out = DriveTree(drive_id=drive_id)
        page_token: str | None = None
        query = f"mimeType='{MIME_FOLDER}' and trashed=false"
        while True:
            params: dict[str, Any] = {
                "q": query,
                "corpora": "drive",
                "driveId": drive_id,
                "includeItemsFromAllDrives": True,
                "supportsAllDrives": True,
                "pageSize": 1000,
                "fields": "nextPageToken,files(id,name,mimeType,parents,modifiedTime)",
            }
            if page_token:
                params["pageToken"] = page_token
            try:
                resp = service.files().list(**params).execute()
            except HttpError:
                logger.exception(
                    "walk_drive_folders failed user={} drive={}",
                    user.id,
                    drive_id,
                )
                return out
            for raw in resp.get("files", []):
                out.folders.append(_node_from_api(raw, drive_id))
            page_token = resp.get("nextPageToken")
            if not page_token:
                return out

    return await asyncio.to_thread(_sync)


async def list_folder_children(user: User, folder_id: str) -> list[DriveNode]:
    """Fetch direct children (files + subfolders) of a single folder.

    Used for lazy file loads when a user expands a folder in the sidebar.
    Returns an empty list on error so a broken folder doesn't break the
    sidebar.
    """

    def _sync() -> list[DriveNode]:
        service = get_drive_service(user)
        out: list[DriveNode] = []
        page_token: str | None = None
        query = f"'{folder_id}' in parents and trashed=false and {_build_mime_clause()}"
        while True:
            params: dict[str, Any] = {
                "q": query,
                "corpora": "allDrives",
                "includeItemsFromAllDrives": True,
                "supportsAllDrives": True,
                "pageSize": 1000,
                "fields": "nextPageToken,files(id,name,mimeType,parents,modifiedTime)",
            }
            if page_token:
                params["pageToken"] = page_token
            try:
                resp = service.files().list(**params).execute()
            except HttpError:
                logger.exception(
                    "list_folder_children failed user={} folder={}",
                    user.id,
                    folder_id,
                )
                return out
            for raw in resp.get("files", []):
                out.append(
                    DriveNode(
                        id=raw["id"],
                        name=raw["name"],
                        mime_type=raw["mimeType"],
                        parent_id=folder_id,
                        modified_time=raw.get("modifiedTime"),
                    )
                )
            page_token = resp.get("nextPageToken")
            if not page_token:
                return out

    return await asyncio.to_thread(_sync)
