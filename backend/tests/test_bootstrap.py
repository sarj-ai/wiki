from __future__ import annotations

from typing import cast
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.bootstrap.service import AggregatingBootstrapService
from app.drive.schemas import DriveNode, DriveSummary, DriveTree, SearchResult, SnippetResult
from app.drive.service import DriveService
from app.extras.schemas import (
    DocIconRead,
    DocIconWrite,
    FolderIconRead,
    FolderIconWrite,
    PreferencesRead,
    PreferencesWrite,
    SortOrderRead,
    SortOrderWrite,
)
from app.extras.service import ExtrasService
from app.files.schemas import FileResponse
from app.files.service import FilesService
from app.identifiers import DriveId, FileId, GoogleUserId, UserId
from app.models.user import User
from app.sidebar.cache import CachedDriveTree, SidebarCacheService


class _FakeDriveService(DriveService):
    def __init__(self, drives: list[DriveSummary]) -> None:
        self._drives = drives

    async def list_drives(self) -> list[DriveSummary]:
        return list(self._drives)

    async def walk_drive_folders(self, drive_id: str) -> DriveTree:
        raise NotImplementedError

    async def list_folder_children(self, folder_id: str) -> list[DriveNode]:
        raise NotImplementedError

    async def search_drive(
        self, drive_id: str, query: str, *, limit: int = 50
    ) -> list[SearchResult]:
        raise NotImplementedError

    async def search(self, query: str, *, limit_per_drive: int = 20) -> list[SearchResult]:
        raise NotImplementedError

    async def fetch_snippet(
        self, file_id: str, query: str, *, mime_type: str | None = None
    ) -> SnippetResult:
        raise NotImplementedError


class _FakeExtrasService(ExtrasService):
    async def list_doc_icons(self) -> list[DocIconRead]:
        return [
            DocIconRead(drive_id=FileId("doc-1"), icon="📚", cover_url=None),
            DocIconRead(
                drive_id=FileId("private-doc"),
                icon="🔒",
                cover_url="https://example.com/private-cover.png",
            ),
        ]

    async def upsert_doc_icon(self, drive_id: str, payload: DocIconWrite) -> DocIconRead:
        raise NotImplementedError

    async def list_folder_icons(self) -> list[FolderIconRead]:
        return [FolderIconRead(folder_drive_id=FileId("folder-1"), icon="🚀")]

    async def upsert_folder_icon(
        self, folder_drive_id: str, payload: FolderIconWrite
    ) -> FolderIconRead:
        raise NotImplementedError

    async def list_sort_orders(self) -> list[SortOrderRead]:
        return [SortOrderRead(drive_id=DriveId("doc-1"), sort_order=1.5)]

    async def upsert_sort_orders(self, items: list[SortOrderWrite]) -> None:
        raise NotImplementedError

    async def get_preferences(self) -> PreferencesRead:
        return PreferencesRead(show_non_doc_files=False, hidden_workspace_ids=[DriveId("hidden-1")])

    async def upsert_preferences(self, payload: PreferencesWrite) -> PreferencesRead:
        raise NotImplementedError


class _FakeFilesService:
    def __init__(self) -> None:
        self.requested: list[FileId] = []

    async def get_accessible_many(self, file_ids: list[FileId]) -> dict[FileId, FileResponse]:
        self.requested = file_ids
        return {
            FileId("doc-1"): FileResponse(
                id=FileId("doc-1"),
                name="Accessible",
                mime_type="application/vnd.google-apps.document",
                parent_folder_id=None,
                drive_id=DriveId("d1"),
                modified_time=None,
            )
        }


class _FakeCacheService(SidebarCacheService):
    def __init__(self, tree: DriveTree | None = None) -> None:
        self._tree = tree
        self.peeked: list[str] = []

    async def get_drive_tree(self, db: AsyncSession, drive_id: str) -> CachedDriveTree:
        raise NotImplementedError

    async def peek_drive_tree(self, db: AsyncSession, drive_id: str) -> CachedDriveTree | None:
        self.peeked.append(drive_id)
        if self._tree is None:
            return None
        return CachedDriveTree(tree=self._tree, refreshed_at=None)

    async def get_folder_children(self, folder_id: str) -> list[DriveNode]:
        raise NotImplementedError

    async def invalidate_drive(self, db: AsyncSession, drive_id: str) -> None:
        raise NotImplementedError

    async def refresh_drive(self, db: AsyncSession, drive_id: str) -> DriveTree:
        raise NotImplementedError


_NO_DB = cast("AsyncSession", None)


def _user() -> User:
    u = User()
    u.id = UserId(uuid.uuid4())
    u.email = "test@sarj.ai"
    u.google_id = GoogleUserId("gid")
    u.display_name = "Test User"
    u.token_version = 0
    return u


def _tree(drive_id: str) -> DriveTree:
    return DriveTree(drive_id=DriveId(drive_id), folders=[], files=[], files_complete=True)


async def test_load_aggregates_all_six_reads() -> None:
    user = _user()
    cache = _FakeCacheService(_tree("d1"))
    files = _FakeFilesService()
    svc = AggregatingBootstrapService(
        drive=_FakeDriveService([DriveSummary(id=DriveId("d1"), name="Eng")]),
        extras=_FakeExtrasService(),
        files=cast("FilesService", files),
        cache=cache,
        db=_NO_DB,
        user=user,
    )

    out = await svc.load()

    assert [d.id for d in out.drives] == ["d1"]
    assert out.preferences.show_non_doc_files is False
    assert out.preferences.hidden_workspace_ids == ["hidden-1"]
    assert [i.folder_drive_id for i in out.folder_icons] == ["folder-1"]
    assert [i.drive_id for i in out.doc_icons] == ["doc-1"]
    assert files.requested == ["doc-1", "private-doc"]
    assert [s.sort_order for s in out.sort_orders] == [1.5]
    assert out.me.id == str(user.id)
    assert out.me.email == "test@sarj.ai"
    assert out.me.display_name == "Test User"
    assert cache.peeked == ["d1"]
    assert out.first_drive_tree is not None
    assert out.first_drive_tree.drive_id == "d1"


async def test_load_omits_first_tree_on_cache_miss() -> None:
    cache = _FakeCacheService(None)
    out = await AggregatingBootstrapService(
        drive=_FakeDriveService([DriveSummary(id=DriveId("d1"), name="Eng")]),
        extras=_FakeExtrasService(),
        files=cast("FilesService", _FakeFilesService()),
        cache=cache,
        db=_NO_DB,
        user=_user(),
    ).load()

    assert cache.peeked == ["d1"]
    assert out.first_drive_tree is None


async def test_load_handles_empty_overlays() -> None:
    class _EmptyExtras(_FakeExtrasService):
        async def list_doc_icons(self) -> list[DocIconRead]:
            return []

        async def list_folder_icons(self) -> list[FolderIconRead]:
            return []

        async def list_sort_orders(self) -> list[SortOrderRead]:
            return []

    cache = _FakeCacheService(_tree("unused"))
    out = await AggregatingBootstrapService(
        drive=_FakeDriveService([]),
        extras=_EmptyExtras(),
        files=cast("FilesService", _FakeFilesService()),
        cache=cache,
        db=_NO_DB,
        user=_user(),
    ).load()

    assert out.drives == []
    assert out.doc_icons == []
    assert out.folder_icons == []
    assert out.sort_orders == []
    assert cache.peeked == []
    assert out.first_drive_tree is None
