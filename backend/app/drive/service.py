from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import AsyncGenerator, Callable, Iterable, Mapping, Sequence
from concurrent.futures import Future
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
from pydantic import RootModel, ValidationError

from app.auth.encryption import decrypt_text
from app.auth.google_tokens import GoogleRefreshResult, provider_refresh_scope
from app.cache.service import CacheNamespace, CacheService
from app.drive.errors import (
    DrivePermissionsError,
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
    GoogleFilePermissionMetadata,
    GoogleFileProbe,
    GooglePermission,
)
from app.drive.retry import execute_batch_with_retry, execute_with_retry, provider_http_status
from app.drive.schemas import (
    MIME_DOC,
    MIME_FOLDER,
    MIME_SHORTCUT,
    MIME_SLIDE,
    NAVIGATOR_SUPPORTED_MIME_TYPES,
    BoundedFolderMembers,
    DriveNode,
    DrivePermission,
    DrivePermissionDetail,
    DrivePermissionsPage,
    DriveSummary,
    DriveTree,
    FilePermissionSnapshot,
    SearchResult,
    ShortcutProbe,
    SnippetResult,
)
from app.drive.snippets import escape_query_value, extract_snippet, has_snippet_terms
from app.identifiers import DriveId, FileId, GoogleUserId
from app.models.user import User
from app.sidebar.singleflight import Singleflight


if TYPE_CHECKING:
    from googleapiclient._apis.drive.v3.resources import DriveResource

_DRIVE_HTTP_TIMEOUT = timedelta(seconds=10)

_PROBE_BATCH_SIZE = 100
_INVENTORY_VALIDATION_BATCH_SIZE = 64

_SEARCH_PAGE_SIZE = 50

_MAX_DRIVE_FILE_PAGES = 3

_MAX_PERSONAL_ANCESTRY_NODES = 64
_MAX_PERSONAL_ANCESTRY_MEMO_NODES = 256

_NAVIGATOR_FOLDER_FIELDS = (
    "nextPageToken,files(id,name,mimeType,parents,modifiedTime,description,"
    "shortcutDetails(targetId,targetMimeType))"
)

_TEMPLATE_FOLDER_FIELDS = "nextPageToken,files(id,name,mimeType,modifiedTime,description)"
_TEMPLATE_MIME_TYPES = (MIME_FOLDER, MIME_DOC, MIME_SLIDE)
_NAMED_FOLDER_FIELDS = "nextPageToken,files(id,name,mimeType)"

_DRIVE_TREE_FIELDS = (
    "nextPageToken,files(id,name,mimeType,parents,modifiedTime,driveId,"
    "shortcutDetails(targetId,targetMimeType))"
)

_QA_CORPUS_FOLDER_FIELDS = (
    "nextPageToken,incompleteSearch,files(id,name,mimeType,parents,driveId,"
    "modifiedTime,version,shortcutDetails(targetId,targetMimeType))"
)

_PERMISSION_PAGE_FIELDS = (
    "nextPageToken,permissions(id,type,role,displayName,emailAddress,domain,"
    "allowFileDiscovery,expirationTime,deleted,pendingOwner,"
    "permissionDetails(inherited,permissionType,role))"
)

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

_DRIVE_LIST_FLIGHTS = Singleflight[list[DriveSummary]]()


class _DriveList(RootModel[list[DriveSummary]]):
    """Validate cached drive summaries before returning them to callers."""


class _InventoryFiles(RootModel[list[GoogleFile]]):
    """Validate bounded buffers of inventory metadata without per-row dispatch."""


class _CachedShortcutProbe(RootModel[ShortcutProbe | None]):
    """Validate a cached shortcut probe, including an explicit cached miss."""


GoogleRefreshHandler = Callable[..., GoogleRefreshResult]


class _DriveFilesResult(NamedTuple):
    files: list[DriveNode]
    complete: bool


class BoundedSearchResult(NamedTuple):
    files: list[SearchResult]
    truncated: bool


class _ShortcutMetadata(NamedTuple):
    drive_names: dict[DriveId, str]
    probes: dict[FileId, ShortcutProbe | None]


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
        return _shared_member_drives(files, drives)

    @abstractmethod
    async def walk_drive_folders(self, drive_id: DriveId) -> DriveTree: ...

    @abstractmethod
    async def list_folder_children(self, folder_id: FileId) -> list[DriveNode]: ...

    async def list_template_children(self, folder_id: FileId) -> list[DriveNode]:
        return await self.list_folder_children(folder_id)

    async def list_named_root_folders(self, drive_id: DriveId, *, name: str) -> list[DriveNode]:
        nodes = await self.list_template_children(FileId(drive_id))
        return _named_folders(nodes, name=name)

    async def list_folder_children_bounded(
        self,
        folder_id: FileId,
        *,
        max_pages: int,
        max_children: int,
    ) -> list[DriveNode]:
        raise NotImplementedError

    async def list_folder_members_bounded(
        self,
        folder_id: FileId,
        *,
        drive_id: DriveId,
        max_pages: int,
        max_children: int,
        max_metadata_bytes: int = 4 * 1024 * 1024,
    ) -> BoundedFolderMembers:
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

    async def read_file_permissions_bounded(
        self,
        file_id: FileId,
        *,
        max_pages: int,
        max_permissions: int,
    ) -> FilePermissionSnapshot:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class _DriveClientBinding:
    user_identity: int
    google_subject: GoogleUserId
    revision: int
    credential_generation: int
    ciphertext: str | None
    expires_at: datetime | None
    handler_identity: int
    factory_identity: int

    def matches(
        self,
        user: User,
        refresh_handler: GoogleRefreshHandler | None,
        client_factory: Callable[[User, GoogleRefreshHandler | None], DriveResource] | None,
    ) -> bool:
        return (
            self.user_identity == id(user)
            and self.google_subject == user.google_id
            and self.revision == user.token_version
            and self.credential_generation == user.google_token_generation
            and self.ciphertext == user.encrypted_access_token
            and self.expires_at == user.google_access_token_expires_at
            and self.handler_identity == id(refresh_handler)
            and self.factory_identity == id(client_factory)
        )


def _drive_client_binding(
    user: User,
    refresh_handler: GoogleRefreshHandler | None,
    client_factory: Callable[[User, GoogleRefreshHandler | None], DriveResource] | None,
) -> _DriveClientBinding:
    return _DriveClientBinding(
        id(user),
        user.google_id,
        user.token_version,
        user.google_token_generation,
        user.encrypted_access_token,
        user.google_access_token_expires_at,
        id(refresh_handler),
        id(client_factory),
    )


class _DriveClientSharing:
    def __init__(
        self,
        user: User,
        clients: dict[int, DriveResource],
        refresh_handler: GoogleRefreshHandler | None,
        client_factory: Callable[[User, GoogleRefreshHandler | None], DriveResource] | None,
    ) -> None:
        self._user = user
        self._clients = clients
        self._refresh_handler = refresh_handler
        self._client_factory = client_factory
        self._bindings: dict[int, _DriveClientBinding] = {}

    def client(self) -> DriveResource:
        thread_id = threading.get_ident()
        if (client := self._clients.get(thread_id)) is not None:
            return client
        binding = _drive_client_binding(self._user, self._refresh_handler, self._client_factory)
        client = reuse_drive_client(
            self._user,
            self._clients,
            refresh_handler=self._refresh_handler,
            client_factory=self._client_factory,
        )
        self._bindings[thread_id] = binding
        return client

    def for_metadata(
        self,
        user: User,
        refresh_handler: GoogleRefreshHandler | None,
        client_factory: Callable[[User, GoogleRefreshHandler | None], DriveResource] | None,
    ) -> DriveResource | None:
        if (
            user is not self._user
            or refresh_handler is not self._refresh_handler
            or client_factory is not self._client_factory
        ):
            return None
        thread_id = threading.get_ident()
        client = self._clients.get(thread_id)
        if client is None:
            return self.client()
        binding = self._bindings.get(thread_id)
        if binding is None or not binding.matches(user, refresh_handler, client_factory):
            return None
        return client


class GoogleDriveService(DriveService):
    def __init__(
        self,
        user: User,
        cache_service: CacheService | None = None,
        *,
        refresh_handler: GoogleRefreshHandler | None = None,
        client_factory: Callable[[User, GoogleRefreshHandler | None], DriveResource] | None = None,
    ) -> None:
        self._user = user
        self._cache = cache_service
        self._refresh_handler = refresh_handler
        self._client_factory = client_factory
        self._clients: dict[int, DriveResource] = {}
        self.metadata_clients = _DriveClientSharing(
            user, self._clients, refresh_handler, client_factory
        )
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
            task = asyncio.create_task(
                _DRIVE_LIST_FLIGHTS.do(str(self._user.id), self._load_drives)
            )
            self._drives_task = task
        try:
            drives = await asyncio.shield(task)
        except BaseException:
            if task.done() and self._drives_task is task:
                self._drives_task = None
            raise
        self._my_drive_id = next((drive.id for drive in drives if drive.is_personal), None)
        return drives

    async def _load_drives(self) -> list[DriveSummary]:
        # This shielded flight can outlive the request that supplied its
        # credentials. Provider refresh belongs to the shared work task.
        with provider_refresh_scope():
            cache_key = str(self._user.id)
            if self._cache is not None:
                cached = await self._cache.get(CacheNamespace.DRIVE_LIST, cache_key)
                if cached is not None:
                    try:
                        drives = _DriveList.model_validate(cached).root
                    except ValidationError:
                        await self._cache.delete(CacheNamespace.DRIVE_LIST, cache_key)
                    else:
                        return drives
            drives = await asyncio.to_thread(self._list_drives_sync)
            if self._cache is not None:
                await self._cache.put(
                    CacheNamespace.DRIVE_LIST,
                    cache_key,
                    _DriveList(drives).model_dump(mode="json"),
                    ttl=_DRIVE_LIST_CACHE_TTL,
                )
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
        resolved = _shared_member_drives(files, drives)
        personal = next((drive for drive in drives if drive.is_personal), None)
        if personal is None:
            return resolved
        root_id = FileId(personal.id)
        parents = parent_ids or {}
        personal_parents: dict[FileId, FileId] = {}
        for file_id, drive_id in files.items():
            if drive_id is not None:
                continue
            if file_id == root_id:
                resolved[file_id] = personal.id
                continue
            if (parent_id := parents.get(file_id)) is not None:
                personal_parents[file_id] = parent_id
        member_parents = await self._member_personal_parents(
            list(dict.fromkeys(personal_parents.values())), root_id=root_id
        )
        resolved.update(
            (file_id, personal.id)
            for file_id, parent_id in personal_parents.items()
            if parent_id in member_parents
        )
        return resolved

    async def _member_personal_parents(
        self, parent_ids: list[FileId], *, root_id: FileId
    ) -> set[FileId]:
        strict = self._bounded_drives.get() is not None
        reads = _PersonalAncestryReads(strict=strict) if len(parent_ids) > 1 else None
        if strict:
            members: set[FileId] = set()
            for parent_id in parent_ids:
                if await asyncio.to_thread(
                    self._is_personal_descendant_sync,
                    file_id=parent_id,
                    root_id=root_id,
                    parent_id=parent_id,
                    strict=True,
                    reads=reads,
                ):
                    members.add(parent_id)
            return members
        descendants = await asyncio.gather(
            *(
                asyncio.to_thread(
                    self._is_personal_descendant_sync,
                    file_id=parent_id,
                    root_id=root_id,
                    parent_id=parent_id,
                    reads=reads,
                )
                for parent_id in parent_ids
            )
        )
        return {
            parent_id
            for parent_id, is_descendant in zip(parent_ids, descendants, strict=True)
            if is_descendant
        }

    @override
    async def walk_drive_folders(self, drive_id: DriveId) -> DriveTree:
        is_personal = await self._is_personal_drive(drive_id)
        tree, (files, files_complete) = await asyncio.gather(
            asyncio.to_thread(self._walk_folders_sync, drive_id, is_personal=is_personal),
            asyncio.to_thread(self._list_drive_files_sync, drive_id, is_personal=is_personal),
        )
        tree.files_complete = files_complete
        preload_files = files if files_complete else []
        metadata = await self._shortcut_metadata(
            node for group in (tree.folders, preload_files) for node in group
        )
        tree.folders = self._apply_shortcuts(tree.folders, metadata, _node_shortcut_update)
        tree.files = self._apply_shortcuts(preload_files, metadata, _node_shortcut_update)
        return tree

    @override
    async def list_folder_children(self, folder_id: FileId) -> list[DriveNode]:
        nodes = await asyncio.to_thread(self._list_children_sync, folder_id)
        return await self._resolve_shortcut_nodes(nodes)

    @override
    async def list_template_children(self, folder_id: FileId) -> list[DriveNode]:
        parsed = await asyncio.to_thread(
            self._list_members_sync,
            folder_id,
            max_pages=None,
            max_children=None,
            fail_closed=False,
            include_unsupported=False,
            navigator_mime_types=_TEMPLATE_MIME_TYPES,
            navigator_fields=_TEMPLATE_FOLDER_FIELDS,
        )
        return parsed.members

    @override
    async def list_named_root_folders(self, drive_id: DriveId, *, name: str) -> list[DriveNode]:
        parsed = await asyncio.to_thread(
            self._list_members_sync,
            FileId(drive_id),
            max_pages=None,
            max_children=None,
            fail_closed=False,
            include_unsupported=False,
            navigator_mime_types=(MIME_FOLDER,),
            navigator_fields=_NAMED_FOLDER_FIELDS,
            name=name,
        )
        return _named_folders(parsed.members, name=name)

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
    async def list_folder_members_bounded(
        self,
        folder_id: FileId,
        *,
        drive_id: DriveId,
        max_pages: int,
        max_children: int,
        max_metadata_bytes: int = 4 * 1024 * 1024,
    ) -> BoundedFolderMembers:
        if max_pages < 1 or max_children < 1 or max_metadata_bytes < 1:
            raise ValueError
        is_personal = await self._is_personal_drive(drive_id)
        return await asyncio.to_thread(
            self._list_folder_members_sync,
            folder_id,
            drive_id=drive_id,
            is_personal=is_personal,
            max_pages=max_pages,
            max_children=max_children,
            max_metadata_bytes=max_metadata_bytes,
        )

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
        shared_hits = (
            _known_drive_hits(hits, drives, limit_per_drive=limit_per_drive)
            if hits is not None
            else None
        )
        if shared_hits is None:
            merged = await self._search_per_drive(drives, stripped, limit_per_drive)
        else:
            my_drive = next((d for d in drives if d.is_personal), None)
            merged = shared_hits
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
            hit = _search_result_from_api(record, drive.id, drive_name=drive.name)
            if drive.is_personal and hit.drive_id is not None:
                continue
            hits.append(hit)
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

    @override
    async def read_file_permissions_bounded(
        self,
        file_id: FileId,
        *,
        max_pages: int,
        max_permissions: int,
    ) -> FilePermissionSnapshot:
        if max_pages <= 0 or max_permissions <= 0:
            raise ValueError
        return await asyncio.to_thread(
            self._read_file_permissions_bounded_sync,
            file_id,
            max_pages,
            max_permissions,
        )

    async def _resolve_shortcut_nodes(self, nodes: list[DriveNode]) -> list[DriveNode]:
        metadata = await self._shortcut_metadata(nodes)
        return self._apply_shortcuts(nodes, metadata, _node_shortcut_update)

    async def _resolve_shortcut_search_results(
        self, results: list[SearchResult]
    ) -> list[SearchResult]:
        metadata = await self._shortcut_metadata(results)
        return self._apply_shortcuts(results, metadata, _search_shortcut_update)

    async def _shortcut_metadata(
        self, items: Iterable[DriveNode | SearchResult]
    ) -> _ShortcutMetadata | None:
        target_ids: dict[FileId, None] = {}
        has_shortcuts = False
        for item in items:
            if item.mime_type == MIME_SHORTCUT:
                has_shortcuts = True
                if item.shortcut_target_id is not None:
                    target_ids[item.shortcut_target_id] = None
        if not has_shortcuts:
            return None
        unique_ids = list(target_ids)
        drive_names, probes = await asyncio.gather(
            self._drive_names(),
            self._probe_targets(unique_ids),
        )
        return _ShortcutMetadata(
            drive_names,
            dict(zip(unique_ids, probes, strict=True)),
        )

    @staticmethod
    def _apply_shortcuts[T: (DriveNode, SearchResult)](
        items: list[T],
        metadata: _ShortcutMetadata | None,
        accessible_update: Callable[
            [DriveId | None, dict[DriveId, str]], dict[str, DriveId | str | None]
        ],
    ) -> list[T]:
        if metadata is None:
            return items
        out: list[T] = []
        for item in items:
            if item.mime_type != MIME_SHORTCUT:
                out.append(item)
                continue
            probe = (
                metadata.probes.get(item.shortcut_target_id)
                if item.shortcut_target_id is not None
                else None
            )
            if probe is None:
                continue
            if not probe.accessible:
                out.append(item.model_copy(update={"shortcut_inaccessible": True}))
                continue
            out.append(
                item.model_copy(
                    update=accessible_update(probe.target_drive_id, metadata.drive_names)
                )
            )
        return out

    async def _drive_names(self) -> dict[DriveId, str]:
        return {d.id: d.name for d in await self.list_drives()}

    async def _is_personal_drive(self, drive_id: DriveId) -> bool:
        if self._my_drive_id is None:
            await self.list_drives()
        return drive_id == self._my_drive_id

    async def _probe_targets(
        self, target_ids: Sequence[FileId | None]
    ) -> list[ShortcutProbe | None]:
        unique_ids = sorted({tid for tid in target_ids if tid})
        if not unique_ids:
            return [None] * len(target_ids)
        probes = await self._cached_target_probes(unique_ids)
        missing = [tid for tid in unique_ids if tid not in probes]
        if missing:
            fetched = await asyncio.to_thread(self._probe_targets_sync, missing)
            if self._cache is not None:
                await self._cache.put_many(
                    CacheNamespace.SHORTCUT_PROBE,
                    {
                        self._probe_cache_key(target_id): (
                            _CachedShortcutProbe(probe).model_dump(mode="json")
                        )
                        for target_id, probe in fetched.items()
                    },
                    ttl=_PROBE_CACHE_TTL,
                )
            probes.update(fetched)
        return [probes.get(tid) if tid else None for tid in target_ids]

    async def _cached_target_probes(
        self, target_ids: list[FileId]
    ) -> dict[FileId, ShortcutProbe | None]:
        if self._cache is None:
            return {}
        keyed_targets = {self._probe_cache_key(target_id): target_id for target_id in target_ids}
        cached = await self._cache.get_many(CacheNamespace.SHORTCUT_PROBE, list(keyed_targets))
        probes: dict[FileId, ShortcutProbe | None] = {}
        for cache_key, target_id in keyed_targets.items():
            if cache_key not in cached:
                continue
            try:
                probes[target_id] = _CachedShortcutProbe.model_validate(cached[cache_key]).root
            except ValidationError:
                await self._cache.delete(CacheNamespace.SHORTCUT_PROBE, cache_key)
        return probes

    def _probe_cache_key(self, target_id: FileId) -> str:
        return f"{self._user.id}:{target_id}"

    def _probe_targets_sync(self, target_ids: list[FileId]) -> dict[FileId, ShortcutProbe | None]:
        client = self._client()
        files = client.files()
        probes: dict[FileId, ShortcutProbe | None] = {}

        def _collect(request_id: str, response: Any, exception: BaseException | None) -> None:
            target_id = FileId(request_id)
            probes[target_id] = self._classify_probe(response, exception)

        for chunk in batched(target_ids, _PROBE_BATCH_SIZE, strict=False):
            batch = client.new_batch_http_request(callback=_collect)
            for tid in chunk:
                batch.add(
                    files.get(
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
        reads: _PersonalAncestryReads | None = None,
    ) -> bool:
        if file_id == root_id:
            return True
        if parent_id is None:
            return False
        if parent_id == root_id:
            return True
        client = self._client()
        pending = [parent_id]
        visited: set[FileId] = set()
        while pending and len(visited) < _MAX_PERSONAL_ANCESTRY_NODES:
            current = pending.pop()
            if current in visited:
                continue
            if current == root_id:
                return True
            visited.add(current)
            location = (
                _read_personal_ancestor(client, current, strict=strict)
                if reads is None
                else reads.read(client, current)
            )
            if location is None:
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
            params = _drive_files_params(
                drive_id,
                is_personal=is_personal,
                query=query,
                fields=_DRIVE_TREE_FIELDS,
                page_token=page_token,
            )
            try:
                resp = execute_with_retry(client.files().list(**params))
            except HttpError as exc:
                _log_provider_failure("walk_drive_folders", exc)
                return out
            for file in _inventory_files(resp.get("files", [])):
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
        return self._list_members_sync(
            folder_id,
            max_pages=max_pages,
            max_children=max_children,
            fail_closed=fail_closed,
            include_unsupported=False,
        ).members

    def _list_folder_members_sync(
        self,
        folder_id: FileId,
        *,
        drive_id: DriveId,
        is_personal: bool,
        max_pages: int,
        max_children: int,
        max_metadata_bytes: int,
    ) -> BoundedFolderMembers:
        return self._list_members_sync(
            folder_id,
            drive_id=drive_id,
            is_personal=is_personal,
            max_pages=max_pages,
            max_children=max_children,
            fail_closed=True,
            include_unsupported=True,
            max_metadata_bytes=max_metadata_bytes,
        )

    def _list_members_sync(
        self,
        folder_id: FileId,
        *,
        drive_id: DriveId | None = None,
        is_personal: bool | None = None,
        max_pages: int | None,
        max_children: int | None,
        fail_closed: bool,
        include_unsupported: bool,
        max_metadata_bytes: int | None = None,
        navigator_mime_types: tuple[str, ...] = (*NAVIGATOR_SUPPORTED_MIME_TYPES, MIME_SHORTCUT),
        navigator_fields: str = _NAVIGATOR_FOLDER_FIELDS,
        name: str | None = None,
    ) -> BoundedFolderMembers:
        client = self._client()
        out: list[DriveNode] = []
        raw_children = 0
        page_token: str | None = None
        pages = 0
        metadata_bytes = 0
        query = f"'{folder_id}' in parents and trashed=false"
        fields = _QA_CORPUS_FOLDER_FIELDS if include_unsupported else navigator_fields
        if not include_unsupported:
            query += f" and {_build_mime_clause(navigator_mime_types)}"
        if name is not None:
            query += f" and name='{escape_query_value(name)}'"
        while True:
            if max_pages is not None and pages >= max_pages:
                raise DriveTraversalIncompleteError
            pages += 1
            params = _drive_files_params(
                drive_id,
                is_personal=is_personal,
                query=query,
                fields=fields,
                page_token=page_token,
            )
            try:
                resp = execute_with_retry(client.files().list(**params))
            except HttpError as exc:
                _log_provider_failure("list_folder_children", exc)
                if fail_closed:
                    raise DriveTraversalIncompleteError from exc
                return BoundedFolderMembers(members=out, page_count=pages)
            raw_files = resp.get("files", [])
            if include_unsupported and resp.get("incompleteSearch", False):
                raise DriveTraversalIncompleteError
            raw_children += len(raw_files)
            if max_children is not None and raw_children > max_children:
                raise DriveTraversalIncompleteError
            parsed = _parse_folder_member_page(
                raw_files,
                folder_id=folder_id,
                drive_id=drive_id,
                is_personal=is_personal,
                include_unsupported=include_unsupported,
                metadata_bytes=metadata_bytes,
                max_metadata_bytes=max_metadata_bytes,
            )
            out.extend(parsed.members)
            metadata_bytes = parsed.metadata_bytes
            page_token = resp.get("nextPageToken")
            if not page_token:
                return BoundedFolderMembers(
                    members=out,
                    page_count=pages,
                    metadata_bytes=metadata_bytes,
                )

    def _list_drive_files_sync(
        self, drive_id: DriveId, *, is_personal: bool = False
    ) -> _DriveFilesResult:
        client = self._client()
        out: list[DriveNode] = []
        page_token: str | None = None
        pages = 0
        query = f"trashed=false and mimeType!='{MIME_FOLDER}' and {_build_mime_clause()}"
        if is_personal:
            query += _OWNED_BY_ME_CLAUSE
        while True:
            params = _drive_files_params(
                drive_id,
                is_personal=is_personal,
                query=query,
                fields=_DRIVE_TREE_FIELDS,
                page_token=page_token,
            )
            try:
                resp = execute_with_retry(client.files().list(**params))
            except HttpError as exc:
                _log_provider_failure("list_drive_files", exc)
                return _DriveFilesResult(files=out, complete=False)
            for file in _inventory_files(resp.get("files", [])):
                node = _preload_file_node(file, drive_id, is_personal=is_personal)
                if node is not None:
                    out.append(node)
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
                "incompleteSearch,nextPageToken,files(id,name,mimeType,parents,modifiedTime,driveId,"
                "shortcutDetails(targetId,targetMimeType))"
            ),
        }
        try:
            resp = execute_with_retry(client.files().list(**params))
        except HttpError as exc:
            _log_provider_failure("search_all_drives", exc)
            return None
        if resp.get("incompleteSearch") or resp.get("nextPageToken"):
            return None
        return [_search_result_from_api(raw) for raw in resp.get("files", [])]

    def _fetch_snippet_sync(
        self, file_id: FileId, query: str, mime_type: str | None
    ) -> SnippetResult:
        if mime_type is None:
            try:
                meta = execute_with_retry(
                    self._client()
                    .files()
                    .get(fileId=file_id, fields="id,mimeType,size", supportsAllDrives=True)
                )
            except HttpError as exc:
                _log_provider_failure("fetch_snippet_metadata", exc)
                return SnippetResult(file_id=file_id, mime_type="", snippet=None, match_term=None)
            mime_type = GoogleFileMetadata.model_validate(meta).mime_type

        if mime_type not in _TEXT_EXPORTABLE_MIME_TYPES or not has_snippet_terms(query):
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
        return _bounded_reorganization_text(text)

    def _list_drive_permissions_sync(
        self,
        drive_id: DriveId,
        page_token: str | None,
    ) -> DrivePermissionsPage:
        params: dict[str, Any] = {
            "fields": _PERMISSION_PAGE_FIELDS,
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
            permissions.append(_drive_permission(permission))
        return DrivePermissionsPage(
            access_model="shared_drive_membership",
            drive_id=drive_id,
            next_page_token=raw.get("nextPageToken"),
            permissions=permissions,
        )

    def _read_file_permissions_bounded_sync(
        self,
        file_id: FileId,
        max_pages: int,
        max_permissions: int,
    ) -> FilePermissionSnapshot:
        client = self._client()
        metadata = _read_file_permission_metadata(client, file_id)

        permissions: list[DrivePermission] = []
        permission_ids: set[str] = set()
        page_token: str | None = None
        seen_page_tokens: set[str] = set()
        for _page in range(max_pages):
            raw_page = _read_permission_page(
                client,
                file_id=file_id,
                page_token=page_token,
                page_size=min(100, max_permissions - len(permissions)),
            )
            for raw_permission in raw_page["permissions"]:
                permission = _strict_permission(raw_permission, permission_ids)
                permission_ids.add(permission.id)
                permissions.append(_drive_permission(permission))
                if len(permissions) > max_permissions:
                    raise DriveTraversalIncompleteError
            next_page_token = raw_page.get("nextPageToken")
            if next_page_token is None:
                break
            if len(permissions) >= max_permissions:
                raise DriveTraversalIncompleteError
            if (
                not isinstance(next_page_token, str)
                or not next_page_token
                or next_page_token in seen_page_tokens
            ):
                raise DriveTraversalIncompleteError
            seen_page_tokens.add(next_page_token)
            page_token = next_page_token
        else:
            raise DriveTraversalIncompleteError

        if _read_file_permission_metadata(client, file_id) != metadata:
            raise DriveTraversalIncompleteError

        return FilePermissionSnapshot(
            file_id=metadata.id,
            mime_type=metadata.mime_type,
            drive_id=metadata.drive_id,
            parent_ids=tuple(metadata.parents),
            version=metadata.version,
            owned_by_me=metadata.owned_by_me,
            shared=metadata.shared,
            can_add_children=metadata.capabilities.can_add_children,
            owner_emails=tuple(owner.email_address or "" for owner in metadata.owners),
            permissions=tuple(sorted(permissions, key=_drive_permission_id)),
            app_properties=metadata.app_properties,
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
        return self.metadata_clients.client()

    @staticmethod
    def build_drive_client(
        user: User,
        refresh_handler: GoogleRefreshHandler | None = None,
    ) -> DriveResource:
        authed_http = google_auth_httplib2.AuthorizedHttp(
            GoogleDriveService.build_credentials(user, refresh_handler),
            http=httplib2.Http(timeout=_DRIVE_HTTP_TIMEOUT.total_seconds()),
        )
        # The installed SDK implements this builder; its external stubs omit the attribute.
        built: DriveResource = discovery.build_from_document(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]  # pyright: ignore[reportAttributeAccessIssue]
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
        return Credentials(  # type: ignore[no-untyped-call]  # google-auth exposes this credential constructor without annotations.
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
    client_factory: Callable[[User, GoogleRefreshHandler | None], DriveResource] | None = None,
) -> DriveResource:
    thread_id = threading.get_ident()
    client = clients.get(thread_id)
    if client is None:
        client = (
            client_factory(user, refresh_handler)
            if client_factory is not None
            else (
                GoogleDriveService.build_drive_client(user, refresh_handler)
                if refresh_handler is not None
                else GoogleDriveService.build_drive_client(user)
            )
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


def _file_permission_error(exc: HttpError) -> DrivePermissionsError:
    status = getattr(exc.resp, "status", None)
    if status == 403:
        return DrivePermissionsForbiddenError()
    if status == 404:
        return DrivePermissionsNotFoundError()
    return DrivePermissionsUpstreamError()


class _PersonalAncestryReads:
    def __init__(self, *, strict: bool) -> None:
        self._strict = strict
        self._lock = threading.Lock()
        self._locations: dict[FileId, Future[GoogleFileLocation | None]] = {}

    def read(self, client: DriveResource, file_id: FileId) -> GoogleFileLocation | None:
        leader = False
        with self._lock:
            pending = self._locations.get(file_id)
            if pending is None and len(self._locations) < _MAX_PERSONAL_ANCESTRY_MEMO_NODES:
                pending = Future[GoogleFileLocation | None]()
                self._locations[file_id] = pending
                leader = True
        if pending is None:
            return _read_personal_ancestor(client, file_id, strict=self._strict)
        if not leader:
            return pending.result()
        try:
            location = _read_personal_ancestor(client, file_id, strict=self._strict)
        except BaseException as exc:
            pending.set_exception(exc)
            raise
        pending.set_result(location)
        return location


def _read_personal_ancestor(
    client: DriveResource, file_id: FileId, *, strict: bool
) -> GoogleFileLocation | None:
    try:
        raw = execute_with_retry(
            client.files().get(fileId=file_id, fields="id,driveId,parents", supportsAllDrives=True)
        )
        location = GoogleFileLocation.model_validate(raw)
    except HttpError as exc:
        if strict and provider_http_status(exc) not in {403, 404}:
            raise
        _log_provider_failure("verify_my_drive_ancestry", exc)
        return None
    except ValidationError:
        if strict:
            raise
        _log_invalid_provider_response("verify_my_drive_ancestry")
        return None
    if strict and location.id != file_id:
        message = "Invalid personal ancestry response"
        raise ValueError(message)
    return location if location.drive_id is None else None


def _read_file_permission_metadata(
    client: DriveResource,
    file_id: FileId,
) -> GoogleFilePermissionMetadata:
    try:
        raw_metadata = execute_with_retry(
            client.files().get(
                fileId=file_id,
                supportsAllDrives=True,
                fields=(
                    "id,mimeType,driveId,parents,version,ownedByMe,shared,"
                    "capabilities(canAddChildren),owners(emailAddress),appProperties"
                ),
            )
        )
        return GoogleFilePermissionMetadata.model_validate(raw_metadata)
    except HttpError as exc:
        raise _file_permission_error(exc) from None
    except ValidationError as exc:
        raise DrivePermissionsUpstreamError from exc


def _drive_permission_id(permission: DrivePermission) -> str:
    return permission.id


def _read_permission_page(
    client: DriveResource, *, file_id: FileId, page_token: str | None, page_size: int
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "fileId": file_id,
        "supportsAllDrives": True,
        "useDomainAdminAccess": False,
        "pageSize": page_size,
        "fields": _PERMISSION_PAGE_FIELDS,
    }
    if page_token is not None:
        params["pageToken"] = page_token
    try:
        raw_page = execute_with_retry(client.permissions().list(**params))
    except HttpError as exc:
        raise _file_permission_error(exc) from None
    if not isinstance(raw_page, dict) or not isinstance(raw_page.get("permissions"), list):
        raise DrivePermissionsUpstreamError
    return raw_page


def _strict_permission(raw: Any, existing_ids: set[str]) -> GooglePermission:
    try:
        permission = GooglePermission.model_validate(raw)
    except ValidationError as exc:
        raise DrivePermissionsUpstreamError from exc
    # Empty permissionDetails cannot prove effective owner-only access, even
    # when the top-level role looks safe. Strict readback also rejects repeats.
    if not permission.id or not permission.permission_details or permission.id in existing_ids:
        raise DrivePermissionsUpstreamError
    return permission


def _drive_permission(permission: GooglePermission) -> DrivePermission:
    return DrivePermission(
        id=permission.id,
        principal_type=permission.type,
        role=permission.role,
        display_name=permission.display_name,
        email_address=permission.email_address,
        domain=permission.domain,
        allow_file_discovery=permission.allow_file_discovery,
        expiration_time=permission.expiration_time,
        deleted=permission.deleted,
        pending_owner=permission.pending_owner,
        inherited=any(detail.inherited for detail in permission.permission_details),
        permission_details=tuple(
            DrivePermissionDetail(
                permission_type=detail.permission_type, role=detail.role, inherited=detail.inherited
            )
            for detail in permission.permission_details
        ),
    )


def _log_invalid_provider_response(operation: str) -> None:
    logger.warning("Google Drive response was invalid operation={operation}", operation=operation)


@cache
def drive_discovery_document() -> dict[str, Any]:
    content = get_static_doc("drive", "v3")  # type: ignore[no-untyped-call]  # google-api-python-client bundles this untyped discovery-document loader.
    if content is None:
        msg = "Bundled Drive v3 discovery document not found"
        raise RuntimeError(msg)
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        msg = "Bundled Drive v3 discovery document is not an object"
        raise TypeError(msg)
    return parsed


@cache
def _build_mime_clause(
    mime_types: tuple[str, ...] = (*NAVIGATOR_SUPPORTED_MIME_TYPES, MIME_SHORTCUT),
) -> str:
    quoted = " or ".join(f"mimeType='{m}'" for m in mime_types)
    return f"({quoted})"


def _drive_files_params(
    drive_id: DriveId | None,
    *,
    is_personal: bool | None,
    query: str,
    fields: str,
    page_token: str | None,
) -> dict[str, Any]:
    if is_personal:
        corpora = "user"
    elif drive_id:
        corpora = "drive"
    else:
        corpora = "allDrives"
    params: dict[str, Any] = {
        "q": query,
        "corpora": corpora,
        "includeItemsFromAllDrives": True,
        "supportsAllDrives": True,
        "pageSize": 1000,
        "fields": fields,
    }
    if drive_id is not None and not is_personal:
        params["driveId"] = drive_id
    if page_token:
        params["pageToken"] = page_token
    return params


def _preload_file_node(
    file: GoogleFile, drive_id: DriveId, *, is_personal: bool
) -> DriveNode | None:
    if is_personal and file.drive_id is not None:
        return None
    if file.mime_type == MIME_FOLDER:
        return None
    if file.mime_type == MIME_SHORTCUT:
        details = file.shortcut_details
        target_mime = details.target_mime_type if details else None
        if target_mime == MIME_FOLDER or target_mime not in NAVIGATOR_SUPPORTED_MIME_TYPES:
            return None
    return _node_from_api(file, drive_id)


def _bounded_reorganization_text(text: str) -> str | None:
    limit = _MAX_REORGANIZATION_TEXT_CHARS
    window = min(len(text), limit * 2)
    prefix = " ".join(text[:window].split())
    if not prefix:
        text = text.lstrip()
        window = min(len(text), limit * 2)
        prefix = " ".join(text[:window].split())
    while len(prefix) <= limit:
        if window == len(text):
            return prefix or None
        window = min(len(text), window * 2)
        prefix = " ".join(text[:window].split())
    head_chars = limit * 2 // 3
    tail_chars = limit - head_chars
    tail_window = min(len(text), limit * 2)
    suffix = " ".join(text[-tail_window:].split())
    if len(suffix) < tail_chars:
        text = text.rstrip()
        tail_window = min(len(text), limit * 2)
        suffix = " ".join(text[-tail_window:].split())
    while len(suffix) < tail_chars and tail_window < len(text):
        tail_window = min(len(text), tail_window * 2)
        suffix = " ".join(text[-tail_window:].split())
    return f"{prefix[:head_chars]} … [middle omitted] … {suffix[-tail_chars:]}"


def _parse_folder_member_page(
    raw_files: list[Any],
    *,
    folder_id: FileId,
    drive_id: DriveId | None,
    is_personal: bool | None,
    include_unsupported: bool,
    metadata_bytes: int,
    max_metadata_bytes: int | None,
) -> BoundedFolderMembers:
    members: list[DriveNode] = []
    for raw in raw_files:
        if max_metadata_bytes is not None:
            metadata_bytes += len(
                json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode()
            )
            if metadata_bytes > max_metadata_bytes:
                raise DriveTraversalIncompleteError
        node = _folder_member_node(
            GoogleFile.model_validate(raw),
            folder_id=folder_id,
            drive_id=drive_id,
            is_personal=is_personal,
            include_unsupported=include_unsupported,
        )
        if node is not None:
            members.append(node)
    return BoundedFolderMembers(members=members, page_count=1, metadata_bytes=metadata_bytes)


def _folder_member_node(
    file: GoogleFile,
    *,
    folder_id: FileId,
    drive_id: DriveId | None,
    is_personal: bool | None,
    include_unsupported: bool,
) -> DriveNode | None:
    if include_unsupported:
        expected_provider_drive = None if is_personal else drive_id
        if file.parents != (folder_id,) or file.drive_id != expected_provider_drive:
            raise DriveTraversalIncompleteError
    details = file.shortcut_details
    target_mime = details.target_mime_type if details else None
    if (
        not include_unsupported
        and file.mime_type == MIME_SHORTCUT
        and target_mime not in NAVIGATOR_SUPPORTED_MIME_TYPES
    ):
        return None
    return DriveNode(
        id=file.id,
        name=file.name,
        mime_type=file.mime_type,
        parent_id=folder_id,
        modified_time=file.modified_time,
        version=file.version if include_unsupported else None,
        drive_id=drive_id if include_unsupported else None,
        description=file.description if not include_unsupported else None,
        shortcut_target_id=details.target_id if details else None,
        shortcut_target_mime_type=target_mime,
    )


def _named_folders(nodes: Iterable[DriveNode], *, name: str) -> list[DriveNode]:
    return [node for node in nodes if node.mime_type == MIME_FOLDER and node.name == name]


def _inventory_files(raw_files: Iterable[Any]) -> Iterable[GoogleFile]:
    for chunk in batched(raw_files, _INVENTORY_VALIDATION_BATCH_SIZE, strict=False):
        yield from _InventoryFiles.model_validate(chunk).root


def _shared_member_drives(
    files: Mapping[FileId, DriveId | None], drives: list[DriveSummary]
) -> dict[FileId, DriveId]:
    shared_drive_ids = {drive.id for drive in drives if not drive.is_personal}
    return {
        file_id: drive_id
        for file_id, drive_id in files.items()
        if drive_id is not None and drive_id in shared_drive_ids
    }


def _known_drive_hits(
    hits: list[SearchResult], drives: list[DriveSummary], *, limit_per_drive: int
) -> list[SearchResult] | None:
    names = {drive.id: drive.name for drive in drives if not drive.is_personal}
    counts: dict[DriveId, int] = {}
    results: list[SearchResult] = []
    for hit in hits:
        drive_id = hit.drive_id
        if drive_id is None or drive_id not in names:
            continue
        count = counts.get(drive_id, 0) + 1
        if count > limit_per_drive:
            # Preserve the per-drive query's result selection when its limit matters.
            return None
        counts[drive_id] = count
        results.append(hit)
    # An over-limit workspace discards this entire result and triggers the
    # independent per-drive search. Keep its already-validated hits until that
    # completeness decision, then allocate only publishable named results.
    return [
        hit.model_copy(update={"drive_name": names[hit.drive_id]})
        for hit in results
        if hit.drive_id is not None
    ]


def _node_shortcut_update(
    target_drive_id: DriveId | None, drive_names: dict[DriveId, str]
) -> dict[str, DriveId | str | None]:
    return {
        "shortcut_target_drive_id": target_drive_id,
        "shortcut_target_drive_name": drive_names.get(target_drive_id) if target_drive_id else None,
    }


def _search_shortcut_update(
    target_drive_id: DriveId | None, drive_names: dict[DriveId, str]
) -> dict[str, DriveId | str | None]:
    return {
        "shortcut_target_drive_name": drive_names.get(target_drive_id) if target_drive_id else None
    }


def _search_result_from_api(
    raw: Any, drive_id: DriveId | None = None, *, drive_name: str | None = None
) -> SearchResult:
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
        drive_name=drive_name,
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
