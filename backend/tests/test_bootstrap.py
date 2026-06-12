"""BootstrapService — aggregation of the six first-paint reads."""

from __future__ import annotations

import uuid

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
from app.models.user import User


class _FakeDriveService(DriveService):
    """Fake ``DriveService`` that only answers ``list_drives``."""

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
    """Read-only canned answers; writes are out of bootstrap's scope."""

    async def list_doc_icons(self) -> list[DocIconRead]:
        return [DocIconRead(drive_id="doc-1", icon="📚", cover_url=None)]

    async def get_doc_icon(self, drive_id: str) -> DocIconRead:
        raise NotImplementedError

    async def upsert_doc_icon(self, drive_id: str, payload: DocIconWrite) -> DocIconRead:
        raise NotImplementedError

    async def list_folder_icons(self) -> list[FolderIconRead]:
        return [FolderIconRead(folder_drive_id="folder-1", icon="🚀")]

    async def upsert_folder_icon(
        self, folder_drive_id: str, payload: FolderIconWrite
    ) -> FolderIconRead:
        raise NotImplementedError

    async def list_sort_orders(self) -> list[SortOrderRead]:
        return [SortOrderRead(drive_id="doc-1", sort_order=1.5)]

    async def upsert_sort_orders(self, items: list[SortOrderWrite]) -> None:
        raise NotImplementedError

    async def clear_sort_order(self, drive_id: str) -> None:
        raise NotImplementedError

    async def get_preferences(self) -> PreferencesRead:
        return PreferencesRead(show_non_doc_files=False, hidden_workspace_ids=["hidden-1"])

    async def upsert_preferences(self, payload: PreferencesWrite) -> PreferencesRead:
        raise NotImplementedError


def _user() -> User:
    u = User()
    u.id = uuid.uuid4()
    u.email = "test@sarj.ai"
    u.google_id = "gid"
    u.display_name = "Test User"
    u.token_version = 0
    return u


async def test_load_aggregates_all_six_reads() -> None:
    user = _user()
    svc = AggregatingBootstrapService(
        drive=_FakeDriveService([DriveSummary(id="d1", name="Eng")]),
        extras=_FakeExtrasService(),
        user=user,
    )

    out = await svc.load()

    assert [d.id for d in out.drives] == ["d1"]
    assert out.preferences.show_non_doc_files is False
    assert out.preferences.hidden_workspace_ids == ["hidden-1"]
    assert [i.folder_drive_id for i in out.folder_icons] == ["folder-1"]
    assert [i.drive_id for i in out.doc_icons] == ["doc-1"]
    assert [s.sort_order for s in out.sort_orders] == [1.5]
    assert out.me.id == str(user.id)
    assert out.me.email == "test@sarj.ai"
    assert out.me.display_name == "Test User"


async def test_load_handles_empty_overlays() -> None:
    class _EmptyExtras(_FakeExtrasService):
        async def list_doc_icons(self) -> list[DocIconRead]:
            return []

        async def list_folder_icons(self) -> list[FolderIconRead]:
            return []

        async def list_sort_orders(self) -> list[SortOrderRead]:
            return []

    out = await AggregatingBootstrapService(
        drive=_FakeDriveService([]),
        extras=_EmptyExtras(),
        user=_user(),
    ).load()

    assert out.drives == []
    assert out.doc_icons == []
    assert out.folder_icons == []
    assert out.sort_orders == []
