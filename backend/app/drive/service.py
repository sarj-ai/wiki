from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Callable
from functools import cache
from itertools import batched
import json
import threading
from typing import TYPE_CHECKING, Any, cast, override

from google.oauth2.credentials import Credentials
import google_auth_httplib2
from googleapiclient import discovery
from googleapiclient.discovery_cache import get_static_doc
from googleapiclient.errors import HttpError
import httplib2
from loguru import logger
from pydantic import ValidationError

from app.auth.encryption import decrypt_text
from app.config import settings
from app.drive import drives_cache, probe_cache
from app.drive.google_schemas import (
    GoogleDrive,
    GoogleFile,
    GoogleFileMetadata,
    GoogleFileProbe,
)
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
from app.identifiers import DriveId, FileId
from app.models.user import User


if TYPE_CHECKING:
    from googleapiclient._apis.drive.v3.resources import DriveResource
    from googleapiclient.http import BatchHttpRequest

_DRIVE_HTTP_TIMEOUT_SECONDS = 30

_PROBE_BATCH_SIZE = 100

_SEARCH_PAGE_SIZE = 50

_MAX_DRIVE_FILE_PAGES = 3

_TEXT_EXPORTABLE_MIME_TYPES = frozenset(
    {
        "application/vnd.google-apps.document",
        "application/vnd.google-apps.spreadsheet",
        "application/vnd.google-apps.presentation",
    }
)

_MAX_EXPORT_BYTES = 2 * 1024 * 1024


class DriveService(ABC):
    @abstractmethod
    async def list_drives(self) -> list[DriveSummary]: ...

    @abstractmethod
    async def walk_drive_folders(self, drive_id: DriveId) -> DriveTree: ...

    @abstractmethod
    async def list_folder_children(self, folder_id: FileId) -> list[DriveNode]: ...

    @abstractmethod
    async def search_drive(
        self, drive_id: DriveId, query: str, *, limit: int = 50
    ) -> list[SearchResult]: ...

    @abstractmethod
    async def search(self, query: str, *, limit_per_drive: int = 20) -> list[SearchResult]: ...

    @abstractmethod
    async def fetch_snippet(
        self, file_id: FileId, query: str, *, mime_type: str | None = None
    ) -> SnippetResult: ...


class GoogleDriveService(DriveService):
    def __init__(self, user: User) -> None:
        self._user = user
        self._clients: dict[int, DriveResource] = {}

    @override
    async def list_drives(self) -> list[DriveSummary]:
        return await drives_cache.get_drives(
            str(self._user.id),
            lambda: asyncio.to_thread(self._list_drives_sync),
        )

    @override
    async def walk_drive_folders(self, drive_id: DriveId) -> DriveTree:
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
    async def list_folder_children(self, folder_id: FileId) -> list[DriveNode]:
        nodes = await asyncio.to_thread(self._list_children_sync, folder_id)
        return await self._resolve_shortcut_nodes(nodes)

    @override
    async def search_drive(
        self, drive_id: DriveId, query: str, *, limit: int = 50
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
                hit.model_copy(update={"drive_name": names[drive_id]})
                for hit in hits
                if (drive_id := hit.drive_id) is not None and drive_id in names
            ]
        return await self._resolve_shortcut_search_results(merged)

    async def _search_per_drive(
        self, drives: list[DriveSummary], query: str, limit_per_drive: int
    ) -> list[SearchResult]:
        tasks = [self.search_drive(d.id, query, limit=limit_per_drive) for d in drives]
        results = await asyncio.gather(*tasks)
        merged: list[SearchResult] = []
        for drive, per_drive in zip(drives, results, strict=True):
            merged.extend(hit.model_copy(update={"drive_name": drive.name}) for hit in per_drive)
        return merged

    @override
    async def fetch_snippet(
        self, file_id: FileId, query: str, *, mime_type: str | None = None
    ) -> SnippetResult:
        return await asyncio.to_thread(self._fetch_snippet_sync, file_id, query, mime_type)

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
        accessible_update: Callable[
            [DriveId | None, dict[DriveId, str]], dict[str, DriveId | str | None]
        ],
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
                continue
            if not probe.accessible:
                out.append(item.model_copy(update={"shortcut_inaccessible": True}))
                continue
            out.append(
                item.model_copy(update=accessible_update(probe.target_drive_id, drive_names))
            )
        return out

    async def _drive_names(self) -> dict[DriveId, str]:
        return {d.id: d.name for d in await self.list_drives()}

    async def _probe_targets(self, target_ids: list[FileId | None]) -> list[ShortcutProbe | None]:
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

    def _probe_targets_sync(self, target_ids: list[FileId]) -> dict[FileId, ShortcutProbe | None]:
        client = self._client()
        probes: dict[FileId, ShortcutProbe | None] = {}

        def _collect(request_id: str, response: Any, exception: BaseException | None) -> None:
            target_id = FileId(request_id)
            probes[target_id] = self._classify_probe(target_id, response, exception)

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
                logger.exception(
                    "shortcut probe batch failed user={user_id}", user_id=self._user.id
                )
                for tid in chunk:
                    probes.setdefault(tid, None)
        return probes

    def _classify_probe(
        self, target_id: FileId, response: Any, exception: BaseException | None
    ) -> ShortcutProbe | None:
        if exception is not None:
            status = getattr(getattr(exception, "resp", None), "status", None)
            if status == 404:
                return None
            if status == 403:
                return ShortcutProbe(accessible=False)
            logger.opt(exception=exception).warning(
                "shortcut target probe failed user={user_id} target={target_id}",
                user_id=self._user.id,
                target_id=target_id,
            )
            return None
        probe = GoogleFileProbe.model_validate(response)
        if probe.trashed:
            return None
        return ShortcutProbe(accessible=True, target_drive_id=probe.drive_id)

    def _list_drives_sync(self) -> list[DriveSummary]:
        client = self._client()
        out: list[DriveSummary] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {
                "pageSize": 100,
                "q": "hidden = false",
                "useDomainAdminAccess": False,
                "fields": "nextPageToken,drives(id,name,hidden)",
            }
            if page_token:
                params["pageToken"] = page_token
            try:
                resp = execute_with_retry(client.drives().list(**params))
            except HttpError:
                logger.exception("drives.list failed for user={user_id}", user_id=self._user.id)
                return out
            for d in resp.get("drives", []):
                try:
                    drive = GoogleDrive.model_validate(d)
                except ValidationError:
                    continue
                if not drive.id or not drive.name or drive.hidden:
                    continue
                out.append(DriveSummary(id=drive.id, name=drive.name))
            page_token = resp.get("nextPageToken")
            if not page_token:
                return out

    def _walk_folders_sync(self, drive_id: DriveId) -> DriveTree:
        client = self._client()
        out = DriveTree(drive_id=drive_id, folders=[], files=[], files_complete=True)
        page_token: str | None = None
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
                    "walk_drive_folders failed user={user_id} drive={drive_id}",
                    user_id=self._user.id,
                    drive_id=drive_id,
                )
                return out
            for raw in resp.get("files", []):
                node = _node_from_api(GoogleFile.model_validate(raw), drive_id)
                if node.mime_type == MIME_SHORTCUT and (
                    node.shortcut_target_mime_type != MIME_FOLDER
                ):
                    continue
                out.folders.append(node)
            page_token = resp.get("nextPageToken")
            if not page_token:
                return out

    def _list_children_sync(self, folder_id: FileId) -> list[DriveNode]:
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
                    "list_folder_children failed user={user_id} folder={folder_id}",
                    user_id=self._user.id,
                    folder_id=folder_id,
                )
                return out
            for raw in resp.get("files", []):
                file = GoogleFile.model_validate(raw)
                details = file.shortcut_details
                target_mime = details.target_mime_type if details else None
                if (
                    file.mime_type == MIME_SHORTCUT
                    and target_mime not in NAVIGATOR_SUPPORTED_MIME_TYPES
                ):
                    continue
                out.append(
                    DriveNode(
                        id=file.id,
                        name=file.name,
                        mime_type=file.mime_type,
                        parent_id=folder_id,
                        modified_time=file.modified_time,
                        shortcut_target_id=details.target_id if details else None,
                        shortcut_target_mime_type=target_mime,
                    )
                )
            page_token = resp.get("nextPageToken")
            if not page_token:
                return out

    def _list_drive_files_sync(self, drive_id: DriveId) -> tuple[list[DriveNode], bool]:
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
                    "list_drive_files failed user={user_id} drive={drive_id}",
                    user_id=self._user.id,
                    drive_id=drive_id,
                )
                return out, False
            for raw in resp.get("files", []):
                file = GoogleFile.model_validate(raw)
                if file.mime_type == MIME_FOLDER:
                    continue
                details = file.shortcut_details
                target_mime = details.target_mime_type if details else None
                if file.mime_type == MIME_SHORTCUT and (
                    target_mime == MIME_FOLDER or target_mime not in NAVIGATOR_SUPPORTED_MIME_TYPES
                ):
                    continue
                out.append(_node_from_api(file, drive_id))
            page_token = resp.get("nextPageToken")
            if not page_token:
                return out, True
            pages += 1
            if pages >= _MAX_DRIVE_FILE_PAGES:
                logger.warning(
                    "drive file listing truncated at {pages} pages drive={drive_id}",
                    pages=pages,
                    drive_id=drive_id,
                )
                return out, False

    def _search_drive_sync(self, drive_id: DriveId, query: str, limit: int) -> list[SearchResult]:
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
                "search_drive failed user={user_id} drive={drive_id} q={query!r}",
                user_id=self._user.id,
                drive_id=drive_id,
                query=query,
            )
            return []
        return [_search_result_from_api(raw, drive_id) for raw in resp.get("files", [])]

    def _search_all_drives_sync(self, query: str, page_size: int) -> list[SearchResult] | None:
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
            logger.exception(
                "all-drives search failed user={user_id} q={query!r}",
                user_id=self._user.id,
                query=query,
            )
            return None
        if resp.get("incompleteResults"):
            return None
        return [_search_result_from_api(raw) for raw in resp.get("files", [])]

    def _fetch_snippet_sync(
        self, file_id: FileId, query: str, mime_type: str | None
    ) -> SnippetResult:
        client = self._client()
        if mime_type is None:
            try:
                meta = execute_with_retry(
                    client.files().get(
                        fileId=file_id, fields="id,mimeType,size", supportsAllDrives=True
                    )
                )
            except HttpError:
                logger.exception("snippet metadata fetch failed file={file_id}", file_id=file_id)
                return SnippetResult(file_id=file_id, mime_type="", snippet=None, match_term=None)
            mime_type = GoogleFileMetadata.model_validate(meta).mime_type

        if mime_type not in _TEXT_EXPORTABLE_MIME_TYPES:
            return SnippetResult(
                file_id=file_id, mime_type=mime_type, snippet=None, match_term=None
            )

        try:
            raw = execute_with_retry(client.files().export(fileId=file_id, mimeType="text/plain"))
        except HttpError:
            logger.exception("snippet export failed file={file_id}", file_id=file_id)
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
        return reuse_drive_client(self._user, self._clients)

    @staticmethod
    def build_drive_client(user: User) -> DriveResource:
        authed_http = google_auth_httplib2.AuthorizedHttp(
            GoogleDriveService._build_credentials(user),
            http=httplib2.Http(timeout=_DRIVE_HTTP_TIMEOUT_SECONDS),
        )
        built = discovery.build_from_document(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
            drive_discovery_document(), http=authed_http
        )
        return cast("DriveResource", built)

    @staticmethod
    def _build_credentials(user: User) -> Credentials:
        if user.encrypted_access_token is None:
            msg = f"User {user.id} has no encrypted_access_token"
            raise ValueError(msg)
        access_token = decrypt_text(user.encrypted_access_token)
        refresh_token = (
            decrypt_text(user.encrypted_refresh_token) if user.encrypted_refresh_token else None
        )
        return Credentials(  # type: ignore[no-untyped-call]
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",  # ruff: ignore[hardcoded-password-func-arg] — public OAuth endpoint URL, not a credential
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET.get_secret_value(),
        )


def reuse_drive_client(user: User, clients: dict[int, DriveResource]) -> DriveResource:
    thread_id = threading.get_ident()
    client = clients.get(thread_id)
    if client is None:
        client = GoogleDriveService.build_drive_client(user)
        clients[thread_id] = client
    return client


@cache
def drive_discovery_document() -> dict[str, Any]:
    content = get_static_doc("drive", "v3")  # type: ignore[no-untyped-call]
    if content is None:
        msg = "Bundled Drive v3 discovery document not found"
        raise RuntimeError(msg)
    return cast("dict[str, Any]", json.loads(content))


@drive_retry
def _execute_batch_with_retry(batch: BatchHttpRequest) -> None:
    batch.execute()


def _build_mime_clause() -> str:
    quoted = " or ".join(
        f"mimeType='{m}'" for m in (*NAVIGATOR_SUPPORTED_MIME_TYPES, MIME_SHORTCUT)
    )
    return f"({quoted})"


def _search_result_from_api(raw: Any, drive_id: DriveId | None = None) -> SearchResult:
    file = GoogleFile.model_validate(raw)
    home_drive_id = file.drive_id or drive_id
    parent_id: FileId | None = file.parents[0] if file.parents else None
    if home_drive_id is not None and parent_id == FileId(home_drive_id):
        parent_id = None
    details = file.shortcut_details
    return SearchResult(
        id=file.id,
        name=file.name,
        mime_type=file.mime_type,
        drive_id=file.drive_id,
        drive_name=None,
        parent_id=parent_id,
        modified_time=file.modified_time,
        shortcut_target_id=details.target_id if details else None,
        shortcut_target_mime_type=details.target_mime_type if details else None,
    )


def _node_from_api(file: GoogleFile, drive_id: DriveId) -> DriveNode:
    parent_id: FileId | None = file.parents[0] if file.parents else None
    if parent_id == FileId(drive_id):
        parent_id = None
    details = file.shortcut_details
    return DriveNode(
        id=file.id,
        name=file.name,
        mime_type=file.mime_type,
        parent_id=parent_id,
        modified_time=file.modified_time,
        shortcut_target_id=details.target_id if details else None,
        shortcut_target_mime_type=details.target_mime_type if details else None,
    )
