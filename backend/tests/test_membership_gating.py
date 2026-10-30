from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING
import uuid

from httpx import ASGITransport, AsyncClient
import pytest

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.drive.dependencies import drive_service_dep
from app.drive.schemas import DriveSummary, DriveTree
from app.extras.dependencies import extras_service_dep
from app.extras.schemas import DocIconRead
from app.files.dependencies import files_service_dep
from app.files.errors import FileNotInScopeError
from app.files.schemas import FileResponse
from app.identifiers import DriveId, FileId
from app.main import app
from app.models.user import User
from app.sidebar.cache import CachedDriveTree, SidebarCacheService
from app.sidebar.dependencies import sidebar_cache_service_dep
from tests.test_files_service import _FakeDriveService, _user


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.extras.schemas import DocIconWrite


MEMBER_DRIVE = DriveId("member-drive")
OTHER_DRIVE = DriveId("someone-elses-drive")
ACCESSIBLE_DOC = FileId("accessible-doc")
INACCESSIBLE_DOC = FileId("inaccessible-doc")


class _FakeCache(SidebarCacheService):
    full_tree_reads = 0
    metadata_reads = 0

    async def get_drive_tree(self, db: AsyncSession, drive_id: DriveId) -> CachedDriveTree:
        type(self).full_tree_reads += 1
        return CachedDriveTree(
            tree=_tree(drive_id),
            refreshed_at=datetime(2026, 8, 20, tzinfo=UTC),
        )

    async def peek_drive_tree(self, db: AsyncSession, drive_id: DriveId) -> CachedDriveTree | None:
        return None

    async def get_drive_tree_refreshed_at(
        self, db: AsyncSession, drive_id: DriveId
    ) -> datetime | None:
        type(self).metadata_reads += 1
        return datetime(2026, 8, 20, tzinfo=UTC)

    async def get_folder_children(self, folder_id: str) -> list:
        return []

    async def invalidate_drive(self, db: AsyncSession, drive_id: DriveId) -> None:
        return None

    async def refresh_drive(self, db: AsyncSession, drive_id: DriveId) -> DriveTree:
        return _tree(drive_id)


class _FakeExtras:
    async def list_doc_icons(self) -> list[DocIconRead]:
        return [
            DocIconRead(drive_id=ACCESSIBLE_DOC, icon="📄", cover_url=None),
            DocIconRead(
                drive_id=INACCESSIBLE_DOC,
                icon="🔒",
                cover_url="https://example.com/private-cover.png",
            ),
        ]

    async def upsert_doc_icon(self, drive_id: FileId, payload: DocIconWrite) -> DocIconRead:
        return DocIconRead(drive_id=FileId(drive_id), icon="📄", cover_url=None)


class _FakeFiles:
    async def require_access(self, file_id: FileId) -> None:
        await self.get(file_id)

    async def get_accessible_ids(self, file_ids: list[FileId]) -> set[FileId]:
        assert file_ids == [ACCESSIBLE_DOC, INACCESSIBLE_DOC]
        return {ACCESSIBLE_DOC}

    async def get(self, file_id: FileId) -> FileResponse:
        if file_id == ACCESSIBLE_DOC:
            return FileResponse(
                id=FileId(file_id),
                name="Doc",
                mime_type="application/vnd.google-apps.document",
                parent_folder_id=None,
                drive_id=MEMBER_DRIVE,
                modified_time=None,
            )
        msg = "File not available in Sarj Wiki"
        raise FileNotInScopeError(msg)


def _tree(drive_id: DriveId) -> DriveTree:
    return DriveTree(drive_id=DriveId(drive_id), folders=[], files=[], files_complete=True)


def _member_user() -> User:
    return _user()


async def _no_db() -> AsyncIterator[None]:
    yield None


@pytest.fixture
def client() -> AsyncClient:
    _FakeCache.full_tree_reads = 0
    _FakeCache.metadata_reads = 0
    member_drives = [DriveSummary(id=MEMBER_DRIVE, name="Mine")]
    app.dependency_overrides[get_current_user] = _member_user
    app.dependency_overrides[drive_service_dep] = lambda: _FakeDriveService(member_drives)
    app.dependency_overrides[sidebar_cache_service_dep] = _FakeCache
    app.dependency_overrides[extras_service_dep] = _FakeExtras
    app.dependency_overrides[files_service_dep] = _FakeFiles
    app.dependency_overrides[get_db] = _no_db
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


# Sidebar drive endpoints gate on shared-drive membership
# (``require_member_drive``): the path param really is a drive id.
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", f"/sidebar/drives/{MEMBER_DRIVE}/folders"),
        ("GET", f"/sidebar/drives/{MEMBER_DRIVE}/permissions"),
        ("POST", f"/sidebar/drives/{MEMBER_DRIVE}/refresh"),
    ],
)
async def test_member_drive_is_allowed(client: AsyncClient, method: str, path: str) -> None:
    async with client:
        resp = await client.request(method, path)
    assert resp.status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/sidebar/drives",
        f"/sidebar/drives/{MEMBER_DRIVE}/folders",
        "/sidebar/folders/folder-1/files",
    ],
)
async def test_sidebar_metadata_is_never_browser_cached(client: AsyncClient, path: str) -> None:
    async with client:
        resp = await client.get(path)
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "private, no-store"


async def test_sidebar_tree_304_is_never_browser_cached(client: AsyncClient) -> None:
    async with client:
        first = await client.get(f"/sidebar/drives/{MEMBER_DRIVE}/folders")
        resp = await client.get(
            f"/sidebar/drives/{MEMBER_DRIVE}/folders",
            headers={"if-none-match": first.headers["etag"]},
        )
    assert resp.status_code == 304
    assert resp.headers["cache-control"] == "private, no-store"
    assert _FakeCache.full_tree_reads == 1
    assert _FakeCache.metadata_reads == 1


async def test_unconditional_sidebar_tree_read_skips_metadata_query(client: AsyncClient) -> None:
    async with client:
        resp = await client.get(f"/sidebar/drives/{MEMBER_DRIVE}/folders")
    assert resp.status_code == 200
    assert _FakeCache.full_tree_reads == 1
    assert _FakeCache.metadata_reads == 0


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", f"/sidebar/drives/{OTHER_DRIVE}/folders"),
        ("GET", f"/sidebar/drives/{OTHER_DRIVE}/permissions"),
        ("POST", f"/sidebar/drives/{OTHER_DRIVE}/refresh"),
    ],
)
async def test_non_member_drive_is_404(client: AsyncClient, method: str, path: str) -> None:
    async with client:
        resp = await client.request(method, path)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Drive not found"


# Doc-icon endpoints gate on *document* access (``require_accessible_doc``):
# the ``{drive_id}`` path param holds a doc id, so the guard verifies the
# caller can open the doc rather than testing it against the drive list.
@pytest.mark.parametrize(
    ("method", "json"),
    [("PUT", {"icon": "🔥", "cover_url": None})],
)
async def test_accessible_doc_icon_is_allowed(
    client: AsyncClient, method: str, json: dict[str, object] | None
) -> None:
    async with client:
        resp = await client.request(method, f"/extras/doc-icons/{ACCESSIBLE_DOC}", json=json)
    assert resp.status_code == 200


@pytest.mark.parametrize(
    ("method", "json"),
    [("PUT", {"icon": "🔥", "cover_url": None})],
)
async def test_inaccessible_doc_icon_is_404(
    client: AsyncClient, method: str, json: dict[str, object] | None
) -> None:
    async with client:
        resp = await client.request(method, f"/extras/doc-icons/{INACCESSIBLE_DOC}", json=json)
    assert resp.status_code == 404
    assert resp.json()["code"] == "file_not_in_scope"


async def test_doc_icon_list_omits_inaccessible_metadata(client: AsyncClient) -> None:
    async with client:
        resp = await client.get("/extras/doc-icons")
    assert resp.status_code == 200
    assert resp.json() == [{"drive_id": ACCESSIBLE_DOC, "icon": "📄", "cover_url": None}]


async def test_non_member_never_reaches_the_cache(client: AsyncClient) -> None:
    async with client:
        resp = await client.request("GET", f"/sidebar/drives/{uuid.uuid7()}/folders")
    assert resp.status_code == 404


async def test_permissions_response_is_not_cached(client: AsyncClient) -> None:
    async with client:
        resp = await client.get(f"/sidebar/drives/{MEMBER_DRIVE}/permissions")
    assert resp.headers["cache-control"] == "private, no-store"
