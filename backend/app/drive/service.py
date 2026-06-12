"""User-scoped Drive operations as a single service class.

Covers drive listing, folder-tree walking, and search. Every operation
runs against the user's OAuth token — ``drives.list()`` is also our
access-control boundary because Drive only returns drives the caller
is a member of.

``DriveService`` is the ABC the rest of the app codes against.
``GoogleDriveService`` is the production implementation that calls the
real Google Drive v3 API; swap it for a fake in tests by subclassing
``DriveService`` directly, or by patching
``GoogleDriveService.build_drive_client``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Callable
from functools import cache
from itertools import batched
import json
from typing import TYPE_CHECKING, Any, cast, override

from google.oauth2.credentials import Credentials
import google_auth_httplib2
from googleapiclient import discovery
from googleapiclient.discovery_cache import get_static_doc
from googleapiclient.errors import HttpError
import httplib2
from loguru import logger

from app.auth.encryption import decrypt_text
from app.config import settings
from app.drive import drives_cache, probe_cache
from app.drive.retry import drive_retry, execute_with_retry
from app.drive.schemas import (
    MIME_FOLDER,
    MIME_SHORTCUT,
    NAVIGATOR_SUPPORTED_MIME_TYPES,
    DriveNode,
    DriveSummary,
    DriveTree,
    SearchResult,
    ShortcutProbe,
    SnippetResult,
)
from app.drive.snippets import escape_query_value, extract_snippet
from app.models.user import User


if TYPE_CHECKING:
    from googleapiclient._apis.drive.v3.resources import DriveResource
    from googleapiclient.http import BatchHttpRequest

_DRIVE_HTTP_TIMEOUT_SECONDS = 30

_PROBE_BATCH_SIZE = 100

_SEARCH_PAGE_SIZE = 50

# Caps the drive-wide file listing baked into the tree cache. Drives
# past the cap fall back to lazy per-folder listings client-side
# (files_complete=False) instead of hauling an unbounded payload.
_MAX_DRIVE_FILE_PAGES = 3

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
    async def search(self, query: str, *, limit_per_drive: int = 20) -> list[SearchResult]:
        """Search every drive the user is a member of.

        Folds ``list_drives`` + per-drive fan-out so callers don't have to
        compose them. Empty/whitespace queries and zero-drive accounts both
        short-circuit to ``[]``.
        """

    @abstractmethod
    async def fetch_snippet(
        self, file_id: str, query: str, *, mime_type: str | None = None
    ) -> SnippetResult:
        """Extract a match snippet from a file's text export.

        A caller-supplied ``mime_type`` skips the metadata lookup; it's an
        optimization, not an authority — the export still runs under the
        user's own token, so a wrong value just yields no snippet.
        """


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
        tree, (files, files_complete) = await asyncio.gather(
            asyncio.to_thread(self._walk_folders_sync, drive_id),
            asyncio.to_thread(self._list_drive_files_sync, drive_id),
        )
        tree.files_complete = files_complete
        tree.folders, tree.files = await asyncio.gather(
            self._resolve_shortcut_nodes(tree.folders),
            self._resolve_shortcut_nodes(files if files_complete else []),
        )
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
        stripped = query.strip()
        if not stripped:
            return []
        drives, hits = await asyncio.gather(
            self.list_drives(),
            asyncio.to_thread(
                self._search_all_drives_sync, stripped, max(limit_per_drive, _SEARCH_PAGE_SIZE)
            ),
        )
        if not drives:
            return []
        if hits is None:
            merged = await self._search_per_drive(drives, stripped, limit_per_drive)
        else:
            names = {d.id: d.name for d in drives}
            merged = [
                hit.model_copy(update={"drive_name": names[hit.drive_id]})
                for hit in hits
                if hit.drive_id in names
            ]
        return await self._resolve_shortcut_search_results(merged)

    async def _search_per_drive(
        self, drives: list[DriveSummary], query: str, limit_per_drive: int
    ) -> list[SearchResult]:
        tasks = [self.search_drive(d.id, query, limit=limit_per_drive) for d in drives]
        results = await asyncio.gather(*tasks)
        merged: list[SearchResult] = []
        for drive, per_drive in zip(drives, results, strict=True):
            for hit in per_drive:
                merged.append(hit.model_copy(update={"drive_name": drive.name}))
        return merged

    @override
    async def fetch_snippet(
        self, file_id: str, query: str, *, mime_type: str | None = None
    ) -> SnippetResult:
        return await asyncio.to_thread(self._fetch_snippet_sync, file_id, query, mime_type)

    # ------------------------------------------------------------------
    # Shortcut resolver. Runs after walk/list to enrich shortcut nodes
    # with target accessibility + the target's home-drive name, and to
    # auto-prune shortcuts whose target was deleted or trashed. Probes
    # are deduplicated and sent as Drive batch HTTP requests.
    # ------------------------------------------------------------------

    async def _resolve_shortcut_nodes(self, nodes: list[DriveNode]) -> list[DriveNode]:
        return await self._resolve_shortcuts(
            nodes,
            lambda target_drive_id, drive_names: {
                "shortcut_target_drive_id": target_drive_id,
                "shortcut_target_drive_name": (
                    drive_names.get(target_drive_id) if target_drive_id else None
                ),
            },
        )

    async def _resolve_shortcut_search_results(
        self, results: list[SearchResult]
    ) -> list[SearchResult]:
        # Same probe + grey-out logic as the sidebar nodes so the palette
        # renders inaccessible shortcut hits with the same UX. SearchResult
        # has no shortcut_target_drive_id field, hence the smaller update.
        return await self._resolve_shortcuts(
            results,
            lambda target_drive_id, drive_names: {
                "shortcut_target_drive_name": (
                    drive_names.get(target_drive_id) if target_drive_id else None
                ),
            },
        )

    async def _resolve_shortcuts[T: (DriveNode, SearchResult)](
        self,
        items: list[T],
        accessible_update: Callable[[str | None, dict[str, str]], dict[str, str | None]],
    ) -> list[T]:
        shortcuts = [i for i in items if i.mime_type == MIME_SHORTCUT]
        if not shortcuts:
            return items
        drive_names, probes = await asyncio.gather(
            self._drive_names(),
            self._probe_targets([i.shortcut_target_id for i in shortcuts]),
        )
        resolved = dict(zip((i.id for i in shortcuts), probes, strict=True))
        out: list[T] = []
        for item in items:
            if item.mime_type != MIME_SHORTCUT:
                out.append(item)
                continue
            probe = resolved[item.id]
            if probe is None:
                # Target deleted or trashed → auto-prune.
                continue
            if not probe.accessible:
                out.append(item.model_copy(update={"shortcut_inaccessible": True}))
                continue
            out.append(
                item.model_copy(update=accessible_update(probe.target_drive_id, drive_names))
            )
        return out

    async def _drive_names(self) -> dict[str, str]:
        return {d.id: d.name for d in await self.list_drives()}

    async def _probe_targets(self, target_ids: list[str | None]) -> list[ShortcutProbe | None]:
        """Probe targets once each (deduplicated), preserving input order."""
        unique_ids = sorted({tid for tid in target_ids if tid})
        if not unique_ids:
            return [None] * len(target_ids)
        user_id = str(self._user.id)
        probes = probe_cache.get_fresh(user_id, unique_ids)
        missing = [tid for tid in unique_ids if tid not in probes]
        if missing:
            fetched = await asyncio.to_thread(self._probe_targets_sync, missing)
            probe_cache.store(user_id, fetched)
            probes.update(fetched)
        return [probes.get(tid) if tid else None for tid in target_ids]

    def _probe_targets_sync(self, target_ids: list[str]) -> dict[str, ShortcutProbe | None]:
        client = self._client()
        probes: dict[str, ShortcutProbe | None] = {}

        def _collect(request_id: str, response: Any, exception: BaseException | None) -> None:
            probes[request_id] = self._classify_probe(request_id, response, exception)

        for chunk in batched(target_ids, _PROBE_BATCH_SIZE, strict=False):
            batch = client.new_batch_http_request(callback=_collect)
            for tid in chunk:
                batch.add(
                    client.files().get(
                        fileId=tid,
                        fields="id,driveId,trashed",
                        supportsAllDrives=True,
                    ),
                    request_id=tid,
                )
            try:
                _execute_batch_with_retry(batch)
            except HttpError:
                logger.exception("shortcut probe batch failed user={}", self._user.id)
                for tid in chunk:
                    probes.setdefault(tid, None)
        return probes

    def _classify_probe(
        self, target_id: str, response: Any, exception: BaseException | None
    ) -> ShortcutProbe | None:
        if exception is not None:
            status = getattr(getattr(exception, "resp", None), "status", None)
            if status == 404:
                return None
            if status == 403:
                return ShortcutProbe(accessible=False)
            logger.opt(exception=exception).warning(
                "shortcut target probe failed user={} target={}",
                self._user.id,
                target_id,
            )
            return None
        if response.get("trashed"):
            return None
        return ShortcutProbe(accessible=True, target_drive_id=response.get("driveId"))

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
        out = DriveTree(drive_id=drive_id, folders=[], files=[])
        page_token: str | None = None
        # Folder walk also picks up shortcuts whose target is a folder
        # so cross-drive folder pointers render natively in the tree.
        # The mime filter on ``targetMimeType`` happens client-side
        # because Drive's q grammar doesn't support shortcutDetails fields.
        query = f"(mimeType='{MIME_FOLDER}' or mimeType='{MIME_SHORTCUT}') and trashed=false"
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

    def _list_drive_files_sync(self, drive_id: str) -> tuple[list[DriveNode], bool]:
        client = self._client()
        out: list[DriveNode] = []
        page_token: str | None = None
        pages = 0
        query = f"trashed=false and {_build_mime_clause()}"
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
                    "list_drive_files failed user={} drive={}",
                    self._user.id,
                    drive_id,
                )
                return out, False
            for raw in resp.get("files", []):
                if raw["mimeType"] == MIME_FOLDER:
                    continue
                details = raw.get("shortcutDetails") or {}
                target_mime = details.get("targetMimeType")
                if raw["mimeType"] == MIME_SHORTCUT and (
                    target_mime == MIME_FOLDER or target_mime not in NAVIGATOR_SUPPORTED_MIME_TYPES
                ):
                    continue
                out.append(_node_from_api(raw, drive_id))
            page_token = resp.get("nextPageToken")
            if not page_token:
                return out, True
            pages += 1
            if pages >= _MAX_DRIVE_FILE_PAGES:
                logger.warning(
                    "drive file listing truncated at {} pages drive={}",
                    pages,
                    drive_id,
                )
                return out, False

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
        return [_search_result_from_api(raw, drive_id) for raw in resp.get("files", [])]

    def _search_all_drives_sync(self, query: str, page_size: int) -> list[SearchResult] | None:
        """Single cross-drive search; ``None`` means fall back to the
        per-drive fan-out (incomplete results or the call failed).
        """
        escaped = escape_query_value(query)
        q = f"(name contains '{escaped}' or fullText contains '{escaped}') and trashed=false"
        client = self._client()
        params: dict[str, Any] = {
            "q": q,
            "corpora": "allDrives",
            "includeItemsFromAllDrives": True,
            "supportsAllDrives": True,
            "pageSize": page_size,
            "fields": (
                "incompleteResults,files(id,name,mimeType,parents,modifiedTime,driveId,"
                "shortcutDetails(targetId,targetMimeType))"
            ),
        }
        try:
            resp = execute_with_retry(client.files().list(**params))
        except HttpError:
            logger.exception("all-drives search failed user={} q={!r}", self._user.id, query)
            return None
        if resp.get("incompleteResults"):
            return None
        return [_search_result_from_api(raw) for raw in resp.get("files", [])]

    def _fetch_snippet_sync(self, file_id: str, query: str, mime_type: str | None) -> SnippetResult:
        client = self._client()
        if mime_type is None:
            try:
                meta = execute_with_retry(
                    client.files().get(
                        fileId=file_id, fields="id,mimeType,size", supportsAllDrives=True
                    )
                )
            except HttpError:
                logger.exception("snippet metadata fetch failed file={}", file_id)
                return SnippetResult(file_id=file_id, mime_type="", snippet=None, match_term=None)
            fetched_mime: str = meta.get("mimeType", "")
            mime_type = fetched_mime

        if mime_type not in _TEXT_EXPORTABLE_MIME_TYPES:
            return SnippetResult(
                file_id=file_id, mime_type=mime_type, snippet=None, match_term=None
            )

        # ``size`` is only set on binary files; Workspace docs don't report
        # a byte size, so we can't pre-filter. The export itself is the
        # backstop — we truncate before decoding if the response is huge.
        try:
            raw = execute_with_retry(client.files().export(fileId=file_id, mimeType="text/plain"))
        except HttpError:
            logger.exception("snippet export failed file={}", file_id)
            return SnippetResult(
                file_id=file_id, mime_type=mime_type, snippet=None, match_term=None
            )

        if isinstance(raw, bytes):
            raw = raw[:_MAX_EXPORT_BYTES]
            text = raw.decode("utf-8", errors="replace")
        else:
            text = str(raw)[: _MAX_EXPORT_BYTES // 2]

        match = extract_snippet(text, query)
        if match is None:
            return SnippetResult(
                file_id=file_id, mime_type=mime_type, snippet=None, match_term=None
            )
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

        Clients are deliberately NOT shared across operations: httplib2
        connections aren't thread-safe, and operations run on
        ``asyncio.to_thread`` workers.
        """
        authed_http = google_auth_httplib2.AuthorizedHttp(
            GoogleDriveService._build_credentials(user),
            http=httplib2.Http(timeout=_DRIVE_HTTP_TIMEOUT_SECONDS),
        )
        # build_from_document is public API the stubs package omits.
        built = discovery.build_from_document(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
            _drive_discovery_document(), http=authed_http
        )
        return cast("DriveResource", built)

    @staticmethod
    def _build_credentials(user: User) -> Credentials:
        if user.encrypted_access_token is None:
            raise ValueError(f"User {user.id} has no encrypted_access_token")
        access_token = decrypt_text(user.encrypted_access_token)
        refresh_token = (
            decrypt_text(user.encrypted_refresh_token) if user.encrypted_refresh_token else None
        )
        # google.oauth2 ships partial typing — mypy strict flags
        # ``Credentials(...)`` as an untyped call; pyright sees full
        # types. Narrow ignore for mypy only.
        return Credentials(  # type: ignore[no-untyped-call]
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",  # noqa: S106 — public OAuth endpoint
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET.get_secret_value(),
        )


@cache
def _drive_discovery_document() -> dict[str, Any]:
    """Parse the bundled Drive v3 discovery document once per process."""
    content = get_static_doc("drive", "v3")  # type: ignore[no-untyped-call]
    if content is None:
        raise RuntimeError("Bundled Drive v3 discovery document not found")
    return cast("dict[str, Any]", json.loads(content))


@drive_retry
def _execute_batch_with_retry(batch: BatchHttpRequest) -> None:
    """Run a Drive batch request; per-item outcomes land in the callback,
    only whole-batch transport failures raise (and retry).
    """
    batch.execute()


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


def _search_result_from_api(raw: Any, drive_id: str | None = None) -> SearchResult:
    home_drive_id = raw.get("driveId") or drive_id
    parents = raw.get("parents") or []
    parent_id: str | None = parents[0] if parents else None
    if parent_id == home_drive_id:
        parent_id = None
    details = raw.get("shortcutDetails") or {}
    return SearchResult(
        id=raw["id"],
        name=raw["name"],
        mime_type=raw["mimeType"],
        drive_id=raw.get("driveId"),
        drive_name=None,
        parent_id=parent_id,
        modified_time=raw.get("modifiedTime"),
        shortcut_target_id=details.get("targetId"),
        shortcut_target_mime_type=details.get("targetMimeType"),
    )


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
