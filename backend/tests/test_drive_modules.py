from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
import secrets
import threading
from typing import Any, Self, override
import uuid

from httplib2 import Response
from pydantic import JsonValue
import pytest
from tenacity import wait_none

from app.auth.encryption import encrypt_text
from app.cache.service import CacheNamespace, CacheService
from app.drive.errors import DrivePermissionsUpstreamError, DriveTraversalIncompleteError
from app.drive.schemas import (
    MIME_FOLDER,
    MIME_SHORTCUT,
    DriveNode,
    DriveSummary,
    SearchResult,
    ShortcutProbe,
)
from app.drive.service import GoogleDriveService
from app.drive.snippets import extract_snippet
from app.identifiers import DriveId, FileId, GoogleUserId, UserId
from app.models.user import User


def _user() -> User:
    u = User()
    u.id = UserId(uuid.uuid7())
    u.email = "test@sarj.ai"
    u.google_id = GoogleUserId("google-test-id")
    u.token_version = 0
    return u


def test_google_credentials_convert_aware_expiry_for_google_auth() -> None:
    user = _user()
    user.encrypted_access_token = encrypt_text(secrets.token_hex(16))
    user.google_access_token_expires_at = datetime.now(UTC) + timedelta(hours=1)

    credentials = GoogleDriveService.build_credentials(user)

    assert credentials.valid
    assert credentials.expiry is not None
    assert credentials.expiry.tzinfo is None


class _FakePendingCall:
    def __init__(self, page: dict[str, Any]) -> None:
        self._page = page

    def execute(self) -> dict[str, Any]:
        return self._page


class _FakeListable:
    def __init__(self, pages: list[dict[str, Any]], captured: list[dict[str, Any]]) -> None:
        self._pages = pages
        self._captured = captured
        self._idx = 0
        self._lock = threading.Lock()

    def list(self, **kwargs: Any) -> _FakePendingCall:
        return self._record(dict(kwargs))

    def _record(self, call: dict[str, Any]) -> _FakePendingCall:
        with self._lock:
            self._captured.append(call)
            page = self._pages[self._idx]
            self._idx += 1
        return _FakePendingCall(page)


class _FakeFilesListable(_FakeListable):
    def __init__(
        self,
        pages: list[dict[str, Any]],
        children_pages: list[dict[str, Any]],
        captured: list[dict[str, Any]],
        root_page: dict[str, Any],
    ) -> None:
        super().__init__(pages, captured)
        self._children = _FakeListable(children_pages, captured)
        self._root_page = root_page

    @override
    def list(self, **kwargs: Any) -> _FakePendingCall:
        q = str(kwargs.get("q", ""))
        if "in parents" in q or q.startswith("trashed=false"):
            return self._children.list(**kwargs)
        return super().list(**kwargs)

    def get(self, **kwargs: Any) -> _FakePendingCall:
        if kwargs.get("fileId") == "root":
            self._captured.append({"_op": "get", **kwargs})
            return _FakePendingCall(self._root_page)
        return self._record({"_op": "get", **kwargs})

    def update(self, **kwargs: Any) -> _FakePendingCall:
        return self._record({"_op": "update", **kwargs})

    def create(self, **kwargs: Any) -> _FakePendingCall:
        return self._record({"_op": "create", **kwargs})


class _FakeClient:
    def __init__(
        self,
        *,
        drives_pages: list[dict[str, Any]] | None = None,
        files_pages: list[dict[str, Any]] | None = None,
        permissions_pages: list[dict[str, Any]] | None = None,
        children_pages: list[dict[str, Any]] | None = None,
        root_page: dict[str, Any] | None = None,
    ) -> None:
        self.drives_captured: list[dict[str, Any]] = []
        self.files_captured: list[dict[str, Any]] = []
        self._drives = _FakeListable(drives_pages or [{}], self.drives_captured)
        self._files = _FakeFilesListable(
            files_pages or [{}],
            children_pages or [{}],
            self.files_captured,
            root_page or {"id": "my-drive-root"},
        )
        self.permissions_captured: list[dict[str, Any]] = []
        self._permissions = _FakeListable(
            permissions_pages or [{"permissions": []}],
            self.permissions_captured,
        )

    def drives(self) -> _FakeListable:
        return self._drives

    def files(self) -> _FakeFilesListable:
        return self._files

    def permissions(self) -> _FakeListable:
        return self._permissions


@pytest.fixture
def patch_drive_client(monkeypatch: pytest.MonkeyPatch) -> Callable[[Any], None]:

    def _install(client: Any) -> None:
        monkeypatch.setattr(
            GoogleDriveService,
            "build_drive_client",
            staticmethod(lambda _u: client),
        )

    return _install


class _Cache(CacheService):
    def __init__(self) -> None:
        self.values: dict[tuple[CacheNamespace, str], JsonValue] = {}

    @override
    async def get(self, namespace: CacheNamespace, key: str) -> JsonValue | None:
        return self.values.get((namespace, key))

    @override
    async def get_many(self, namespace: CacheNamespace, keys: list[str]) -> dict[str, JsonValue]:
        return {
            key: self.values[cache_key]
            for key in keys
            if (cache_key := (namespace, key)) in self.values
        }

    @override
    async def put(
        self,
        namespace: CacheNamespace,
        key: str,
        value: JsonValue,
        *,
        ttl: timedelta,
    ) -> None:
        del ttl
        self.values[namespace, key] = value

    @override
    async def put_many(
        self,
        namespace: CacheNamespace,
        values: Mapping[str, JsonValue],
        *,
        ttl: timedelta,
    ) -> None:
        del ttl
        self.values.update({(namespace, key): value for key, value in values.items()})

    @override
    async def delete(self, namespace: CacheNamespace, key: str) -> None:
        self.values.pop((namespace, key), None)


def _svc() -> GoogleDriveService:
    return GoogleDriveService(_user(), _Cache())


async def test_list_drives_paginates_until_empty_token(patch_drive_client: Any) -> None:
    client = _FakeClient(
        drives_pages=[
            {
                "drives": [{"id": "d1", "name": "Engineering"}],
                "nextPageToken": "t1",
            },
            {
                "drives": [{"id": "d2", "name": "Marketing"}],
            },
        ]
    )
    patch_drive_client(client)
    result = await _svc().list_drives()

    assert [d.id for d in result] == ["my-drive-root", "d1", "d2"]
    assert [d.name for d in result] == ["My Drive", "Engineering", "Marketing"]
    assert [d.is_personal for d in result] == [True, False, False]
    assert len(client.drives_captured) == 2
    assert client.drives_captured[0]["fields"] == "nextPageToken,drives(id,name,hidden)"
    assert all(call["q"] == "hidden = false" for call in client.drives_captured)
    assert all(call["useDomainAdminAccess"] is False for call in client.drives_captured)
    assert client.drives_captured[1].get("pageToken") == "t1"


async def test_list_drives_skips_hidden_entries(patch_drive_client: Any) -> None:
    client = _FakeClient(
        drives_pages=[
            {
                "drives": [
                    {"id": "d1", "name": "Engineering"},
                    {"id": "d2", "name": "Healthcare", "hidden": True},
                    {"id": "d3", "name": "Marketing", "hidden": False},
                ]
            }
        ]
    )
    patch_drive_client(client)

    result = await _svc().list_drives()

    assert [d.id for d in result] == ["my-drive-root", "d1", "d3"]


async def test_list_drive_permissions_maps_page_and_requests_private_fields(
    patch_drive_client: Any,
) -> None:
    client = _FakeClient(
        drives_pages=[{"drives": [{"id": "d1", "name": "Engineering"}]}],
        permissions_pages=[
            {
                "nextPageToken": "next",
                "permissions": [
                    {
                        "displayName": "Engineering",
                        "emailAddress": "engineering@example.com",
                        "id": "permission-1",
                        "role": "fileOrganizer",
                        "type": "group",
                        "permissionDetails": [
                            {"permissionType": "member", "role": "fileOrganizer", "inherited": True}
                        ],
                    }
                ],
            }
        ],
    )
    patch_drive_client(client)

    incoming_page_token = "incoming"
    next_page_token = "next"
    page = await _svc().list_drive_permissions(DriveId("d1"), page_token=incoming_page_token)

    assert page.access_model == "shared_drive_membership"
    assert page.next_page_token == next_page_token
    assert page.permissions[0].principal_type == "group"
    assert page.permissions[0].role == "fileOrganizer"
    assert page.permissions[0].inherited
    call = client.permissions_captured[0]
    assert call["fileId"] == "d1"
    assert call["pageToken"] == "incoming"
    assert call["pageSize"] == 100
    assert call["supportsAllDrives"] is True
    assert call["useDomainAdminAccess"] is False
    assert "emailAddress" in call["fields"]
    assert "permissionDetails" in call["fields"]


async def test_my_drive_permissions_are_item_specific_without_google_acl_call(
    patch_drive_client: Any,
) -> None:
    client = _FakeClient()
    patch_drive_client(client)

    page = await _svc().list_drive_permissions(DriveId("my-drive-root"))

    assert page.access_model == "item_specific"
    assert page.permissions == []
    assert client.permissions_captured == []


async def test_file_permission_snapshot_is_live_complete_and_bounded(
    patch_drive_client: Any,
) -> None:
    client = _FakeClient(
        files_pages=[
            {
                "id": "folder",
                "mimeType": MIME_FOLDER,
                "version": "17",
                "ownedByMe": True,
                "shared": False,
                "capabilities": {"canAddChildren": True},
                "owners": [{"emailAddress": "qa@example.test"}],
            },
            {
                "id": "folder",
                "mimeType": MIME_FOLDER,
                "version": "17",
                "ownedByMe": True,
                "shared": False,
                "capabilities": {"canAddChildren": True},
                "owners": [{"emailAddress": "qa@example.test"}],
            },
        ],
        permissions_pages=[
            {
                "nextPageToken": "page-2",
                "permissions": [
                    {
                        "id": "deleted-reader",
                        "type": "user",
                        "role": "reader",
                        "emailAddress": "old@example.test",
                        "deleted": True,
                        "permissionDetails": [
                            {"permissionType": "file", "role": "reader", "inherited": False}
                        ],
                    }
                ],
            },
            {
                "permissions": [
                    {
                        "id": "owner",
                        "type": "user",
                        "role": "owner",
                        "emailAddress": "qa@example.test",
                        "permissionDetails": [
                            {"permissionType": "file", "role": "owner", "inherited": False}
                        ],
                    }
                ]
            },
        ],
    )
    patch_drive_client(client)

    snapshot = await _svc().read_file_permissions_bounded(
        FileId("folder"), max_pages=2, max_permissions=10
    )

    assert snapshot.file_id == "folder"
    assert snapshot.drive_id is None
    assert snapshot.version == "17"
    assert snapshot.owner_emails == ("qa@example.test",)
    assert [permission.id for permission in snapshot.permissions] == ["deleted-reader", "owner"]
    metadata_call = client.files_captured[0]
    assert metadata_call["fileId"] == "folder"
    assert metadata_call["supportsAllDrives"] is True
    assert "ownedByMe" in metadata_call["fields"]
    assert "owners(emailAddress)" in metadata_call["fields"]
    assert client.permissions_captured[0]["useDomainAdminAccess"] is False
    assert client.permissions_captured[1]["pageToken"] == "page-2"


@pytest.mark.parametrize(
    "permissions_pages",
    [
        [{"permissions": "not-a-list"}],
        [{"permissions": [{"id": "missing-role", "type": "user"}]}],
        [{"permissions": [{"id": "owner", "type": "user", "role": "owner"}]}],
        [
            {
                "permissions": [
                    {
                        "id": "owner",
                        "type": "user",
                        "role": "owner",
                        "permissionDetails": [{"permissionType": "file", "role": "owner"}],
                    }
                ]
            }
        ],
        [
            {
                "permissions": [
                    {
                        "id": "owner",
                        "type": "user",
                        "role": "owner",
                        "permissionDetails": [{"role": "owner", "inherited": False}],
                    }
                ]
            }
        ],
        [
            {
                "permissions": [
                    {
                        "id": "owner",
                        "type": "user",
                        "role": "owner",
                        "permissionDetails": [{"permissionType": "file", "inherited": False}],
                    }
                ]
            }
        ],
        [
            {
                "nextPageToken": "same",
                "permissions": [{"id": "one", "type": "user", "role": "owner"}],
            },
            {
                "nextPageToken": "same",
                "permissions": [{"id": "two", "type": "user", "role": "reader"}],
            },
        ],
        [
            {
                "permissions": [
                    {"id": "duplicate", "type": "user", "role": "owner"},
                    {"id": "duplicate", "type": "user", "role": "reader"},
                ]
            }
        ],
    ],
    ids=(
        "invalid-list",
        "invalid-entry",
        "missing-effective-details",
        "missing-inherited",
        "missing-permission-type",
        "missing-detail-role",
        "repeated-page-token",
        "duplicate-id",
    ),
)
async def test_file_permission_snapshot_fails_closed_on_incomplete_or_invalid_inventory(
    patch_drive_client: Any,
    permissions_pages: list[dict[str, Any]],
) -> None:
    client = _FakeClient(
        files_pages=[
            {
                "id": "folder",
                "mimeType": MIME_FOLDER,
                "version": "17",
                "ownedByMe": True,
                "shared": False,
                "capabilities": {"canAddChildren": True},
                "owners": [{"emailAddress": "qa@example.test"}],
            },
            {
                "id": "folder",
                "mimeType": MIME_FOLDER,
                "version": "17",
                "ownedByMe": True,
                "shared": False,
                "capabilities": {"canAddChildren": True},
                "owners": [{"emailAddress": "qa@example.test"}],
            },
        ],
        permissions_pages=permissions_pages,
    )
    patch_drive_client(client)

    with pytest.raises((DrivePermissionsUpstreamError, DriveTraversalIncompleteError)):
        await _svc().read_file_permissions_bounded(
            FileId("folder"), max_pages=2, max_permissions=10
        )


async def test_file_permission_snapshot_refuses_truncated_inventory(
    patch_drive_client: Any,
) -> None:
    client = _FakeClient(
        files_pages=[
            {
                "id": "folder",
                "mimeType": MIME_FOLDER,
                "version": "17",
                "ownedByMe": True,
                "shared": False,
                "capabilities": {"canAddChildren": True},
                "owners": [{"emailAddress": "qa@example.test"}],
            },
            {
                "id": "folder",
                "mimeType": MIME_FOLDER,
                "version": "17",
                "ownedByMe": True,
                "shared": False,
                "capabilities": {"canAddChildren": True},
                "owners": [{"emailAddress": "qa@example.test"}],
            },
        ],
        permissions_pages=[
            {
                "nextPageToken": "more",
                "permissions": [
                    {
                        "id": "owner",
                        "type": "user",
                        "role": "owner",
                        "permissionDetails": [
                            {"permissionType": "file", "role": "owner", "inherited": False}
                        ],
                    }
                ],
            }
        ],
    )
    patch_drive_client(client)

    with pytest.raises(DriveTraversalIncompleteError):
        await _svc().read_file_permissions_bounded(FileId("folder"), max_pages=1, max_permissions=1)


async def test_file_permission_snapshot_rejects_metadata_change_during_inventory(
    patch_drive_client: Any,
) -> None:
    client = _FakeClient(
        files_pages=[
            {
                "id": "folder",
                "mimeType": MIME_FOLDER,
                "version": "17",
                "ownedByMe": True,
                "shared": False,
                "capabilities": {"canAddChildren": True},
                "owners": [{"emailAddress": "qa@example.test"}],
            },
            {
                "id": "folder",
                "mimeType": MIME_FOLDER,
                "version": "18",
                "ownedByMe": True,
                "shared": True,
                "capabilities": {"canAddChildren": True},
                "owners": [{"emailAddress": "qa@example.test"}],
            },
        ],
        permissions_pages=[
            {
                "permissions": [
                    {
                        "id": "owner",
                        "type": "user",
                        "role": "owner",
                        "emailAddress": "qa@example.test",
                        "permissionDetails": [
                            {"permissionType": "file", "role": "owner", "inherited": False}
                        ],
                    }
                ]
            }
        ],
    )
    patch_drive_client(client)

    with pytest.raises(DriveTraversalIncompleteError):
        await _svc().read_file_permissions_bounded(
            FileId("folder"), max_pages=2, max_permissions=10
        )


async def test_list_drives_skips_entries_missing_fields(patch_drive_client: Any) -> None:
    client = _FakeClient(
        drives_pages=[
            {
                "drives": [
                    {"id": "d1", "name": "Engineering"},
                    {"id": "d2"},
                    {"name": "Orphan"},
                    {},
                ]
            }
        ]
    )
    patch_drive_client(client)
    result = await _svc().list_drives()
    assert [d.id for d in result] == ["my-drive-root", "d1"]


async def test_list_drives_singleflights_concurrent_request_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _svc()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def _load() -> list[DriveSummary]:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return [DriveSummary(id=DriveId("d1"), name="Drive")]

    monkeypatch.setattr(svc, "_load_drives", _load)
    first = asyncio.create_task(svc.list_drives())
    await started.wait()
    second = asyncio.create_task(svc.list_drives())
    await asyncio.sleep(0)
    release.set()

    assert await first == await second
    assert await svc.list_drives() == [DriveSummary(id=DriveId("d1"), name="Drive")]
    assert calls == 1


@pytest.mark.parametrize(
    ("file_id", "locations"),
    [
        (
            "personal-doc",
            [
                {"id": "personal-folder", "parents": ["personal-root"]},
            ],
        ),
        ("personal-root", [{"id": "personal-root"}]),
    ],
    ids=["descendant", "root"],
)
async def test_resolve_member_drive_accepts_only_my_drive_ancestry(
    patch_drive_client: Any,
    file_id: FileId,
    locations: list[dict[str, Any]],
) -> None:
    client = _FakeClient(
        files_pages=locations,
        root_page={"id": "personal-root"},
    )
    patch_drive_client(client)

    resolved = await _svc().resolve_member_drive(
        FileId(file_id),
        None,
        parent_id=(FileId("personal-folder") if file_id == "personal-doc" else None),
    )

    assert resolved == "personal-root"


@pytest.mark.parametrize(
    "locations",
    [
        [{"id": "shared-with-me", "parents": ["owner-root"]}, {"id": "owner-root"}],
        [{"id": "rootless"}],
    ],
    ids=["shared-with-me", "rootless"],
)
async def test_resolve_member_drive_rejects_non_my_drive_files(
    patch_drive_client: Any,
    locations: list[dict[str, Any]],
) -> None:
    client = _FakeClient(
        files_pages=locations,
        root_page={"id": "personal-root"},
    )
    patch_drive_client(client)

    resolved = await _svc().resolve_member_drive(FileId(str(locations[0]["id"])), None)

    assert resolved is None


@pytest.mark.parametrize(
    ("reported_drive_id", "expected"),
    [("engineering", "engineering"), ("foreign", None)],
    ids=["listed-shared-drive", "unlisted-shared-drive"],
)
async def test_resolve_member_drive_preserves_exact_shared_drive_membership(
    patch_drive_client: Any,
    reported_drive_id: str,
    expected: str | None,
) -> None:
    client = _FakeClient(drives_pages=[{"drives": [{"id": "engineering", "name": "Engineering"}]}])
    patch_drive_client(client)

    resolved = await _svc().resolve_member_drive(FileId("doc"), DriveId(reported_drive_id))

    assert resolved == expected
    ancestry_calls = [
        call
        for call in client.files_captured
        if call.get("_op") == "get" and call.get("fileId") != "root"
    ]
    assert ancestry_calls == []


async def test_walk_my_drive_uses_user_corpus_and_excludes_shared_drive_items(
    patch_drive_client: Any,
) -> None:
    client = _FakeClient(
        root_page={"id": "personal-root"},
        files_pages=[
            {
                "files": [
                    {
                        "id": "mine-folder",
                        "name": "Mine",
                        "mimeType": MIME_FOLDER,
                        "parents": ["personal-root"],
                    },
                    {
                        "driveId": "shared-drive",
                        "id": "shared-folder",
                        "name": "Shared",
                        "mimeType": MIME_FOLDER,
                        "parents": ["shared-drive"],
                    },
                ]
            }
        ],
        children_pages=[
            {
                "files": [
                    {
                        "id": "mine-doc",
                        "name": "Personal notes",
                        "mimeType": "application/vnd.google-apps.document",
                        "parents": ["personal-root"],
                    },
                    {
                        "driveId": "shared-drive",
                        "id": "shared-doc",
                        "name": "Shared notes",
                        "mimeType": "application/vnd.google-apps.document",
                        "parents": ["shared-drive"],
                    },
                ]
            }
        ],
    )
    patch_drive_client(client)

    tree = await _svc().walk_drive_folders(DriveId("personal-root"))

    assert [folder.id for folder in tree.folders] == ["mine-folder"]
    assert [file.id for file in tree.files] == ["mine-doc"]
    assert tree.folders[0].parent_id is None
    assert tree.files[0].parent_id is None
    list_calls = [call for call in client.files_captured if "q" in call]
    assert all(call["corpora"] == "user" for call in list_calls)
    assert all("driveId" not in call for call in list_calls)
    assert all("'me' in owners" in call["q"] for call in list_calls)


async def test_walk_drive_folders_uses_corpora_drive_and_folder_query(
    patch_drive_client: Any,
) -> None:
    client = _FakeClient(
        files_pages=[
            {
                "files": [
                    {
                        "id": "f1",
                        "name": "Root Folder",
                        "mimeType": MIME_FOLDER,
                        "parents": ["the-drive"],
                    },
                    {
                        "id": "f2",
                        "name": "Sub",
                        "mimeType": MIME_FOLDER,
                        "parents": ["f1"],
                    },
                ]
            }
        ]
    )
    patch_drive_client(client)

    tree = await _svc().walk_drive_folders(DriveId("the-drive"))
    assert tree.drive_id == "the-drive"
    assert [f.id for f in tree.folders] == ["f1", "f2"]
    assert tree.folders[0].parent_id is None
    assert tree.folders[1].parent_id == "f1"

    call = next(c for c in client.files_captured if c.get("corpora") == "drive")
    assert call["driveId"] == "the-drive"
    assert call["includeItemsFromAllDrives"] is True
    assert call["supportsAllDrives"] is True
    assert f"mimeType='{MIME_FOLDER}'" in call["q"]
    assert "trashed=false" in call["q"]


async def test_walk_drive_folders_paginates(patch_drive_client: Any) -> None:
    client = _FakeClient(
        files_pages=[
            {
                "files": [
                    {"id": "f1", "name": "A", "mimeType": MIME_FOLDER, "parents": ["d"]},
                ],
                "nextPageToken": "NEXT",
            },
            {
                "files": [
                    {"id": "f2", "name": "B", "mimeType": MIME_FOLDER, "parents": ["d"]},
                ],
            },
        ]
    )
    patch_drive_client(client)
    tree = await _svc().walk_drive_folders(DriveId("d"))
    assert [f.id for f in tree.folders] == ["f1", "f2"]
    walk_calls = [
        c
        for c in client.files_captured
        if c.get("corpora") == "drive" and str(c.get("q", "")).startswith("(")
    ]
    assert walk_calls[1].get("pageToken") == "NEXT"


async def test_walk_populates_files_from_parallel_drive_listing(
    patch_drive_client: Any,
) -> None:
    client = _FakeClient(
        files_pages=[
            {
                "files": [
                    {"id": "f1", "name": "Folder", "mimeType": MIME_FOLDER, "parents": ["d"]},
                ]
            }
        ],
        children_pages=[
            {
                "files": [
                    {
                        "id": "doc1",
                        "name": "Root Doc",
                        "mimeType": "application/vnd.google-apps.document",
                        "parents": ["d"],
                    },
                    {
                        "id": "doc2",
                        "name": "Nested Doc",
                        "mimeType": "application/vnd.google-apps.document",
                        "parents": ["f1"],
                    },
                    {"id": "f1", "name": "Folder", "mimeType": MIME_FOLDER, "parents": ["d"]},
                ]
            }
        ],
    )
    patch_drive_client(client)

    tree = await _svc().walk_drive_folders(DriveId("d"))

    assert [f.id for f in tree.folders] == ["f1"]
    assert [f.id for f in tree.files] == ["doc1", "doc2"]
    assert tree.files[0].parent_id is None
    assert tree.files[1].parent_id == "f1"
    files_call = next(c for c in client.files_captured if c.get("q", "").startswith("trashed"))
    assert files_call["corpora"] == "drive"
    assert files_call["driveId"] == "d"
    assert f"mimeType!='{MIME_FOLDER}'" in files_call["q"]


async def test_walk_resolves_folder_and_file_shortcuts_in_one_probe_batch(
    patch_drive_client: Any,
) -> None:
    client = _FakeClient(
        files_pages=[
            {
                "files": [
                    {
                        "id": "folder-link",
                        "name": "Folder link",
                        "mimeType": MIME_SHORTCUT,
                        "parents": ["d"],
                        "shortcutDetails": {
                            "targetId": "folder-target",
                            "targetMimeType": MIME_FOLDER,
                        },
                    }
                ]
            }
        ],
        children_pages=[
            {
                "files": [
                    {
                        "id": "file-link",
                        "name": "File link",
                        "mimeType": MIME_SHORTCUT,
                        "parents": ["d"],
                        "shortcutDetails": {
                            "targetId": "file-target",
                            "targetMimeType": "application/vnd.google-apps.document",
                        },
                    }
                ]
            }
        ],
    )
    patch_drive_client(client)
    calls: list[list[FileId]] = []

    class RecordingDrive(GoogleDriveService):
        @override
        def _probe_targets_sync(
            self, target_ids: list[FileId]
        ) -> dict[FileId, ShortcutProbe | None]:
            calls.append(target_ids)
            return {target_id: ShortcutProbe(accessible=True) for target_id in target_ids}

    service = RecordingDrive(_user(), _Cache())
    tree = await service.walk_drive_folders(DriveId("d"))

    assert calls == [["file-target", "folder-target"]]
    assert [node.id for node in tree.folders] == ["folder-link"]
    assert [node.id for node in tree.files] == ["file-link"]


async def test_shortcut_resolution_keeps_authorization_bound_to_each_target() -> None:
    class TargetDrive(GoogleDriveService):
        @override
        async def _drive_names(self) -> dict[DriveId, str]:
            return {DriveId("available-drive"): "Available"}

        @override
        def _probe_targets_sync(
            self, target_ids: list[FileId]
        ) -> dict[FileId, ShortcutProbe | None]:
            return {
                target_id: ShortcutProbe(
                    accessible=target_id != "denied-target",
                    target_drive_id=DriveId("available-drive")
                    if target_id != "denied-target"
                    else None,
                )
                for target_id in target_ids
            }

    nodes = [
        DriveNode(
            id=FileId("same-shortcut"),
            name="Shortcut",
            mime_type=MIME_SHORTCUT,
            parent_id=None,
            modified_time=None,
            shortcut_target_id=FileId(target),
            shortcut_target_mime_type=MIME_FOLDER,
        )
        for target in ("denied-target", "available-target")
    ]
    resolved = await TargetDrive(_user())._resolve_shortcut_nodes(nodes)

    assert resolved[0].shortcut_inaccessible is True
    assert resolved[0].shortcut_target_drive_id is None
    assert resolved[1].shortcut_inaccessible is False
    assert resolved[1].shortcut_target_drive_id == "available-drive"


async def test_walk_truncates_oversized_drive_file_listings(
    patch_drive_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.drive import service as drive_service

    monkeypatch.setattr(drive_service, "_MAX_DRIVE_FILE_PAGES", 1)
    client = _FakeClient(
        files_pages=[{"files": []}],
        children_pages=[
            {
                "files": [
                    {
                        "id": "doc1",
                        "name": "Doc",
                        "mimeType": "application/vnd.google-apps.document",
                        "parents": ["d"],
                    }
                ],
                "nextPageToken": "MORE",
            },
        ],
    )
    patch_drive_client(client)

    tree = await _svc().walk_drive_folders(DriveId("d"))

    assert tree.files_complete is False
    assert tree.files == []


async def test_list_folder_children_queries_by_parent(patch_drive_client: Any) -> None:
    client = _FakeClient(
        children_pages=[
            {
                "files": [
                    {
                        "id": "doc1",
                        "name": "Doc",
                        "mimeType": "application/vnd.google-apps.document",
                        "parents": ["folder-x"],
                        "description": "Navigator summary",
                    }
                ]
            }
        ]
    )
    patch_drive_client(client)
    children = await _svc().list_folder_children(FileId("folder-x"))
    assert [c.id for c in children] == ["doc1"]
    assert children[0].parent_id == "folder-x"
    assert children[0].description == "Navigator summary"
    call = client.files_captured[0]
    assert "'folder-x' in parents" in call["q"]
    assert call["corpora"] == "allDrives"
    assert "description" in call["fields"]
    assert "version" not in call["fields"]


def test_internal_corpus_binding_fields_are_excluded_from_drive_node_serialization() -> None:
    node = DriveNode(
        id=FileId("doc"),
        name="Doc",
        mime_type="application/vnd.google-apps.document",
        parent_id=FileId("folder"),
        modified_time=None,
        version="provider-version",
        drive_id=DriveId("provider-drive"),
    )

    serialized = node.model_dump(mode="json")

    assert "version" not in serialized
    assert "drive_id" not in serialized


async def test_bounded_folder_children_rejects_partial_pagination(
    patch_drive_client: Any,
) -> None:
    client = _FakeClient(
        children_pages=[
            {
                "files": [
                    {
                        "id": "doc1",
                        "name": "Doc",
                        "mimeType": "application/vnd.google-apps.document",
                        "parents": ["folder-x"],
                    }
                ],
                "nextPageToken": "MORE",
            }
        ]
    )
    patch_drive_client(client)

    with pytest.raises(DriveTraversalIncompleteError):
        await _svc().list_folder_children_bounded(FileId("folder-x"), max_pages=1, max_children=10)


async def test_bounded_folder_members_paginates_without_filtering_unsupported_types(
    patch_drive_client: Any,
) -> None:
    client = _FakeClient(
        children_pages=[
            {
                "files": [
                    {
                        "id": "doc1",
                        "name": "Doc",
                        "mimeType": "application/vnd.google-apps.document",
                        "parents": ["folder-x"],
                        "driveId": "shared-drive",
                    }
                ],
                "nextPageToken": "MORE",
            },
            {
                "files": [
                    {
                        "id": "binary1",
                        "name": "Explicitly unsupported",
                        "mimeType": "application/x-security-evidence",
                        "parents": ["folder-x"],
                        "driveId": "shared-drive",
                    }
                ]
            },
        ]
    )
    patch_drive_client(client)

    result = await _svc().list_folder_members_bounded(
        FileId("folder-x"),
        drive_id=DriveId("shared-drive"),
        max_pages=2,
        max_children=10,
    )

    assert [member.id for member in result.members] == ["doc1", "binary1"]
    assert result.page_count == 2
    list_calls = [call for call in client.files_captured if "q" in call]
    assert len(list_calls) == 2
    assert all("mimeType=" not in str(call["q"]) for call in list_calls)
    assert all(call["corpora"] == "drive" for call in list_calls)
    assert all(call["driveId"] == "shared-drive" for call in list_calls)
    assert all("incompleteSearch" in call["fields"] for call in list_calls)
    assert all("version" in call["fields"] for call in list_calls)
    assert all("description" not in call["fields"] for call in list_calls)


async def test_bounded_folder_members_rejects_metadata_before_accumulating(
    patch_drive_client: Any,
) -> None:
    client = _FakeClient(
        children_pages=[
            {
                "files": [
                    {
                        "id": "doc1",
                        "name": "x" * 1_000,
                        "mimeType": "application/vnd.google-apps.document",
                        "parents": ["folder-x"],
                        "driveId": "shared-drive",
                        "version": "1",
                    }
                ]
            }
        ]
    )
    patch_drive_client(client)

    with pytest.raises(DriveTraversalIncompleteError):
        await _svc().list_folder_members_bounded(
            FileId("folder-x"),
            drive_id=DriveId("shared-drive"),
            max_pages=1,
            max_children=10,
            max_metadata_bytes=100,
        )


async def test_bounded_folder_members_retries_transient_provider_reads(
    patch_drive_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _TransientCall:
        def __init__(self) -> None:
            self.attempts = 0

        def execute(self) -> dict[str, object]:
            self.attempts += 1
            if self.attempts == 1:
                raise ConnectionError
            return {
                "files": [
                    {
                        "id": "doc1",
                        "name": "Doc",
                        "mimeType": "application/vnd.google-apps.document",
                        "parents": ["folder-x"],
                        "driveId": "shared-drive",
                    }
                ]
            }

    class _TransientFiles:
        def __init__(self) -> None:
            self.call = _TransientCall()

        def list(self, **_kwargs: object) -> _TransientCall:
            return self.call

    class _TransientClient:
        def __init__(self) -> None:
            self.files_api = _TransientFiles()

        def files(self) -> _TransientFiles:
            return self.files_api

    client = _TransientClient()
    patch_drive_client(client)
    monkeypatch.setattr("app.drive.retry.execute_with_retry.retry.wait", wait_none())

    svc = _svc()
    svc._my_drive_id = DriveId("personal-root")
    result = await svc.list_folder_members_bounded(
        FileId("folder-x"),
        drive_id=DriveId("shared-drive"),
        max_pages=2,
        max_children=10,
    )

    assert [member.id for member in result.members] == ["doc1"]
    assert client.files_api.call.attempts == 2


@pytest.mark.parametrize(
    "parents",
    [[], ["folder-x", "other"], ["other"]],
    ids=("missing", "multiple", "wrong"),
)
async def test_bounded_folder_members_rejects_inexact_provider_parent(
    patch_drive_client: Any,
    parents: list[str],
) -> None:
    client = _FakeClient(
        children_pages=[
            {
                "files": [
                    {
                        "id": "doc1",
                        "name": "Doc",
                        "mimeType": "application/vnd.google-apps.document",
                        "parents": parents,
                        "driveId": "shared-drive",
                    }
                ]
            }
        ]
    )
    patch_drive_client(client)

    with pytest.raises(DriveTraversalIncompleteError):
        await _svc().list_folder_members_bounded(
            FileId("folder-x"),
            drive_id=DriveId("shared-drive"),
            max_pages=1,
            max_children=10,
        )


async def test_bounded_folder_members_rejects_incomplete_search(
    patch_drive_client: Any,
) -> None:
    client = _FakeClient(children_pages=[{"files": [], "incompleteSearch": True}])
    patch_drive_client(client)

    with pytest.raises(DriveTraversalIncompleteError):
        await _svc().list_folder_members_bounded(
            FileId("folder-x"),
            drive_id=DriveId("shared-drive"),
            max_pages=1,
            max_children=10,
        )


@pytest.mark.parametrize("provider_drive", [None, "other-drive"])
async def test_bounded_shared_folder_members_rejects_drive_mismatch(
    patch_drive_client: Any,
    provider_drive: str | None,
) -> None:
    raw = {
        "id": "doc1",
        "name": "Doc",
        "mimeType": "application/vnd.google-apps.document",
        "parents": ["folder-x"],
    }
    if provider_drive is not None:
        raw["driveId"] = provider_drive
    client = _FakeClient(children_pages=[{"files": [raw]}])
    patch_drive_client(client)

    with pytest.raises(DriveTraversalIncompleteError):
        await _svc().list_folder_members_bounded(
            FileId("folder-x"),
            drive_id=DriveId("shared-drive"),
            max_pages=1,
            max_children=10,
        )


async def test_bounded_personal_folder_members_uses_user_corpus_without_drive_id(
    patch_drive_client: Any,
) -> None:
    client = _FakeClient(
        children_pages=[
            {
                "files": [
                    {
                        "id": "doc1",
                        "name": "Doc",
                        "mimeType": "application/vnd.google-apps.document",
                        "parents": ["folder-x"],
                    }
                ]
            }
        ]
    )
    patch_drive_client(client)
    svc = _svc()
    svc._my_drive_id = DriveId("personal-root")

    result = await svc.list_folder_members_bounded(
        FileId("folder-x"),
        drive_id=DriveId("personal-root"),
        max_pages=1,
        max_children=10,
    )

    (call,) = client.files_captured
    assert call["corpora"] == "user"
    assert "driveId" not in call
    assert result.members[0].drive_id == "personal-root"


def _stub_probes(
    monkeypatch: pytest.MonkeyPatch,
    svc: GoogleDriveService,
    mapping: dict[str, ShortcutProbe | None],
) -> None:

    def _fake(target_ids: list[FileId]) -> dict[FileId, ShortcutProbe | None]:
        return {tid: mapping.get(tid) for tid in target_ids}

    monkeypatch.setattr(svc, "_probe_targets_sync", _fake)


async def test_walk_surfaces_folder_shortcuts_and_drops_file_shortcuts(
    patch_drive_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        drives_pages=[
            {"drives": [{"id": "engineering", "name": "Engineering"}]},
        ],
        files_pages=[
            {
                "files": [
                    {
                        "id": "f1",
                        "name": "Sales root",
                        "mimeType": MIME_FOLDER,
                        "parents": ["sales"],
                    },
                    {
                        "id": "sc-folder",
                        "name": "API Reference",
                        "mimeType": MIME_SHORTCUT,
                        "parents": ["sales"],
                        "shortcutDetails": {
                            "targetId": "eng-api-folder",
                            "targetMimeType": MIME_FOLDER,
                        },
                    },
                    {
                        "id": "sc-file",
                        "name": "Spec.gdoc",
                        "mimeType": MIME_SHORTCUT,
                        "parents": ["sales"],
                        "shortcutDetails": {
                            "targetId": "eng-spec",
                            "targetMimeType": "application/vnd.google-apps.document",
                        },
                    },
                ]
            },
        ],
    )
    patch_drive_client(client)
    svc = _svc()
    _stub_probes(
        monkeypatch,
        svc,
        {"eng-api-folder": ShortcutProbe(accessible=True, target_drive_id=DriveId("engineering"))},
    )

    tree = await svc.walk_drive_folders(DriveId("sales"))
    ids = [n.id for n in tree.folders]
    assert ids == ["f1", "sc-folder"]

    sc = next(n for n in tree.folders if n.id == "sc-folder")
    assert sc.mime_type == MIME_SHORTCUT
    assert sc.shortcut_target_id == "eng-api-folder"
    assert sc.shortcut_target_mime_type == MIME_FOLDER
    assert sc.shortcut_target_drive_id == "engineering"
    assert sc.shortcut_target_drive_name == "Engineering"
    assert sc.shortcut_inaccessible is False

    walk_call = next(call for call in client.files_captured if "q" in call)
    q = walk_call["q"]
    assert f"mimeType='{MIME_FOLDER}'" in q
    assert f"mimeType='{MIME_SHORTCUT}'" in q
    assert "shortcutDetails(targetId,targetMimeType)" in walk_call["fields"]


async def test_list_children_surfaces_file_shortcuts_with_target_mime(
    patch_drive_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        drives_pages=[
            {"drives": [{"id": "engineering", "name": "Engineering"}]},
        ],
        children_pages=[
            {
                "files": [
                    {
                        "id": "doc1",
                        "name": "Native doc",
                        "mimeType": "application/vnd.google-apps.document",
                        "parents": ["folder-x"],
                    },
                    {
                        "id": "sc-doc",
                        "name": "Linked doc",
                        "mimeType": MIME_SHORTCUT,
                        "parents": ["folder-x"],
                        "shortcutDetails": {
                            "targetId": "eng-doc",
                            "targetMimeType": "application/vnd.google-apps.document",
                        },
                    },
                    {
                        "id": "sc-unsupported",
                        "name": "Linked third-party",
                        "mimeType": MIME_SHORTCUT,
                        "parents": ["folder-x"],
                        "shortcutDetails": {
                            "targetId": "eng-third-party",
                            "targetMimeType": "application/vnd.google-apps.jam",
                        },
                    },
                ]
            }
        ],
    )
    patch_drive_client(client)
    svc = _svc()
    _stub_probes(
        monkeypatch,
        svc,
        {"eng-doc": ShortcutProbe(accessible=True, target_drive_id=DriveId("engineering"))},
    )

    children = await svc.list_folder_children(FileId("folder-x"))
    ids = [c.id for c in children]
    assert ids == ["doc1", "sc-doc"]
    sc = next(c for c in children if c.id == "sc-doc")
    assert sc.shortcut_target_id == "eng-doc"
    assert sc.shortcut_target_drive_id == "engineering"


async def test_resolver_marks_forbidden_target_inaccessible(
    patch_drive_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        drives_pages=[{"drives": [{"id": "engineering", "name": "Engineering"}]}],
        files_pages=[
            {
                "files": [
                    {
                        "id": "sc",
                        "name": "Locked Folder",
                        "mimeType": MIME_SHORTCUT,
                        "parents": ["sales"],
                        "shortcutDetails": {
                            "targetId": "eng-locked",
                            "targetMimeType": MIME_FOLDER,
                        },
                    },
                ]
            }
        ],
    )
    patch_drive_client(client)
    svc = _svc()
    _stub_probes(monkeypatch, svc, {"eng-locked": ShortcutProbe(accessible=False)})

    tree = await svc.walk_drive_folders(DriveId("sales"))
    assert len(tree.folders) == 1
    assert tree.folders[0].shortcut_inaccessible is True
    assert tree.folders[0].shortcut_target_drive_id is None
    assert tree.folders[0].shortcut_target_drive_name is None


async def test_resolver_prunes_deleted_or_trashed_targets(
    patch_drive_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        drives_pages=[{"drives": [{"id": "engineering", "name": "Engineering"}]}],
        files_pages=[
            {
                "files": [
                    {
                        "id": "sc-gone",
                        "name": "Dead Shortcut",
                        "mimeType": MIME_SHORTCUT,
                        "parents": ["sales"],
                        "shortcutDetails": {
                            "targetId": "deleted-target",
                            "targetMimeType": MIME_FOLDER,
                        },
                    },
                    {
                        "id": "f1",
                        "name": "Real Folder",
                        "mimeType": MIME_FOLDER,
                        "parents": ["sales"],
                    },
                ]
            }
        ],
    )
    patch_drive_client(client)
    svc = _svc()
    _stub_probes(monkeypatch, svc, {"deleted-target": None})

    tree = await svc.walk_drive_folders(DriveId("sales"))
    assert [n.id for n in tree.folders] == ["f1"]


async def test_search_drive_escapes_quotes_and_passes_scoping(
    patch_drive_client: Any,
) -> None:
    client = _FakeClient(
        files_pages=[
            {
                "files": [
                    {
                        "id": "s1",
                        "name": "O'Brien Notes",
                        "mimeType": "application/vnd.google-apps.document",
                        "parents": ["folder-y"],
                        "driveId": "d1",
                    }
                ]
            }
        ]
    )
    patch_drive_client(client)
    results = await _svc().search_drive(DriveId("d1"), "O'Brien")
    assert [r.id for r in results] == ["s1"]
    call = next(call for call in client.files_captured if "q" in call)
    assert r"O\'Brien" in call["q"]
    assert call["corpora"] == "drive"
    assert call["driveId"] == "d1"


async def test_search_drive_empty_query_returns_empty(patch_drive_client: Any) -> None:
    client = _FakeClient()
    patch_drive_client(client)
    assert await _svc().search_drive(DriveId("d1"), "   ") == []
    assert client.files_captured == []


async def test_search_combines_shared_drives_with_owned_my_drive_results(
    patch_drive_client: Any,
) -> None:
    client = _FakeClient(
        drives_pages=[
            {"drives": [{"id": "d1", "name": "D1"}, {"id": "d2", "name": "D2"}]},
        ],
        files_pages=[
            {
                "files": [
                    {"id": "a", "name": "A", "mimeType": "x", "parents": ["d1"], "driveId": "d1"},
                    {"id": "b", "name": "B", "mimeType": "x", "parents": ["d2"], "driveId": "d2"},
                    {"id": "personal", "name": "P", "mimeType": "x", "parents": ["root"]},
                ]
            },
            {
                "files": [
                    {
                        "id": "personal",
                        "name": "P",
                        "mimeType": "x",
                        "parents": ["my-drive-root"],
                    }
                ]
            },
        ],
    )
    patch_drive_client(client)
    results = await _svc().search("q")
    assert {r.id for r in results} == {"a", "b", "personal"}
    personal = next(result for result in results if result.id == "personal")
    assert personal.drive_id == "my-drive-root"
    assert personal.drive_name == "My Drive"
    assert {r.drive_name for r in results} == {"D1", "D2", "My Drive"}
    search_calls = [call for call in client.files_captured if "q" in call]
    assert len(search_calls) == 2
    assert search_calls[0]["corpora"] == "allDrives"
    assert "driveId" not in search_calls[0]
    assert "incompleteSearch" in search_calls[0]["fields"]
    assert search_calls[1]["corpora"] == "user"
    assert "'me' in owners" in search_calls[1]["q"]


async def test_search_falls_back_to_per_drive_on_incomplete_results(
    patch_drive_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _FakeClient(
        drives_pages=[
            {"drives": [{"id": "d1", "name": "D1"}, {"id": "d2", "name": "D2"}]},
        ],
        files_pages=[{"incompleteSearch": True, "files": []}],
    )
    patch_drive_client(client)
    svc = _svc()

    def _search_one(
        drive_id: DriveId, _query: str, _limit: int, *, is_personal: bool = False
    ) -> list[SearchResult]:
        result_id = "personal" if is_personal else drive_id
        return [
            SearchResult(
                id=FileId(result_id),
                name=result_id,
                mime_type="x",
                drive_id=drive_id,
                drive_name=None,
                parent_id=None,
                modified_time=None,
            )
        ]

    monkeypatch.setattr(svc, "_search_drive_sync", _search_one)
    results = await svc.search("q")
    assert {r.id for r in results} == {"d1", "d2", "personal"}
    assert len([call for call in client.files_captured if "q" in call]) == 1


async def test_search_short_circuits_on_empty_query(patch_drive_client: Any) -> None:
    client = _FakeClient()
    patch_drive_client(client)
    assert await _svc().search("   ") == []
    assert client.drives_captured == []


async def test_probe_targets_dedupes_and_preserves_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _svc()
    seen: list[list[FileId]] = []

    def _fake(target_ids: list[FileId]) -> dict[FileId, ShortcutProbe | None]:
        seen.append(list(target_ids))
        return {
            tid: ShortcutProbe(accessible=True, target_drive_id=DriveId("d")) for tid in target_ids
        }

    monkeypatch.setattr(svc, "_probe_targets_sync", _fake)
    out = await svc._probe_targets([FileId("t1"), None, FileId("t2"), FileId("t1")])

    assert seen == [["t1", "t2"]]
    assert out[0] == ShortcutProbe(accessible=True, target_drive_id=DriveId("d"))
    assert out[1] is None
    assert out[3] == out[0]


async def test_probe_targets_serves_repeat_walks_from_the_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _svc()
    seen: list[list[FileId]] = []

    def _fake(target_ids: list[FileId]) -> dict[FileId, ShortcutProbe | None]:
        seen.append(list(target_ids))
        return {
            tid: ShortcutProbe(accessible=True, target_drive_id=DriveId("d")) for tid in target_ids
        }

    monkeypatch.setattr(svc, "_probe_targets_sync", _fake)
    first = await svc._probe_targets([FileId("t1"), FileId("t2")])
    second = await svc._probe_targets([FileId("t1"), FileId("t2")])
    third = await svc._probe_targets([FileId("t1"), FileId("t3")])

    assert seen == [["t1", "t2"], ["t3"]]
    assert second == first
    assert third[0] == first[0]


def test_classify_probe_maps_statuses_like_the_old_per_call_path() -> None:
    svc = _svc()

    def _err(status: int) -> Exception:
        from googleapiclient.errors import HttpError

        return HttpError(Response({"reason": "test", "status": str(status)}), b"{}")

    assert svc._classify_probe(None, _err(404)) is None
    assert svc._classify_probe(None, _err(403)) == ShortcutProbe(accessible=False)
    assert svc._classify_probe(None, _err(500)) is None
    assert svc._classify_probe({"trashed": True}, None) is None
    assert svc._classify_probe({"id": "t", "driveId": "eng"}, None) == ShortcutProbe(
        accessible=True, target_drive_id=DriveId("eng")
    )


async def test_snippet_with_known_mime_skips_metadata_get(
    patch_drive_client: Any,
) -> None:
    captured: list[dict[str, Any]] = []

    class _FakeFiles:
        def get(self, **_: Any) -> _FakeFiles:
            msg = "metadata get must be skipped when mime_type is supplied"
            raise AssertionError(msg)

        def export(self, **kwargs: Any) -> Self:
            captured.append({"_op": "export", **kwargs})
            return self

        def execute(self) -> bytes:
            return b"some text with a match inside it for the snippet"

    class _FakeSnippetClient:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    patch_drive_client(_FakeSnippetClient())
    result = await _svc().fetch_snippet(
        FileId("f1"), "match", mime_type="application/vnd.google-apps.document"
    )
    assert result.snippet is not None
    assert len(captured) == 1


async def test_snippet_with_non_exportable_mime_makes_no_drive_calls(
    patch_drive_client: Any,
) -> None:
    class _ExplodingClient:
        def files(self) -> Any:
            msg = "no Drive call expected for non-exportable types"
            raise AssertionError(msg)

    patch_drive_client(_ExplodingClient())
    result = await _svc().fetch_snippet(FileId("f1"), "match", mime_type="application/pdf")
    assert result.snippet is None
    assert result.mime_type == "application/pdf"


async def test_document_text_export_is_collapsed_and_bounded(
    patch_drive_client: Any,
) -> None:
    class _FakeFiles:
        def export(self, **_: Any) -> Self:
            return self

        def execute(self) -> bytes:
            return ("opening\n\n" + "private topic " * 2_000 + "closing").encode()

    class _FakeTextClient:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    patch_drive_client(_FakeTextClient())

    text = await _svc().fetch_document_text(FileId("f1"))

    assert text is not None
    assert text.startswith("opening private topic")
    assert text.endswith("closing")
    assert "[middle omitted]" in text
    assert len(text) < 12_100


def test_discovery_document_parsed_once() -> None:
    from app.drive.service import drive_discovery_document

    drive_discovery_document.cache_clear()
    first = drive_discovery_document()
    second = drive_discovery_document()
    assert first is second


def test_extract_snippet_basic_match() -> None:
    text = "The quick brown fox jumps over the lazy dog under the tree"
    result = extract_snippet(text, "brown", context_chars=10)
    assert result is not None
    snippet, word = result
    assert "brown" in snippet
    assert word == "brown"


def test_extract_snippet_case_insensitive() -> None:
    text = "Deploy the BACKEND to production on Friday"
    result = extract_snippet(text, "backend", context_chars=20)
    assert result is not None
    snippet, _ = result
    assert "BACKEND" in snippet


@pytest.mark.parametrize(
    ("text", "query", "context_chars", "expected_word"),
    [
        (
            "Alpha comes first then bravo appears here in the middle somewhere",
            "charlie bravo alpha",
            20,
            "Alpha",
        ),
        (
            "Longest word here is collaborator with many characters around it",
            "of collaborator",
            10,
            "collaborator",
        ),
    ],
    ids=["first-query-word-present", "skip-short-query-words"],
)
def test_extract_snippet_selects_query_word(
    text: str,
    query: str,
    context_chars: int,
    expected_word: str,
) -> None:
    result = extract_snippet(text, query, context_chars=context_chars)
    assert result is not None
    _, word = result
    assert word == expected_word


def test_extract_snippet_prefix_fallback_plural() -> None:
    text = "The project has one phase left before the launch is ready to go"
    result = extract_snippet(text, "phases", context_chars=20)
    assert result is not None
    snippet, word = result
    assert "phase" in snippet.lower()
    assert word.lower() == "phase"


def test_extract_snippet_no_match_returns_none() -> None:
    text = "There is nothing interesting in here at all"
    assert extract_snippet(text, "missing") is None


@pytest.mark.parametrize(
    ("text", "query", "expected", "snippet"),
    [
        ("abcd elsewhere abcdef then abcdef again", "abcdefghijk", "abcdef", "…abcdef…"),
        ("abcd elsewhere abcdefghijk", "abcdefghijk", "abcdefghijk", "…abcdefghijk"),
        ("abcd only", "abcdefghijk", "abcd", "abcd…"),
        ("unrelated content", "abcdefghijk", None, None),
        ("abc only", "abcd", None, None),
    ],
    ids=["longest-prefix", "exact-match", "minimum-prefix", "absent-prefix", "four-letters"],
)
def test_extract_snippet_preserves_longest_prefix_before_earliest_match(
    text: str,
    query: str,
    expected: str | None,
    snippet: str | None,
) -> None:
    result = extract_snippet(text, query, context_chars=0)
    assert (result.matched_word if result else None) == expected
    assert (result.text if result else None) == snippet


def test_extract_snippet_empty_inputs() -> None:
    assert extract_snippet("", "hello") is None
    assert extract_snippet("hello world", "") is None


def test_extract_snippet_collapses_whitespace_and_ellipsis() -> None:
    text = "prefix text       here   with     weird   spacing and a match token near the middle of"
    result = extract_snippet(text, "match", context_chars=15)
    assert result is not None
    snippet, _ = result
    assert "   " not in snippet
    assert snippet.startswith("…")
