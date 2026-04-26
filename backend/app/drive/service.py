"""User-scoped Drive operations as a single service class.

Consolidates the thin wrappers that used to live in ``drives.py``,
``tree_walker.py``, and ``search.py``. Every operation runs against the
user's OAuth token — ``drives.list()`` is also our access-control
boundary because Drive only returns drives the caller is a member of.

``DriveService`` is the ABC the rest of the app codes against.
``GoogleDriveService`` is the production implementation that calls the
real Google Drive v3 API; swap it for a fake in tests by subclassing
``DriveService`` directly, or by patching ``build_drive_client``.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, override

from googleapiclient.errors import HttpError
from loguru import logger

from app.drive.credentials import build_drive_client
from app.drive.schemas import (
    MIME_FOLDER,
    NAVIGATOR_SUPPORTED_MIME_TYPES,
    DriveNode,
    DriveSummary,
    DriveTree,
    SearchResult,
    SnippetResult,
)
from app.drive.snippets import escape_query_value, extract_snippet
from app.models.user import User

if TYPE_CHECKING:
    from googleapiclient._apis.drive.v3.resources import DriveResource

# Google Workspace types that expose a clean text/plain export. Other
# types (PDF, images, Forms) can't export useful text — we return a
# null snippet and let the UI show a type-specific placeholder.
_TEXT_EXPORTABLE_MIME_TYPES = frozenset(
    {
        "application/vnd.google-apps.document",
        "application/vnd.google-apps.spreadsheet",
        "application/vnd.google-apps.presentation",
    }
)

_MAX_EXPORT_BYTES = 2 * 1024 * 1024  # cap huge docs so search doesn't stall


class DriveService(ABC):
    """Contract for per-user Drive operations."""

    @abstractmethod
    async def list_drives(self) -> list[DriveSummary]: ...

    @abstractmethod
    async def walk_drive_folders(self, drive_id: str) -> DriveTree: ...

    @abstractmethod
    async def list_folder_children(self, folder_id: str) -> list[DriveNode]: ...

    @abstractmethod
    async def search_drive(
        self, drive_id: str, query: str, *, limit: int = 50
    ) -> list[SearchResult]: ...

    @abstractmethod
    async def search_across_drives(
        self, drive_ids: list[str], query: str, *, limit_per_drive: int = 20
    ) -> list[SearchResult]: ...

    @abstractmethod
    async def fetch_snippet(self, file_id: str, query: str) -> SnippetResult: ...


class GoogleDriveService(DriveService):
    """Production ``DriveService`` backed by the Google Drive v3 API."""

    def __init__(self, user: User) -> None:
        self._user = user

    @override
    async def list_drives(self) -> list[DriveSummary]:
        return await asyncio.to_thread(self._list_drives_sync)

    @override
    async def walk_drive_folders(self, drive_id: str) -> DriveTree:
        return await asyncio.to_thread(self._walk_folders_sync, drive_id)

    @override
    async def list_folder_children(self, folder_id: str) -> list[DriveNode]:
        return await asyncio.to_thread(self._list_children_sync, folder_id)

    @override
    async def search_drive(
        self, drive_id: str, query: str, *, limit: int = 50
    ) -> list[SearchResult]:
        if not query.strip():
            return []
        return await asyncio.to_thread(self._search_drive_sync, drive_id, query.strip(), limit)

    @override
    async def search_across_drives(
        self, drive_ids: list[str], query: str, *, limit_per_drive: int = 20
    ) -> list[SearchResult]:
        if not drive_ids or not query.strip():
            return []
        tasks = [
            self.search_drive(drive_id, query, limit=limit_per_drive) for drive_id in drive_ids
        ]
        results = await asyncio.gather(*tasks)
        merged: list[SearchResult] = []
        for per_drive in results:
            merged.extend(per_drive)
        return merged

    @override
    async def fetch_snippet(self, file_id: str, query: str) -> SnippetResult:
        return await asyncio.to_thread(self._fetch_snippet_sync, file_id, query)

    # ------------------------------------------------------------------
    # Sync implementations. Wrapped in asyncio.to_thread by the public
    # methods above so the event loop stays free during the blocking
    # Google API client calls.
    # ------------------------------------------------------------------

    def _list_drives_sync(self) -> list[DriveSummary]:
        client = self._client()
        out: list[DriveSummary] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {
                "pageSize": 100,
                "fields": "nextPageToken,drives(id,name)",
            }
            if page_token:
                params["pageToken"] = page_token
            try:
                resp = client.drives().list(**params).execute()
            except HttpError:
                logger.exception("drives.list failed for user={}", self._user.id)
                return out
            for d in resp.get("drives", []):
                drive_id = d.get("id")
                name = d.get("name")
                if drive_id and name:
                    out.append(DriveSummary(id=drive_id, name=name))
            page_token = resp.get("nextPageToken")
            if not page_token:
                return out

    def _walk_folders_sync(self, drive_id: str) -> DriveTree:
        client = self._client()
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
                resp = client.files().list(**params).execute()
            except HttpError:
                logger.exception(
                    "walk_drive_folders failed user={} drive={}",
                    self._user.id,
                    drive_id,
                )
                return out
            for raw in resp.get("files", []):
                out.folders.append(_node_from_api(raw, drive_id))
            page_token = resp.get("nextPageToken")
            if not page_token:
                return out

    def _list_children_sync(self, folder_id: str) -> list[DriveNode]:
        client = self._client()
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
                resp = client.files().list(**params).execute()
            except HttpError:
                logger.exception(
                    "list_folder_children failed user={} folder={}",
                    self._user.id,
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

    def _search_drive_sync(self, drive_id: str, query: str, limit: int) -> list[SearchResult]:
        escaped = escape_query_value(query)
        q = f"(name contains '{escaped}' or fullText contains '{escaped}') and trashed=false"
        client = self._client()
        params: dict[str, Any] = {
            "q": q,
            "corpora": "drive",
            "driveId": drive_id,
            "includeItemsFromAllDrives": True,
            "supportsAllDrives": True,
            "pageSize": limit,
            "fields": "files(id,name,mimeType,parents,modifiedTime,driveId)",
        }
        try:
            resp = client.files().list(**params).execute()
        except HttpError:
            logger.exception(
                "search_drive failed user={} drive={} q={!r}",
                self._user.id,
                drive_id,
                query,
            )
            return []
        out: list[SearchResult] = []
        for raw in resp.get("files", []):
            parents = raw.get("parents") or []
            parent_id = parents[0] if parents else None
            if parent_id == drive_id:
                parent_id = None
            out.append(
                SearchResult(
                    id=raw["id"],
                    name=raw["name"],
                    mime_type=raw["mimeType"],
                    drive_id=raw.get("driveId"),
                    parent_id=parent_id,
                    modified_time=raw.get("modifiedTime"),
                )
            )
        return out

    def _fetch_snippet_sync(self, file_id: str, query: str) -> SnippetResult:
        client = self._client()
        try:
            meta = (
                client.files()
                .get(fileId=file_id, fields="id,mimeType,size", supportsAllDrives=True)
                .execute()
            )
        except HttpError:
            logger.exception("snippet metadata fetch failed file={}", file_id)
            return SnippetResult(file_id=file_id, mime_type="", snippet=None)

        mime_type = meta.get("mimeType", "")
        if mime_type not in _TEXT_EXPORTABLE_MIME_TYPES:
            return SnippetResult(file_id=file_id, mime_type=mime_type, snippet=None)

        # ``size`` is only set on binary files; Workspace docs don't report
        # a byte size, so we can't pre-filter. The export itself is the
        # backstop — we truncate before decoding if the response is huge.
        try:
            raw = client.files().export(fileId=file_id, mimeType="text/plain").execute()
        except HttpError:
            logger.exception("snippet export failed file={}", file_id)
            return SnippetResult(file_id=file_id, mime_type=mime_type, snippet=None)

        if isinstance(raw, bytes):
            raw = raw[:_MAX_EXPORT_BYTES]
            text = raw.decode("utf-8", errors="replace")
        else:
            text = str(raw)[: _MAX_EXPORT_BYTES // 2]

        match = extract_snippet(text, query)
        if match is None:
            return SnippetResult(file_id=file_id, mime_type=mime_type, snippet=None)
        snippet, word = match
        return SnippetResult(
            file_id=file_id,
            mime_type=mime_type,
            snippet=snippet,
            match_term=word,
        )

    def _client(self) -> DriveResource:
        return build_drive_client(self._user)


def _build_mime_clause() -> str:
    # Folders always included so the tree is walkable; everything else
    # filtered to Workspace + PDFs + common images so we don't haul down
    # random binary blobs a user happens to have in a drive.
    quoted = " or ".join(f"mimeType='{m}'" for m in NAVIGATOR_SUPPORTED_MIME_TYPES)
    return f"({quoted})"


def _node_from_api(raw: Any, drive_id: str) -> DriveNode:
    # Drive returns the drive id itself as the parent for root-level
    # files/folders; we normalize that to None so tree reconstruction
    # treats them as top-level.
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
