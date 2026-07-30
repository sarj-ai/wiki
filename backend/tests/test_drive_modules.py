from __future__ import annotations

import builtins
from collections.abc import Callable
import threading
from typing import Any, Self, override
import uuid

import httplib2
import pytest

from app.drive import probe_cache
from app.drive.schemas import MIME_FOLDER, MIME_SHORTCUT, ShortcutProbe
from app.drive.service import GoogleDriveService
from app.drive.snippets import extract_snippet
from app.models.user import User


@pytest.fixture(autouse=True)
def _reset_probe_cache() -> None:
    probe_cache.clear()


def _user() -> User:
    u = User()
    u.id = uuid.uuid4()
    u.email = "test@sarj.ai"
    u.google_id = "google-test-id"
    u.token_version = 0
    return u


class _FakePendingCall:
    def __init__(self, page: dict[str, Any]) -> None:
        self._page = page

    def execute(self) -> dict[str, Any]:
        return self._page


class _FakeListable:
    def __init__(
        self, pages: builtins.list[dict[str, Any]], captured: builtins.list[dict[str, Any]]
    ) -> None:
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
        pages: builtins.list[dict[str, Any]],
        children_pages: builtins.list[dict[str, Any]],
        captured: builtins.list[dict[str, Any]],
    ) -> None:
        super().__init__(pages, captured)
        self._children = _FakeListable(children_pages, captured)

    @override
    def list(self, **kwargs: Any) -> _FakePendingCall:
        q = str(kwargs.get("q", ""))
        if "in parents" in q or q.startswith("trashed=false"):
            return self._children.list(**kwargs)
        return super().list(**kwargs)

    def get(self, **kwargs: Any) -> _FakePendingCall:
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
        children_pages: list[dict[str, Any]] | None = None,
    ) -> None:
        self.drives_captured: list[dict[str, Any]] = []
        self.files_captured: list[dict[str, Any]] = []
        self._drives = _FakeListable(drives_pages or [{}], self.drives_captured)
        self._files = _FakeFilesListable(
            files_pages or [{}], children_pages or [{}], self.files_captured
        )

    def drives(self) -> _FakeListable:
        return self._drives

    def files(self) -> _FakeFilesListable:
        return self._files


@pytest.fixture
def patch_drive_client(monkeypatch: pytest.MonkeyPatch) -> Callable[[Any], None]:

    def _install(client: Any) -> None:
        monkeypatch.setattr(
            GoogleDriveService,
            "build_drive_client",
            staticmethod(lambda _u: client),
        )

    return _install


def _svc() -> GoogleDriveService:
    return GoogleDriveService(_user())


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

    assert [d.id for d in result] == ["d1", "d3"]


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
    tree = await _svc().walk_drive_folders("d")
    assert [f.id for f in tree.folders] == ["f1", "f2"]
    walk_calls = [c for c in client.files_captured if c.get("corpora") == "drive"]
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

    tree = await _svc().walk_drive_folders("d")

    assert [f.id for f in tree.folders] == ["f1"]
    assert [f.id for f in tree.files] == ["doc1", "doc2"]
    assert tree.files[0].parent_id is None
    assert tree.files[1].parent_id == "f1"
    files_call = next(c for c in client.files_captured if c.get("q", "").startswith("trashed"))
    assert files_call["corpora"] == "drive"
    assert files_call["driveId"] == "d"


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

    tree = await _svc().walk_drive_folders("d")

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


def _stub_probes(
    monkeypatch: pytest.MonkeyPatch,
    svc: GoogleDriveService,
    mapping: dict[str, ShortcutProbe | None],
) -> None:

    def _fake(target_ids: list[str]) -> dict[str, ShortcutProbe | None]:
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
        {"eng-api-folder": ShortcutProbe(accessible=True, target_drive_id="engineering")},
    )

    tree = await svc.walk_drive_folders("sales")
    ids = [n.id for n in tree.folders]
    assert ids == ["f1", "sc-folder"]

    sc = next(n for n in tree.folders if n.id == "sc-folder")
    assert sc.mime_type == MIME_SHORTCUT
    assert sc.shortcut_target_id == "eng-api-folder"
    assert sc.shortcut_target_mime_type == MIME_FOLDER
    assert sc.shortcut_target_drive_id == "engineering"
    assert sc.shortcut_target_drive_name == "Engineering"
    assert sc.shortcut_inaccessible is False

    q = client.files_captured[0]["q"]
    assert f"mimeType='{MIME_FOLDER}'" in q
    assert f"mimeType='{MIME_SHORTCUT}'" in q
    assert "shortcutDetails(targetId,targetMimeType)" in client.files_captured[0]["fields"]


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
        {"eng-doc": ShortcutProbe(accessible=True, target_drive_id="engineering")},
    )

    children = await svc.list_folder_children("folder-x")
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

    tree = await svc.walk_drive_folders("sales")
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

    tree = await svc.walk_drive_folders("sales")
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
    results = await _svc().search_drive("d1", "O'Brien")
    assert [r.id for r in results] == ["s1"]
    call = client.files_captured[0]
    assert r"O\'Brien" in call["q"]
    assert call["corpora"] == "drive"
    assert call["driveId"] == "d1"


async def test_search_drive_empty_query_returns_empty(patch_drive_client: Any) -> None:
    client = _FakeClient()
    patch_drive_client(client)
    assert await _svc().search_drive("d1", "   ") == []
    assert client.files_captured == []


async def test_search_uses_single_all_drives_query(patch_drive_client: Any) -> None:
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
        ],
    )
    patch_drive_client(client)
    results = await _svc().search("q")
    assert {r.id for r in results} == {"a", "b"}
    assert {r.drive_name for r in results} == {"D1", "D2"}
    assert len(client.files_captured) == 1
    call = client.files_captured[0]
    assert call["corpora"] == "allDrives"
    assert "driveId" not in call
    assert "incompleteResults" in call["fields"]


async def test_search_falls_back_to_per_drive_on_incomplete_results(
    patch_drive_client: Any,
) -> None:
    client = _FakeClient(
        drives_pages=[
            {"drives": [{"id": "d1", "name": "D1"}, {"id": "d2", "name": "D2"}]},
        ],
        files_pages=[
            {"incompleteResults": True, "files": []},
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
    assert len(client.files_captured) == 3
    fan_out_corpora = {c["corpora"] for c in client.files_captured[1:]}
    assert fan_out_corpora == {"drive"}


async def test_search_short_circuits_on_empty_query(patch_drive_client: Any) -> None:
    client = _FakeClient()
    patch_drive_client(client)
    assert await _svc().search("   ") == []
    assert client.drives_captured == []


async def test_probe_targets_dedupes_and_preserves_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _svc()
    seen: list[list[str]] = []

    def _fake(target_ids: list[str]) -> dict[str, ShortcutProbe | None]:
        seen.append(list(target_ids))
        return {tid: ShortcutProbe(accessible=True, target_drive_id="d") for tid in target_ids}

    monkeypatch.setattr(svc, "_probe_targets_sync", _fake)
    out = await svc._probe_targets(["t1", None, "t2", "t1"])

    assert seen == [["t1", "t2"]]
    assert out[0] == ShortcutProbe(accessible=True, target_drive_id="d")
    assert out[1] is None
    assert out[3] == out[0]


async def test_probe_targets_serves_repeat_walks_from_the_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _svc()
    seen: list[list[str]] = []

    def _fake(target_ids: list[str]) -> dict[str, ShortcutProbe | None]:
        seen.append(list(target_ids))
        return {tid: ShortcutProbe(accessible=True, target_drive_id="d") for tid in target_ids}

    monkeypatch.setattr(svc, "_probe_targets_sync", _fake)
    first = await svc._probe_targets(["t1", "t2"])
    second = await svc._probe_targets(["t1", "t2"])
    third = await svc._probe_targets(["t1", "t3"])

    assert seen == [["t1", "t2"], ["t3"]]
    assert second == first
    assert third[0] == first[0]


def test_classify_probe_maps_statuses_like_the_old_per_call_path() -> None:
    svc = _svc()

    def _err(status: int) -> Exception:
        from googleapiclient.errors import HttpError

        resp = httplib2.Response({"status": str(status)})
        resp.reason = "test"
        return HttpError(resp, b"{}")

    assert svc._classify_probe("t", None, _err(404)) is None
    assert svc._classify_probe("t", None, _err(403)) == ShortcutProbe(accessible=False)
    assert svc._classify_probe("t", None, _err(500)) is None
    assert svc._classify_probe("t", {"trashed": True}, None) is None
    assert svc._classify_probe("t", {"id": "t", "driveId": "eng"}, None) == ShortcutProbe(
        accessible=True, target_drive_id="eng"
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
        "f1", "match", mime_type="application/vnd.google-apps.document"
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
    result = await _svc().fetch_snippet("f1", "match", mime_type="application/pdf")
    assert result.snippet is None
    assert result.mime_type == "application/pdf"


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
