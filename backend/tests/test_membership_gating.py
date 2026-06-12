from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
import uuid

from httpx import ASGITransport, AsyncClient
import pytest

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.drive.dependencies import drive_service_dep
from app.drive.schemas import DriveSummary, DriveTree
from app.extras.dependencies import extras_service_dep
from app.extras.schemas import DocIconRead
from app.main import app
from app.models.user import User
from app.sidebar.cache import CachedDriveTree, SidebarCacheService
from app.sidebar.dependencies import sidebar_cache_service_dep
from tests.test_files_service import _FakeDriveService, _user


MEMBER_DRIVE = "member-drive"
OTHER_DRIVE = "someone-elses-drive"


class _FakeCache(SidebarCacheService):
    async def get_drive_tree(self, db: object, drive_id: str) -> CachedDriveTree:
        return CachedDriveTree(tree=_tree(drive_id), refreshed_at=None)

    async def peek_drive_tree(self, db: object, drive_id: str) -> CachedDriveTree | None:
        return None

    async def get_folder_children(self, folder_id: str) -> list:
        return []

    async def invalidate_drive(self, db: object, drive_id: str) -> None:
        return None

    async def refresh_drive(self, db: object, drive_id: str) -> DriveTree:
        return _tree(drive_id)


class _FakeExtras:
    async def get_doc_icon(self, drive_id: str) -> DocIconRead:
        return DocIconRead(drive_id=drive_id, icon="📄", cover_url=None)

    async def upsert_doc_icon(self, drive_id: str, payload: object) -> DocIconRead:
        return DocIconRead(drive_id=drive_id, icon="📄", cover_url=None)


def _tree(drive_id: str) -> DriveTree:
    return DriveTree(drive_id=drive_id, folders=[], files=[])


def _member_user() -> User:
    return _user()


async def _no_db() -> AsyncIterator[None]:
    yield None


@pytest.fixture
def client() -> AsyncClient:
    member_drives = [DriveSummary(id=MEMBER_DRIVE, name="Mine")]
    app.dependency_overrides[get_current_user] = _member_user
    app.dependency_overrides[drive_service_dep] = lambda: _FakeDriveService(member_drives)
    app.dependency_overrides[sidebar_cache_service_dep] = _FakeCache
    app.dependency_overrides[extras_service_dep] = _FakeExtras
    app.dependency_overrides[get_db] = _no_db
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", f"/sidebar/drives/{MEMBER_DRIVE}/folders"),
        ("POST", f"/sidebar/drives/{MEMBER_DRIVE}/refresh"),
        ("GET", f"/extras/doc-icons/{MEMBER_DRIVE}"),
    ],
)
async def test_member_drive_is_allowed(client: AsyncClient, method: str, path: str) -> None:
    async with client:
        resp = await client.request(method, path)
    assert resp.status_code == 200


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("GET", f"/sidebar/drives/{OTHER_DRIVE}/folders", None),
        ("POST", f"/sidebar/drives/{OTHER_DRIVE}/refresh", None),
        ("GET", f"/extras/doc-icons/{OTHER_DRIVE}", None),
        ("PUT", f"/extras/doc-icons/{OTHER_DRIVE}", {"icon": "🔥", "cover_url": None}),
    ],
)
async def test_non_member_drive_is_404(
    client: AsyncClient, method: str, path: str, json: dict[str, object] | None
) -> None:
    async with client:
        resp = await client.request(method, path, json=json)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Drive not found"


async def test_non_member_never_reaches_the_cache(client: AsyncClient) -> None:
    async with client:
        resp = await client.request("GET", f"/sidebar/drives/{uuid.uuid4()}/folders")
    assert resp.status_code == 404
