"""User-scoped Drive operations as a single service class.

Consolidates the thin wrappers that used to live in ``drives.py``,
``tree_walker.py``, and ``search.py``. Every operation runs against the
user's OAuth token — ``drives.list()`` is also our access-control
boundary because Drive only returns drives the caller is a member of.

``DriveService`` is the ABC the rest of the app codes against.
``GoogleDriveService`` is the production implementation that calls the
real Google Drive v3 API; swap it for a fake in tests by subclassing
``DriveService`` directly, or by patching
``GoogleDriveService.build_drive_client``.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, override

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from loguru import logger

from app.auth.encryption import decrypt_token
from app.config import settings
from app.drive import drives_cache
from app.drive.retry import execute_with_retry
from app.drive.schemas import (
    MIME_FOLDER,
    MIME_SHORTCUT,
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


@dataclass(frozen=True)
class _ShortcutProbe:
    """Outcome of a single ``files.get`` against a shortcut's target.

    ``None`` from the probe path means "drop the shortcut" (target deleted
    or trashed). A returned probe is either ``ok`` (with the target's home
    drive id, possibly ``None`` for My Drive items we don't surface) or
    ``forbidden`` (user lacks permission — render greyed-out).
    """

    accessible: bool
    target_drive_id: str | None = None


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
    async def search(self, query: str, *, limit_per_drive: int = 20) -> list[SearchResult]:
        """Search every drive the user is a member of.

        Folds ``list_drives`` + per-drive fan-out so callers don't have to
        compose them. Empty/whitespace queries and zero-drive accounts both
        short-circuit to ``[]``.
        """

    @abstractmethod
    async def fetch_snippet(self, file_id: str, query: str) -> SnippetResult: ...


class GoogleDriveService(DriveService):
    """Production ``DriveService`` backed by the Google Drive v3 API."""

    def __init__(self, user: User) -> None:
        self._user = user

    @override
    async def list_drives(self) -> list[DriveSummary]:
        # Stale-while-revalidate: fresh entries serve instantly, stale
        # entries also serve instantly while a background refetch
        # repopulates the cache for the next request. Cold path is the
        # only synchronous fetch — typically once per session.
        # Invalidated on logout (see ``AuthService.revoke_session``).
        return await drives_cache.get_drives(
            str(self._user.id),
            lambda: asyncio.to_thread(self._list_drives_sync),
        )

    @override
    async def walk_drive_folders(self, drive_id: str) -> DriveTree:
        tree = await asyncio.to_thread(self._walk_folders_sync, drive_id)
        tree.folders = await self._resolve_shortcut_nodes(tree.folders)
        return tree

    @override
    async def list_folder_children(self, folder_id: str) -> list[DriveNode]:
        nodes = await asyncio.to_thread(self._list_children_sync, folder_id)
        return await self._resolve_shortcut_nodes(nodes)

    @override
    async def search_drive(
        self, drive_id: str, query: str, *, limit: int = 50
    ) -> list[SearchResult]:
        if not query.strip():
            return []
        return await asyncio.to_thread(self._search_drive_sync, drive_id, query.strip(), limit)

    @override
    async def search(self, query: str, *, limit_per_drive: int = 20) -> list[SearchResult]:
        if not query.strip():
            return []
        drives = await self.list_drives()
        if not drives:
            return []
        tasks = [self.search_drive(d.id, query, limit=limit_per_drive) for d in drives]
        results = await asyncio.gather(*tasks)
        # Stamp each hit with its parent drive's name as we merge — saves
        # the client a separate ``drives.find()`` lookup per row.
        merged: list[SearchResult] = []
        for drive, per_drive in zip(drives, results, strict=True):
            for hit in per_drive:
                merged.append(hit.model_copy(update={"drive_name": drive.name}))
        # Resolve shortcut hits the same way the sidebar walk does —
        # grey out the rare case where a shortcut name matches but the
        # target's ACL blocks the user, drop hits whose target was
        # trashed.
        return await self._resolve_shortcut_search_results(merged)

    @override
    async def fetch_snippet(self, file_id: str, query: str) -> SnippetResult:
        return await asyncio.to_thread(self._fetch_snippet_sync, file_id, query)

    # ------------------------------------------------------------------
    # Shortcut resolver. Runs after walk/list to enrich shortcut nodes
    # with target accessibility + the target's home-drive name, and to
    # auto-prune shortcuts whose target was deleted or trashed. Probes
    # run in parallel via ``asyncio.gather`` so resolving N shortcuts
    # costs ~one round-trip latency, not N.
    # ------------------------------------------------------------------

    async def _resolve_shortcut_nodes(self, nodes: list[DriveNode]) -> list[DriveNode]:
        shortcuts = [n for n in nodes if n.mime_type == MIME_SHORTCUT]
        if not shortcuts:
            return nodes
        drive_names = await self._drive_names()
        probes = await self._probe_targets_parallel(
            [n.shortcut_target_id for n in shortcuts]
        )
        resolved = dict(zip((n.id for n in shortcuts), probes, strict=True))
        out: list[DriveNode] = []
        for node in nodes:
            if node.mime_type != MIME_SHORTCUT:
                out.append(node)
                continue
            probe = resolved[node.id]
            if probe is None:
                # Target deleted or trashed → auto-prune.
                continue
            if not probe.accessible:
                out.append(node.model_copy(update={"shortcut_inaccessible": True}))
                continue
            target_drive_id = probe.target_drive_id
            out.append(
                node.model_copy(
                    update={
                        "shortcut_target_drive_id": target_drive_id,
                        "shortcut_target_drive_name": (
                            drive_names.get(target_drive_id) if target_drive_id else None
                        ),
                    }
                )
            )
        return out

    async def _resolve_shortcut_search_results(
        self, results: list[SearchResult]
    ) -> list[SearchResult]:
        """Same probe + grey-out logic as :meth:`_resolve_shortcut_nodes`,
        applied to search hits so the palette can render inaccessible
        shortcut hits with the same UX as the sidebar (greyed-out tooltip,
        click suppressed). Auto-prune behaves identically — shortcut hits
        whose target is deleted/trashed are dropped from the result list.
        """
        shortcuts = [r for r in results if r.mime_type == MIME_SHORTCUT]
        if not shortcuts:
            return results
        drive_names = await self._drive_names()
        probes = await self._probe_targets_parallel(
            [r.shortcut_target_id for r in shortcuts]
        )
        resolved = dict(zip((r.id for r in shortcuts), probes, strict=True))
        out: list[SearchResult] = []
        for result in results:
            if result.mime_type != MIME_SHORTCUT:
                out.append(result)
                continue
            probe = resolved[result.id]
            if probe is None:
                continue
            if not probe.accessible:
                out.append(result.model_copy(update={"shortcut_inaccessible": True}))
                continue
            target_drive_id = probe.target_drive_id
            out.append(
                result.model_copy(
                    update={
                        "shortcut_target_drive_name": (
                            drive_names.get(target_drive_id) if target_drive_id else None
                        ),
                    }
                )
            )
        return out

    async def _drive_names(self) -> dict[str, str]:
        return {d.id: d.name for d in await self.list_drives()}

    async def _probe_targets_parallel(
        self, target_ids: list[str | None]
    ) -> list[_ShortcutProbe | None]:
        return list(
            await asyncio.gather(
                *(asyncio.to_thread(self._probe_target_sync, tid) for tid in target_ids)
            )
        )

    def _probe_target_sync(self, target_id: str | None) -> _ShortcutProbe | None:
        if not target_id:
            return None
        client = self._client()
        try:
            meta = execute_with_retry(
                client.files().get(
                    fileId=target_id,
                    fields="id,driveId,trashed",
                    supportsAllDrives=True,
                )
            )
        except HttpError as exc:
            status = getattr(exc.resp, "status", None)
            if status == 404:
                return None
            if status == 403:
                return _ShortcutProbe(accessible=False)
            logger.exception(
                "shortcut target probe failed user={} target={}",
                self._user.id,
                target_id,
            )
            # Be conservative — if we can't classify the failure, drop
            # the shortcut rather than rendering a misleading row.
            return None
        if meta.get("trashed"):
            return None
        return _ShortcutProbe(accessible=True, target_drive_id=meta.get("driveId"))

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
                resp = execute_with_retry(client.drives().list(**params))
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
        # Folder walk also picks up shortcuts whose target is a folder
        # so cross-drive folder pointers render natively in the tree.
        # The mime filter on ``targetMimeType`` happens client-side
        # because Drive's q grammar doesn't support shortcutDetails fields.
        query = (
            f"(mimeType='{MIME_FOLDER}' or mimeType='{MIME_SHORTCUT}') and trashed=false"
        )
        while True:
            params: dict[str, Any] = {
                "q": query,
                "corpora": "drive",
                "driveId": drive_id,
                "includeItemsFromAllDrives": True,
                "supportsAllDrives": True,
                "pageSize": 1000,
                "fields": (
                    "nextPageToken,files(id,name,mimeType,parents,modifiedTime,"
                    "shortcutDetails(targetId,targetMimeType))"
                ),
            }
            if page_token:
                params["pageToken"] = page_token
            try:
                resp = execute_with_retry(client.files().list(**params))
            except HttpError:
                logger.exception(
                    "walk_drive_folders failed user={} drive={}",
                    self._user.id,
                    drive_id,
                )
                return out
            for raw in resp.get("files", []):
                node = _node_from_api(raw, drive_id)
                if node.mime_type == MIME_SHORTCUT and (
                    node.shortcut_target_mime_type != MIME_FOLDER
                ):
                    # File-target shortcuts surface only in the children
                    # listing — keep the folder walk strictly hierarchical.
                    continue
                out.folders.append(node)
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
                "fields": (
                    "nextPageToken,files(id,name,mimeType,parents,modifiedTime,"
                    "shortcutDetails(targetId,targetMimeType))"
                ),
            }
            if page_token:
                params["pageToken"] = page_token
            try:
                resp = execute_with_retry(client.files().list(**params))
            except HttpError:
                logger.exception(
                    "list_folder_children failed user={} folder={}",
                    self._user.id,
                    folder_id,
                )
                return out
            for raw in resp.get("files", []):
                details = raw.get("shortcutDetails") or {}
                target_mime = details.get("targetMimeType")
                if (
                    raw["mimeType"] == MIME_SHORTCUT
                    and target_mime not in NAVIGATOR_SUPPORTED_MIME_TYPES
                ):
                    # Drop shortcuts pointing at unsupported file types
                    # (e.g. third-party Drive apps) so we never render a
                    # row that has nothing to open.
                    continue
                out.append(
                    DriveNode(
                        id=raw["id"],
                        name=raw["name"],
                        mime_type=raw["mimeType"],
                        parent_id=folder_id,
                        modified_time=raw.get("modifiedTime"),
                        shortcut_target_id=details.get("targetId"),
                        shortcut_target_mime_type=target_mime,
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
            "fields": (
                "files(id,name,mimeType,parents,modifiedTime,driveId,"
                "shortcutDetails(targetId,targetMimeType))"
            ),
        }
        try:
            resp = execute_with_retry(client.files().list(**params))
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
            details = raw.get("shortcutDetails") or {}
            out.append(
                SearchResult(
                    id=raw["id"],
                    name=raw["name"],
                    mime_type=raw["mimeType"],
                    drive_id=raw.get("driveId"),
                    parent_id=parent_id,
                    modified_time=raw.get("modifiedTime"),
                    shortcut_target_id=details.get("targetId"),
                    shortcut_target_mime_type=details.get("targetMimeType"),
                )
            )
        return out

    def _fetch_snippet_sync(self, file_id: str, query: str) -> SnippetResult:
        client = self._client()
        try:
            meta = execute_with_retry(
                client.files().get(
                    fileId=file_id, fields="id,mimeType,size", supportsAllDrives=True
                )
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
            raw = execute_with_retry(client.files().export(fileId=file_id, mimeType="text/plain"))
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
        return self.build_drive_client(self._user)

    @staticmethod
    def build_drive_client(user: User) -> DriveResource:
        """Construct an authenticated Drive v3 client for ``user``.

        Public so :class:`~app.files.service.GoogleFilesService` can
        spin up its own client off the same per-user credentials. The
        actual credential decryption stays in ``_build_credentials``
        because no caller outside this class should touch the OAuth
        secret material directly.
        """
        return build(
            "drive",
            "v3",
            credentials=GoogleDriveService._build_credentials(user),
            static_discovery=True,
        )

    @staticmethod
    def _build_credentials(user: User) -> Credentials:
        if user.encrypted_access_token is None:
            raise ValueError(f"User {user.id} has no encrypted_access_token")
        access_token = decrypt_token(user.encrypted_access_token)
        refresh_token = (
            decrypt_token(user.encrypted_refresh_token) if user.encrypted_refresh_token else None
        )
        # google.oauth2 ships partial typing — mypy strict flags
        # ``Credentials(...)`` as an untyped call; pyright sees full
        # types. Narrow ignore for mypy only.
        return Credentials(  # type: ignore[no-untyped-call]
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",  # noqa: S106 — public OAuth endpoint
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
        )


def _build_mime_clause() -> str:
    # Folders always included so the tree is walkable; everything else
    # filtered to Workspace + PDFs + common images so we don't haul down
    # random binary blobs a user happens to have in a drive. Shortcuts
    # are included unconditionally — we filter them client-side based on
    # ``shortcutDetails.targetMimeType`` since Drive's q grammar can't
    # filter on shortcut details.
    quoted = " or ".join(
        f"mimeType='{m}'" for m in (*NAVIGATOR_SUPPORTED_MIME_TYPES, MIME_SHORTCUT)
    )
    return f"({quoted})"


def _node_from_api(raw: Any, drive_id: str) -> DriveNode:
    # Drive returns the drive id itself as the parent for root-level
    # files/folders; we normalize that to None so tree reconstruction
    # treats them as top-level.
    parents = raw.get("parents") or []
    parent_id: str | None = parents[0] if parents else None
    if parent_id == drive_id:
        parent_id = None
    details = raw.get("shortcutDetails") or {}
    return DriveNode(
        id=raw["id"],
        name=raw["name"],
        mime_type=raw["mimeType"],
        parent_id=parent_id,
        modified_time=raw.get("modifiedTime"),
        shortcut_target_id=details.get("targetId"),
        shortcut_target_mime_type=details.get("targetMimeType"),
    )
