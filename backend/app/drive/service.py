from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, timedelta
from functools import cache
from itertools import batched
import json
import threading
from typing import TYPE_CHECKING, Any, NamedTuple, override

from google.oauth2.credentials import Credentials
import google_auth_httplib2
from googleapiclient import discovery
from googleapiclient.discovery_cache import get_static_doc
from googleapiclient.errors import HttpError
import httplib2
from loguru import logger
from pydantic import TypeAdapter, ValidationError

from app.auth.encryption import decrypt_text
from app.auth.google_tokens import GoogleRefreshResult
from app.cache.service import CacheNamespace, CacheService
from app.drive.errors import (
    DrivePermissionsForbiddenError,
    DrivePermissionsNotFoundError,
    DrivePermissionsUpstreamError,
    DriveTraversalIncompleteError,
)
from app.drive.google_schemas import (
    GoogleDrive,
    GoogleFile,
    GoogleFileLocation,
    GoogleFileMetadata,
    GoogleFileProbe,
    GooglePermission,
)
from app.drive.retry import execute_batch_with_retry, execute_with_retry, provider_http_status
from app.drive.schemas import (
    MIME_FOLDER,
    MIME_SHORTCUT,
    NAVIGATOR_SUPPORTED_MIME_TYPES,
    DriveNode,
    DrivePermission,
    DrivePermissionsPage,
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

_DRIVE_HTTP_TIMEOUT = timedelta(seconds=10)

_PROBE_BATCH_SIZE = 100

_SEARCH_PAGE_SIZE = 50

_MAX_DRIVE_FILE_PAGES = 3

_MAX_PERSONAL_ANCESTRY_NODES = 64

_MY_DRIVE_ALIAS = FileId("root")

_MY_DRIVE_NAME = "My Drive"

_OWNED_BY_ME_CLAUSE = " and 'me' in owners"

_TEXT_EXPORTABLE_MIME_TYPES = frozenset(
    {
        "application/vnd.google-apps.document",
        "application/vnd.google-apps.spreadsheet",
        "application/vnd.google-apps.presentation",
    }
)

_MAX_EXPORT_BYTES = 2 * 1024 * 1024

_MAX_REORGANIZATION_TEXT_CHARS = 12_000

_DRIVE_LIST_CACHE_TTL = timedelta(seconds=30)

_PROBE_CACHE_TTL = timedelta(minutes=5)

_DRIVE_LIST = TypeAdapter(list[DriveSummary])

_SHORTCUT_PROBE: TypeAdapter[ShortcutProbe | None] = TypeAdapter(ShortcutProbe | None)

GoogleRefreshHandler = Callable[..., GoogleRefreshResult]


class _DriveFilesResult(NamedTuple):
    files: list[DriveNode]
    complete: bool


class BoundedSearchResult(NamedTuple):
    files: list[SearchResult]
    truncated: bool


class DriveService(ABC):
    @property
    def google_refresh_handler(self) -> GoogleRefreshHandler | None:
        return None

    @abstractmethod
    async def list_drives(self) -> list[DriveSummary]: ...

    async def resolve_member_drive(
        self,
        file_id: FileId,
        drive_id: DriveId | None,
        *,
        parent_id: FileId | None = None,
    ) -> DriveId | None:
        del parent_id
        if not file_id or drive_id is None:
            return None
        drives = await self.list_drives()
        return drive_id if any(d.id == drive_id and not d.is_personal for d in drives) else None

    async def resolve_member_drives(
        self,
        files: Mapping[FileId, DriveId | None],
        *,
        parent_ids: Mapping[FileId, FileId | None] | None = None,
    ) -> dict[FileId, DriveId]:
        del parent_ids
        drives = await self.list_drives()
        shared_drive_ids = {drive.id for drive in drives if not drive.is_personal}
        return {
            file_id: drive_id
            for file_id, drive_id in files.items()
            if drive_id is not None and drive_id in shared_drive_ids
        }

    @abstractmethod
    async def walk_drive_folders(self, drive_id: DriveId) -> DriveTree: ...

    @abstractmethod
    async def list_folder_children(self, folder_id: FileId) -> list[DriveNode]: ...

    async def list_folder_children_bounded(
        self,
        folder_id: FileId,
        *,
        max_pages: int,
        max_children: int,
    ) -> list[DriveNode]:
        raise NotImplementedError

    @abstractmethod
    async def search_drive(
        self, drive_id: DriveId, query: str, *, limit: int = 50
    ) -> list[SearchResult]: ...

    @abstractmethod
    async def search(self, query: str, *, limit_per_drive: int = 20) -> list[SearchResult]: ...

    def search_bounded(
        self, query: str, *, limit: int = 50
    ) -> AbstractAsyncContextManager[BoundedSearchResult]:
        raise NotImplementedError

    @abstractmethod
    async def fetch_snippet(
        self, file_id: FileId, query: str, *, mime_type: str | None = None
    ) -> SnippetResult: ...

    async def fetch_document_text(self, file_id: FileId) -> str | None:
        raise NotImplementedError

    async def list_drive_permissions(
        self,
        drive_id: DriveId,
        *,
        page_token: str | None = None,
    ) -> DrivePermissionsPage:
        raise NotImplementedError


class GoogleDriveService(DriveService):
    def __init__(
        self,
        user: User,
        cache_service: CacheService | None = None,
        *,
        refresh_handler: GoogleRefreshHandler | None = None,
    ) -> None:
        self._user = user
        self._cache = cache_service
        self._refresh_handler = refresh_handler
        self._clients: dict[int, DriveResource] = {}
        self._drives_task: asyncio.Task[list[DriveSummary]] | None = None
        self._my_drive_id: DriveId | None = None
        self._bounded_drives: ContextVar[list[DriveSummary] | None] = ContextVar(
            "bounded_search_drives", default=None
        )

    @property
    @override
    def google_refresh_handler(self) -> GoogleRefreshHandler | None:
        return self._refresh_handler

    @override
    async def list_drives(self) -> list[DriveSummary]:
        bounded = self._bounded_drives.get()
        if bounded is not None:
            return bounded
        task = self._drives_task
        if task is None:
            task = asyncio.create_task(self._load_drives())
            self._drives_task = task
        try:
            return await asyncio.shield(task)
        except BaseException:
            if task.done() and self._drives_task is task:
                self._drives_task = None
            raise

    async def _load_drives(self) -> list[DriveSummary]:
        cache_key = str(self._user.id)
        if self._cache is not None:
            cached = await self._cache.get(CacheNamespace.DRIVE_LIST, cache_key)
            if cached is not None:
                try:
                    drives = _DRIVE_LIST.validate_python(cached)
                except ValidationError:
                    await self._cache.delete(CacheNamespace.DRIVE_LIST, cache_key)
                else:
                    self._my_drive_id = next((d.id for d in drives if d.is_personal), None)
                    return drives
        drives = await asyncio.to_thread(self._list_drives_sync)
        if self._cache is not None:
            await self._cache.put(
                CacheNamespace.DRIVE_LIST,
                cache_key,
                [drive.model_dump(mode="json") for drive in drives],
                ttl=_DRIVE_LIST_CACHE_TTL,
            )
        self._my_drive_id = next((d.id for d in drives if d.is_personal), None)
        return drives

    @override
    async def resolve_member_drive(
        self,
        file_id: FileId,
        drive_id: DriveId | None,
        *,
        parent_id: FileId | None = None,
    ) -> DriveId | None:
        if drive_id is not None:
            return await super().resolve_member_drive(file_id, drive_id, parent_id=parent_id)
        drives = await self.list_drives()
        personal = next((d for d in drives if d.is_personal), None)
        if personal is None:
            return None
        is_descendant = await asyncio.to_thread(
            self._is_personal_descendant_sync,
            file_id=file_id,
            root_id=FileId(personal.id),
            parent_id=parent_id,
            strict=self._bounded_drives.get() is not None,
        )
        return personal.id if is_descendant else None

    @override
    async def resolve_member_drives(
        self,
        files: Mapping[FileId, DriveId | None],
        *,
        parent_ids: Mapping[FileId, FileId | None] | None = None,
    ) -> dict[FileId, DriveId]:
        drives = await self.list_drives()
        shared_drive_ids = {drive.id for drive in drives if not drive.is_personal}
        resolved = {
            file_id: drive_id
            for file_id, drive_id in files.items()
            if drive_id is not None and drive_id in shared_drive_ids
        }
        personal = next((drive for drive in drives if drive.is_personal), None)
        if personal is None:
            return resolved
        personal_ids = [file_id for file_id, drive_id in files.items() if drive_id is None]
        if self._bounded_drives.get() is not None:
            for file_id in personal_ids:
                if await asyncio.to_thread(
                    self._is_personal_descendant_sync,
                    file_id=file_id,
                    root_id=FileId(personal.id),
                    parent_id=parent_ids.get(file_id) if parent_ids is not None else None,
                    strict=True,
                ):
                    resolved[file_id] = personal.id
            return resolved
        descendants = await asyncio.gather(
            *(
                asyncio.to_thread(
                    self._is_personal_descendant_sync,
                    file_id=file_id,
                    root_id=FileId(personal.id),
                    parent_id=parent_ids.get(file_id) if parent_ids is not None else None,
                )
                for file_id in personal_ids
            )
        )
        resolved.update(
            (file_id, personal.id)
            for file_id, is_descendant in zip(personal_ids, descendants, strict=True)
            if is_descendant
        )
        return resolved

    @override
    async def walk_drive_folders(self, drive_id: DriveId) -> DriveTree:
        is_personal = await self._is_personal_drive(drive_id)
        tree, (files, files_complete) = await asyncio.gather(
            asyncio.to_thread(self._walk_folders_sync, drive_id, is_personal=is_personal),
            asyncio.to_thread(self._list_drive_files_sync, drive_id, is_personal=is_personal),
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
    async def list_folder_children_bounded(
        self,
        folder_id: FileId,
        *,
        max_pages: int,
        max_children: int,
    ) -> list[DriveNode]:
        if max_pages < 1 or max_children < 1:
            raise ValueError
        nodes = await asyncio.to_thread(
            self._list_children_sync,
            folder_id,
            max_pages=max_pages,
            max_children=max_children,
            fail_closed=True,
        )
        return await self._resolve_shortcut_nodes(nodes)

    @override
    async def search_drive(
        self, drive_id: DriveId, query: str, *, limit: int = 50
    ) -> list[SearchResult]:
        if not query.strip():
            return []
        is_personal = await self._is_personal_drive(drive_id)
        return await asyncio.to_thread(
            self._search_drive_sync,
            drive_id,
            query.strip(),
            limit,
            is_personal=is_personal,
        )

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
            my_drive = next((d for d in drives if d.is_personal), None)
            merged = [
                hit.model_copy(update={"drive_name": names[drive_id]})
                for hit in hits
                if (drive_id := hit.drive_id) is not None and drive_id in names
            ]
            if my_drive is not None:
                personal = await self.search_drive(my_drive.id, stripped, limit=limit_per_drive)
                merged.extend(
                    hit.model_copy(update={"drive_name": my_drive.name}) for hit in personal
                )
        return await self._resolve_shortcut_search_results(merged)

    @override
    @asynccontextmanager
    async def search_bounded(
        self, query: str, *, limit: int = 50
    ) -> AsyncGenerator[BoundedSearchResult]:
        if not query.strip() or len(query) > 2048 or type(limit) is not int or not 1 <= limit <= 50:
            msg = "Invalid bounded search request"
            raise ValueError(msg)
        drives = await asyncio.to_thread(self._bounded_drives_sync)
        token = self._bounded_drives.set(drives)
        try:
            hits: list[SearchResult] = []
            for drive in drives:
                remaining = limit - len(hits)
                if remaining == 0:
                    break
                hits.extend(
                    await asyncio.to_thread(self._bounded_search_sync, drive, query, remaining)
                )
            # A single page per drive and bounded discovery are never exhaustive.
            yield BoundedSearchResult(hits, truncated=True)
        finally:
            self._bounded_drives.reset(token)

    def _bounded_drives_sync(self) -> list[DriveSummary]:
        client = self._client()
        root = GoogleFileLocation.model_validate(
            execute_with_retry(
                client.files().get(fileId=_MY_DRIVE_ALIAS, fields="id", supportsAllDrives=True)
            )
        )
        response = execute_with_retry(
            client.drives().list(
                pageSize=7,
                q="hidden = false",
                useDomainAdminAccess=False,
                fields="nextPageToken,drives(id,name,hidden)",
            )
        )
        records = response.get("drives", [])
        if not isinstance(records, list) or len(records) > 7:
            msg = "Invalid bounded drive inventory"
            raise ValueError(msg)
        drives = [DriveSummary(id=DriveId(root.id), name=_MY_DRIVE_NAME, is_personal=True)]
        for record in records:
            drive = GoogleDrive.model_validate(record)
            if not drive.hidden:
                drives.append(DriveSummary(id=drive.id, name=drive.name))
        return drives

    def _bounded_search_sync(
        self, drive: DriveSummary, query: str, limit: int
    ) -> list[SearchResult]:
        escaped = escape_query_value(query.strip())
        q = f"(name contains '{escaped}' or fullText contains '{escaped}') and trashed=false"
        if drive.is_personal:
            q += _OWNED_BY_ME_CLAUSE
        params: dict[str, Any] = {
            "q": q,
            "corpora": "user" if drive.is_personal else "drive",
            "includeItemsFromAllDrives": True,
            "supportsAllDrives": True,
            "pageSize": limit,
            "fields": "nextPageToken,files(id,name,mimeType,parents,modifiedTime,driveId)",
        }
        if not drive.is_personal:
            params["driveId"] = drive.id
        response = execute_with_retry(self._client().files().list(**params))
        records = response.get("files", [])
        if not isinstance(records, list) or len(records) > limit:
            msg = "Invalid bounded search response"
            raise ValueError(msg)
        hits: list[SearchResult] = []
        for record in records:
            hit = _search_result_from_api(record, drive.id)
            if drive.is_personal and hit.drive_id is not None:
                continue
            hits.append(hit.model_copy(update={"drive_name": drive.name}))
        return hits

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

    @override
    async def fetch_document_text(self, file_id: FileId) -> str | None:
        return await asyncio.to_thread(self._fetch_document_text_sync, file_id)

    @override
    async def list_drive_permissions(
        self,
        drive_id: DriveId,
        *,
        page_token: str | None = None,
    ) -> DrivePermissionsPage:
        if await self._is_personal_drive(drive_id):
            return DrivePermissionsPage(
                access_model="item_specific",
                drive_id=drive_id,
                permissions=[],
            )
        return await asyncio.to_thread(
            self._list_drive_permissions_sync,
            drive_id,
            page_token,
        )

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

    async def _is_personal_drive(self, drive_id: DriveId) -> bool:
        if self._my_drive_id is None:
            await self.list_drives()
        return drive_id == self._my_drive_id

    async def _probe_targets(self, target_ids: list[FileId | None]) -> list[ShortcutProbe | None]:
        unique_ids = sorted({tid for tid in target_ids if tid})
        if not unique_ids:
            return [None] * len(target_ids)
        probes: dict[FileId, ShortcutProbe | None] = {}
        if self._cache is not None:
            cache_keys = [self._probe_cache_key(target_id) for target_id in unique_ids]
            cached = await self._cache.get_many(CacheNamespace.SHORTCUT_PROBE, cache_keys)
            for target_id in unique_ids:
                cache_key = self._probe_cache_key(target_id)
                if cache_key in cached:
                    try:
                        probes[target_id] = _SHORTCUT_PROBE.validate_python(cached[cache_key])
                    except ValidationError:
                        await self._cache.delete(CacheNamespace.SHORTCUT_PROBE, cache_key)
        missing = [tid for tid in unique_ids if tid not in probes]
        if missing:
            fetched = await asyncio.to_thread(self._probe_targets_sync, missing)
            if self._cache is not None:
                await self._cache.put_many(
                    CacheNamespace.SHORTCUT_PROBE,
                    {
                        self._probe_cache_key(target_id): (
                            None
                            if probe is None
                            else {
                                "accessible": probe.accessible,
                                "target_drive_id": probe.target_drive_id,
                            }
                        )
                        for target_id, probe in fetched.items()
                    },
                    ttl=_PROBE_CACHE_TTL,
                )
            probes.update(fetched)
        return [probes.get(tid) if tid else None for tid in target_ids]

    def _probe_cache_key(self, target_id: FileId) -> str:
        return f"{self._user.id}:{target_id}"

    def _probe_targets_sync(self, target_ids: list[FileId]) -> dict[FileId, ShortcutProbe | None]:
        client = self._client()
        probes: dict[FileId, ShortcutProbe | None] = {}

        def _collect(request_id: str, response: Any, exception: BaseException | None) -> None:
            target_id = FileId(request_id)
            probes[target_id] = self._classify_probe(response, exception)

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
                execute_batch_with_retry(batch)
            except HttpError as exc:
                _log_provider_failure("shortcut_probe_batch", exc)
                for tid in chunk:
                    probes.setdefault(tid, None)
        return probes

    @staticmethod
    def _classify_probe(response: Any, exception: BaseException | None) -> ShortcutProbe | None:
        if exception is not None:
            status = getattr(getattr(exception, "resp", None), "status", None)
            if status == 404:
                return None
            if status == 403:
                return ShortcutProbe(accessible=False)
            _log_provider_failure("shortcut_target_probe", exception)
            return None
        probe = GoogleFileProbe.model_validate(response)
        if probe.trashed:
            return None
        return ShortcutProbe(accessible=True, target_drive_id=probe.drive_id)

    def _list_drives_sync(self) -> list[DriveSummary]:
        client = self._client()
        out: list[DriveSummary] = []
        try:
            raw = execute_with_retry(
                client.files().get(fileId=_MY_DRIVE_ALIAS, fields="id", supportsAllDrives=True)
            )
            root = GoogleFileLocation.model_validate(raw)
            out.append(DriveSummary(id=DriveId(root.id), name=_MY_DRIVE_NAME, is_personal=True))
        except HttpError as exc:
            _log_provider_failure("my_drive_root", exc)
        except ValidationError:
            _log_invalid_provider_response("my_drive_root")
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
            except HttpError as exc:
                _log_provider_failure("list_drives", exc)
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

    def _is_personal_descendant_sync(
        self,
        *,
        file_id: FileId,
        root_id: FileId,
        parent_id: FileId | None = None,
        strict: bool = False,
    ) -> bool:
        client = self._client()
        if file_id == root_id:
            return True
        if parent_id is None:
            return False
        if parent_id == root_id:
            return True
        pending = [parent_id]
        visited: set[FileId] = set()
        while pending and len(visited) < _MAX_PERSONAL_ANCESTRY_NODES:
            current = pending.pop()
            if current in visited:
                continue
            if current == root_id:
                return True
            visited.add(current)
            try:
                raw = execute_with_retry(
                    client.files().get(
                        fileId=current,
                        fields="id,driveId,parents",
                        supportsAllDrives=True,
                    )
                )
                location = GoogleFileLocation.model_validate(raw)
            except HttpError as exc:
                if strict and provider_http_status(exc) not in {403, 404}:
                    raise
                _log_provider_failure("verify_my_drive_ancestry", exc)
                return False
            except ValidationError:
                if strict:
                    raise
                _log_invalid_provider_response("verify_my_drive_ancestry")
                return False
            if strict and location.id != current:
                message = "Invalid personal ancestry response"
                raise ValueError(message)
            if location.drive_id is not None:
                return False
            if location.id == root_id:
                return True
            pending.extend(parent for parent in location.parents if parent not in visited)
        return False

    def _walk_folders_sync(self, drive_id: DriveId, *, is_personal: bool = False) -> DriveTree:
        client = self._client()
        out = DriveTree(drive_id=drive_id, folders=[], files=[], files_complete=True)
        page_token: str | None = None
        query = f"(mimeType='{MIME_FOLDER}' or mimeType='{MIME_SHORTCUT}') and trashed=false"
        if is_personal:
            query += _OWNED_BY_ME_CLAUSE
        while True:
            params: dict[str, Any] = {
                "q": query,
                "corpora": "user" if is_personal else "drive",
                "includeItemsFromAllDrives": True,
                "supportsAllDrives": True,
                "pageSize": 1000,
                "fields": (
                    "nextPageToken,files(id,name,mimeType,parents,modifiedTime,driveId,"
                    "shortcutDetails(targetId,targetMimeType))"
                ),
            }
            if not is_personal:
                params["driveId"] = drive_id
            if page_token:
                params["pageToken"] = page_token
            try:
                resp = execute_with_retry(client.files().list(**params))
            except HttpError as exc:
                _log_provider_failure("walk_drive_folders", exc)
                return out
            for raw in resp.get("files", []):
                file = GoogleFile.model_validate(raw)
                if is_personal and file.drive_id is not None:
                    continue
                node = _node_from_api(file, drive_id)
                if node.mime_type == MIME_SHORTCUT and (
                    node.shortcut_target_mime_type != MIME_FOLDER
                ):
                    continue
                out.folders.append(node)
            page_token = resp.get("nextPageToken")
            if not page_token:
                return out

    def _list_children_sync(
        self,
        folder_id: FileId,
        *,
        max_pages: int | None = None,
        max_children: int | None = None,
        fail_closed: bool = False,
    ) -> list[DriveNode]:
        client = self._client()
        out: list[DriveNode] = []
        raw_children = 0
        page_token: str | None = None
        pages = 0
        query = f"'{folder_id}' in parents and trashed=false and {_build_mime_clause()}"
        while True:
            if max_pages is not None and pages >= max_pages:
                raise DriveTraversalIncompleteError
            pages += 1
            params: dict[str, Any] = {
                "q": query,
                "corpora": "allDrives",
                "includeItemsFromAllDrives": True,
                "supportsAllDrives": True,
                "pageSize": 1000,
                "fields": (
                    "nextPageToken,files(id,name,mimeType,parents,modifiedTime,description,"
                    "shortcutDetails(targetId,targetMimeType))"
                ),
            }
            if page_token:
                params["pageToken"] = page_token
            try:
                resp = execute_with_retry(client.files().list(**params))
            except HttpError as exc:
                _log_provider_failure("list_folder_children", exc)
                if fail_closed:
                    raise DriveTraversalIncompleteError from exc
                return out
            raw_files = resp.get("files", [])
            raw_children += len(raw_files)
            if max_children is not None and raw_children > max_children:
                raise DriveTraversalIncompleteError
            for raw in raw_files:
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
                        description=file.description,
                        shortcut_target_id=details.target_id if details else None,
                        shortcut_target_mime_type=target_mime,
                    )
                )
            page_token = resp.get("nextPageToken")
            if not page_token:
                return out

    def _list_drive_files_sync(
        self, drive_id: DriveId, *, is_personal: bool = False
    ) -> _DriveFilesResult:
        client = self._client()
        out: list[DriveNode] = []
        page_token: str | None = None
        pages = 0
        query = f"trashed=false and {_build_mime_clause()}"
        if is_personal:
            query += _OWNED_BY_ME_CLAUSE
        while True:
            params: dict[str, Any] = {
                "q": query,
                "corpora": "user" if is_personal else "drive",
                "includeItemsFromAllDrives": True,
                "supportsAllDrives": True,
                "pageSize": 1000,
                "fields": (
                    "nextPageToken,files(id,name,mimeType,parents,modifiedTime,driveId,"
                    "shortcutDetails(targetId,targetMimeType))"
                ),
            }
            if not is_personal:
                params["driveId"] = drive_id
            if page_token:
                params["pageToken"] = page_token
            try:
                resp = execute_with_retry(client.files().list(**params))
            except HttpError as exc:
                _log_provider_failure("list_drive_files", exc)
                return _DriveFilesResult(files=out, complete=False)
            for raw in resp.get("files", []):
                file = GoogleFile.model_validate(raw)
                if is_personal and file.drive_id is not None:
                    continue
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
                return _DriveFilesResult(files=out, complete=True)
            pages += 1
            if pages >= _MAX_DRIVE_FILE_PAGES:
                logger.warning("Drive file listing truncated pages={pages}", pages=pages)
                return _DriveFilesResult(files=out, complete=False)

    def _search_drive_sync(
        self, drive_id: DriveId, query: str, limit: int, *, is_personal: bool = False
    ) -> list[SearchResult]:
        escaped = escape_query_value(query)
        q = f"(name contains '{escaped}' or fullText contains '{escaped}') and trashed=false"
        if is_personal:
            q += _OWNED_BY_ME_CLAUSE
        client = self._client()
        params: dict[str, Any] = {
            "q": q,
            "corpora": "user" if is_personal else "drive",
            "includeItemsFromAllDrives": True,
            "supportsAllDrives": True,
            "pageSize": limit,
            "fields": (
                "files(id,name,mimeType,parents,modifiedTime,driveId,"
                "shortcutDetails(targetId,targetMimeType))"
            ),
        }
        if not is_personal:
            params["driveId"] = drive_id
        try:
            resp = execute_with_retry(client.files().list(**params))
        except HttpError as exc:
            _log_provider_failure("search_drive", exc)
            return []
        results: list[SearchResult] = []
        for raw in resp.get("files", []):
            result = _search_result_from_api(raw, drive_id)
            if is_personal and result.drive_id is not None:
                continue
            if is_personal:
                result = result.model_copy(update={"drive_id": drive_id})
            results.append(result)
        return results

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
        except HttpError as exc:
            _log_provider_failure("search_all_drives", exc)
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
            except HttpError as exc:
                _log_provider_failure("fetch_snippet_metadata", exc)
                return SnippetResult(file_id=file_id, mime_type="", snippet=None, match_term=None)
            mime_type = GoogleFileMetadata.model_validate(meta).mime_type

        if mime_type not in _TEXT_EXPORTABLE_MIME_TYPES:
            return SnippetResult(
                file_id=file_id, mime_type=mime_type, snippet=None, match_term=None
            )

        text = self._export_text_sync(file_id)
        if text is None:
            return SnippetResult(
                file_id=file_id, mime_type=mime_type, snippet=None, match_term=None
            )

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

    def _fetch_document_text_sync(self, file_id: FileId) -> str | None:
        text = self._export_text_sync(file_id)
        if text is None:
            return None
        collapsed = " ".join(text.split())
        if not collapsed:
            return None
        if len(collapsed) <= _MAX_REORGANIZATION_TEXT_CHARS:
            return collapsed
        head_chars = _MAX_REORGANIZATION_TEXT_CHARS * 2 // 3
        tail_chars = _MAX_REORGANIZATION_TEXT_CHARS - head_chars
        return f"{collapsed[:head_chars]} … [middle omitted] … {collapsed[-tail_chars:]}"

    def _list_drive_permissions_sync(
        self,
        drive_id: DriveId,
        page_token: str | None,
    ) -> DrivePermissionsPage:
        params: dict[str, Any] = {
            "fields": (
                "nextPageToken,permissions(id,type,role,displayName,emailAddress,domain,"
                "allowFileDiscovery,expirationTime,deleted,pendingOwner)"
            ),
            "fileId": drive_id,
            "pageSize": 100,
            "supportsAllDrives": True,
            "useDomainAdminAccess": False,
        }
        if page_token:
            params["pageToken"] = page_token
        try:
            raw = execute_with_retry(self._client().permissions().list(**params))
        except HttpError as exc:
            status = getattr(exc.resp, "status", None)
            if status == 403:
                raise DrivePermissionsForbiddenError from None
            if status == 404:
                raise DrivePermissionsNotFoundError from None
            raise DrivePermissionsUpstreamError from None

        permissions: list[DrivePermission] = []
        for item in raw.get("permissions", []):
            try:
                permission = GooglePermission.model_validate(item)
            except ValidationError:
                _log_invalid_provider_response("list_drive_permissions")
                continue
            permissions.append(
                DrivePermission(
                    allow_file_discovery=permission.allow_file_discovery,
                    deleted=permission.deleted,
                    display_name=permission.display_name,
                    domain=permission.domain,
                    email_address=permission.email_address,
                    expiration_time=permission.expiration_time,
                    id=permission.id,
                    pending_owner=permission.pending_owner,
                    principal_type=permission.type,
                    role=permission.role,
                )
            )
        return DrivePermissionsPage(
            access_model="shared_drive_membership",
            drive_id=drive_id,
            next_page_token=raw.get("nextPageToken"),
            permissions=permissions,
        )

    def _export_text_sync(self, file_id: FileId) -> str | None:
        client = self._client()
        try:
            raw = execute_with_retry(client.files().export(fileId=file_id, mimeType="text/plain"))
        except HttpError as exc:
            _log_provider_failure("export_document_text", exc)
            return None
        if isinstance(raw, bytes):
            return raw[:_MAX_EXPORT_BYTES].decode("utf-8", errors="replace")
        return str(raw)[: _MAX_EXPORT_BYTES // 2]

    def _client(self) -> DriveResource:
        return reuse_drive_client(
            self._user,
            self._clients,
            refresh_handler=self._refresh_handler,
        )

    @staticmethod
    def build_drive_client(
        user: User,
        refresh_handler: GoogleRefreshHandler | None = None,
    ) -> DriveResource:
        authed_http = google_auth_httplib2.AuthorizedHttp(
            GoogleDriveService.build_credentials(user, refresh_handler),
            http=httplib2.Http(timeout=_DRIVE_HTTP_TIMEOUT.total_seconds()),
        )
        built: DriveResource = discovery.build_from_document(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
            drive_discovery_document(), http=authed_http
        )
        return built

    @staticmethod
    def build_credentials(
        user: User,
        refresh_handler: GoogleRefreshHandler | None = None,
    ) -> Credentials:
        if user.encrypted_access_token is None:
            msg = "User has no encrypted Drive access token"
            raise ValueError(msg)
        access_token = decrypt_text(user.encrypted_access_token)
        return Credentials(  # type: ignore[no-untyped-call]
            token=access_token,
            expiry=(
                user.google_access_token_expires_at.astimezone(UTC).replace(tzinfo=None)
                if user.google_access_token_expires_at is not None
                else None
            ),
            refresh_handler=refresh_handler,
        )


def reuse_drive_client(
    user: User,
    clients: dict[int, DriveResource],
    *,
    refresh_handler: GoogleRefreshHandler | None = None,
) -> DriveResource:
    thread_id = threading.get_ident()
    client = clients.get(thread_id)
    if client is None:
        client = (
            GoogleDriveService.build_drive_client(user, refresh_handler)
            if refresh_handler is not None
            else GoogleDriveService.build_drive_client(user)
        )
        clients[thread_id] = client
    return client


def build_google_credentials(
    user: User,
    refresh_handler: GoogleRefreshHandler | None = None,
) -> Credentials:
    return GoogleDriveService.build_credentials(
        user,
        refresh_handler,
    )


def _log_provider_failure(operation: str, exc: BaseException) -> None:
    status = getattr(getattr(exc, "resp", None), "status", None)
    logger.warning(
        "Google Drive request failed operation={operation} status={status}",
        operation=operation,
        status=status if isinstance(status, int) else "unknown",
    )


def _log_invalid_provider_response(operation: str) -> None:
    logger.warning("Google Drive response was invalid operation={operation}", operation=operation)


@cache
def drive_discovery_document() -> dict[str, Any]:
    content = get_static_doc("drive", "v3")  # type: ignore[no-untyped-call]
    if content is None:
        msg = "Bundled Drive v3 discovery document not found"
        raise RuntimeError(msg)
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        msg = "Bundled Drive v3 discovery document is not an object"
        raise TypeError(msg)
    return parsed


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
