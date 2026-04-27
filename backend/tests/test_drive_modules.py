"""DriveService + snippet helper tests.

``GoogleDriveService`` is exercised with a fake ``DriveResource`` stubbed
in via ``GoogleDriveService.build_drive_client``. The fake records every
kwarg the service passes so we can assert the q-string, corpora scoping,
and pagination semantics that matter for correctness.

The pure snippet helpers have no Drive dependency; they're tested with
plain string inputs.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.drive.schemas import MIME_FOLDER
from app.drive.service import GoogleDriveService
from app.drive.snippets import extract_snippet
from app.models.user import User


def _user() -> User:
    u = User()
    u.id = uuid.uuid4()
    u.email = "test@sarj.ai"
    u.google_id = "google-test-id"
    u.token_version = 0
    return u


class _FakeListable:
    def __init__(self, pages: list[dict[str, Any]], captured: list[dict[str, Any]]) -> None:
        self._pages = pages
        self._captured = captured
        self._idx = 0

    def list(self, **kwargs: Any) -> _FakeListable:
        self._captured.append(dict(kwargs))
        self._pending = self._pages[self._idx]
        self._idx += 1
        return self

    def execute(self) -> dict[str, Any]:
        return self._pending


class _FakeFilesListable(_FakeListable):
    def get(self, **kwargs: Any) -> _FakeFilesListable:
        self._captured.append({"_op": "get", **kwargs})
        self._pending = self._pages[self._idx]
        self._idx += 1
        return self

    def update(self, **kwargs: Any) -> _FakeFilesListable:
        self._captured.append({"_op": "update", **kwargs})
        self._pending = self._pages[self._idx]
        self._idx += 1
        return self

    def create(self, **kwargs: Any) -> _FakeFilesListable:
        self._captured.append({"_op": "create", **kwargs})
        self._pending = self._pages[self._idx]
        self._idx += 1
        return self


class _FakeClient:
    def __init__(
        self,
        *,
        drives_pages: list[dict[str, Any]] | None = None,
        files_pages: list[dict[str, Any]] | None = None,
    ) -> None:
        self.drives_captured: list[dict[str, Any]] = []
        self.files_captured: list[dict[str, Any]] = []
        self._drives = _FakeListable(drives_pages or [{}], self.drives_captured)
        self._files = _FakeFilesListable(files_pages or [{}], self.files_captured)

    def drives(self) -> _FakeListable:
        return self._drives

    def files(self) -> _FakeFilesListable:
        return self._files


@pytest.fixture
def patch_drive_client(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Return a helper that patches ``GoogleDriveService.build_drive_client``."""

    def _install(client: _FakeClient) -> None:
        monkeypatch.setattr(
            GoogleDriveService,
            "build_drive_client",
            staticmethod(lambda _u: client),
        )

    return _install


def _svc() -> GoogleDriveService:
    return GoogleDriveService(_user())


# ---------------------------------------------------------------------------
# list_drives
# ---------------------------------------------------------------------------


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

    assert [d.id for d in result] == ["d1", "d2"]
    assert [d.name for d in result] == ["Engineering", "Marketing"]
    assert len(client.drives_captured) == 2
    assert client.drives_captured[1].get("pageToken") == "t1"


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
    assert [d.id for d in result] == ["d1"]


# ---------------------------------------------------------------------------
# walk_drive_folders + list_folder_children
# ---------------------------------------------------------------------------


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

    tree = await _svc().walk_drive_folders("the-drive")
    assert tree.drive_id == "the-drive"
    assert [f.id for f in tree.folders] == ["f1", "f2"]
    # Root-level folder is normalized: parent_id None, not the drive id.
    assert tree.folders[0].parent_id is None
    assert tree.folders[1].parent_id == "f1"

    call = client.files_captured[0]
    assert call["corpora"] == "drive"
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
    tree = await _svc().walk_drive_folders("d")
    assert [f.id for f in tree.folders] == ["f1", "f2"]
    assert client.files_captured[1].get("pageToken") == "NEXT"


async def test_list_folder_children_queries_by_parent(patch_drive_client: Any) -> None:
    client = _FakeClient(
        files_pages=[
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
    children = await _svc().list_folder_children("folder-x")
    assert [c.id for c in children] == ["doc1"]
    assert children[0].parent_id == "folder-x"
    call = client.files_captured[0]
    assert "'folder-x' in parents" in call["q"]
    assert call["corpora"] == "allDrives"


# ---------------------------------------------------------------------------
# search_drive + search
# ---------------------------------------------------------------------------


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
    results = await _svc().search_drive("d1", "O'Brien")
    assert [r.id for r in results] == ["s1"]
    call = client.files_captured[0]
    # Single-quote escaped as backslash-quote in Drive q-string.
    assert r"O\'Brien" in call["q"]
    assert call["corpora"] == "drive"
    assert call["driveId"] == "d1"


async def test_search_drive_empty_query_returns_empty(patch_drive_client: Any) -> None:
    client = _FakeClient()
    patch_drive_client(client)
    assert await _svc().search_drive("d1", "   ") == []
    # No API calls were made.
    assert client.files_captured == []


async def test_search_fans_out_across_member_drives(patch_drive_client: Any) -> None:
    client = _FakeClient(
        drives_pages=[
            {"drives": [{"id": "d1", "name": "D1"}, {"id": "d2", "name": "D2"}]},
        ],
        files_pages=[
            {
                "files": [
                    {"id": "a", "name": "A", "mimeType": "x", "parents": ["d1"], "driveId": "d1"}
                ]
            },
            {
                "files": [
                    {"id": "b", "name": "B", "mimeType": "x", "parents": ["d2"], "driveId": "d2"}
                ]
            },
        ],
    )
    patch_drive_client(client)
    results = await _svc().search("q")
    assert {r.id for r in results} == {"a", "b"}


async def test_search_short_circuits_on_empty_query(patch_drive_client: Any) -> None:
    client = _FakeClient()
    patch_drive_client(client)
    assert await _svc().search("   ") == []
    # Never even fetched drives.
    assert client.drives_captured == []


# ---------------------------------------------------------------------------
# snippet extraction — pure function, no Drive calls
# ---------------------------------------------------------------------------


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


def test_extract_snippet_picks_first_word_with_match() -> None:
    text = "Alpha comes first then bravo appears here in the middle somewhere"
    result = extract_snippet(text, "charlie bravo alpha", context_chars=20)
    assert result is not None
    _, word = result
    # alpha occurs at index 0 (as "Alpha"), bravo later, charlie never —
    # so alpha wins. matched_word preserves the original casing.
    assert word == "Alpha"


def test_extract_snippet_prefix_fallback_plural() -> None:
    # Drive's full-text search stems "phases" → "phase"; we mirror that.
    text = "The project has one phase left before the launch is ready to go"
    result = extract_snippet(text, "phases", context_chars=20)
    assert result is not None
    snippet, word = result
    assert "phase" in snippet.lower()
    # The prefix that actually matched is the shortened "phase".
    assert word.lower() == "phase"


def test_extract_snippet_skips_short_words() -> None:
    text = "Longest word here is collaborator with many characters around it"
    result = extract_snippet(text, "of collaborator", context_chars=10)
    assert result is not None
    _, word = result
    assert word == "collaborator"


def test_extract_snippet_no_match_returns_none() -> None:
    text = "There is nothing interesting in here at all"
    assert extract_snippet(text, "missing") is None


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
