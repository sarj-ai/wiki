from __future__ import annotations

from collections.abc import Callable
import json
import threading
from typing import TYPE_CHECKING, Any, NamedTuple, Self, override
from urllib.parse import parse_qs, urlsplit

from googleapiclient import discovery
from httplib2 import Response
from pydantic import ValidationError
import pytest

from app.drive.google_schemas import GoogleFile, GoogleFileAccessLocation
from app.drive.schemas import MIME_DOC, MIME_SLIDE
from app.drive.service import GoogleDriveService, drive_discovery_document, reuse_drive_client
from app.identifiers import FileId
from tests.support import dependency
from tests.test_provider_hot_paths import _MetadataFiles, _MetadataProvider, _user
from tests.test_provider_round3 import _CountingDrive, _SingleFiles, _SingleProvider


if TYPE_CHECKING:
    from googleapiclient._apis.drive.v3.resources import DriveResource

    from app.drive.service import GoogleRefreshHandler
    from app.models.user import User

_SHEET_MIME_TYPE = "application/vnd.google-apps.spreadsheet"


class _HttpResult(NamedTuple):
    # Google HTTP transport requires response/content tuple compatibility.
    response: Response
    content: bytes


def _json_response(payload: dict[str, object], *, status: int = 200) -> _HttpResult:
    return _HttpResult(Response({"status": str(status)}), json.dumps(payload).encode())


class _SearchTransport:
    def __init__(
        self, *, shared_drives: int = 3, files_per_drive: int = 1, incomplete: bool = False
    ) -> None:
        self.incomplete = incomplete
        self.shared_drives = shared_drives
        self.shared = [
            {
                "id": (
                    f"document-{index}"
                    if files_per_drive == 1
                    else f"document-{index}-{file_index}"
                ),
                "name": f"Document {index}",
                "mimeType": MIME_DOC,
                "parents": [f"drive-{index}"],
                "driveId": f"drive-{index}",
            }
            for index in range(shared_drives)
            for file_index in range(files_per_drive)
        ]
        self.owned = {"id": "owned", "name": "Owned", "mimeType": MIME_DOC, "parents": ["my-root"]}
        self.unowned = {
            "id": "shared-with-me",
            "name": "Outside My Drive",
            "mimeType": MIME_DOC,
            "parents": ["another-users-folder"],
        }
        self.owned_visible = True
        self.calls: list[dict[str, str]] = []
        self.inventory_calls: list[str] = []
        self.lock = threading.Lock()
        self.before_request: Callable[[], None] | None = None

    def _inventory_response(self, path: str) -> _HttpResult | None:
        if not path.endswith(("/files/root", "/drives")):
            return None
        with self.lock:
            self.inventory_calls.append(path)
        if path.endswith("/files/root"):
            return _json_response({"id": "my-root"})
        return _json_response(
            {
                "drives": [
                    {"id": f"drive-{index}", "name": f"Drive {index}"}
                    for index in range(self.shared_drives)
                ]
            }
        )

    def request(self, uri: str, _method: str, **_params: Any) -> _HttpResult:
        location = urlsplit(uri)
        inventory = self._inventory_response(location.path)
        if inventory is not None:
            return inventory
        params = {key: values[0] for key, values in parse_qs(location.query).items()}
        with self.lock:
            self.calls.append(params)
        if self.before_request is not None:
            self.before_request()
        result: dict[str, object] = {}
        match params["corpora"]:
            case "allDrives":
                top_level = params["fields"].split(",", 1)[0]
                if top_level not in drive_discovery_document()["schemas"]["FileList"]["properties"]:
                    return _json_response(
                        {"error": {"message": f"Invalid field selection {top_level}"}}, status=400
                    )
                files = [*self.shared, self.owned, self.unowned]
                result["incompleteSearch"] = self.incomplete
            case "user":
                assert "'me' in owners" in params["q"]
                files = [self.owned] if self.owned_visible else []
            case _:
                files = [file for file in self.shared if file["driveId"] == params["driveId"]]
        page_size = int(params["pageSize"])
        result["files"] = files[:page_size]
        # Google returns only fields requested by the caller.
        if len(files) > page_size and "nextPageToken" in params["fields"].split(","):
            result["nextPageToken"] = "more-results"
        return _json_response(result)


class _SearchDrive(GoogleDriveService):
    def __init__(self, transport: _SearchTransport) -> None:
        super().__init__(_user())
        self.transport = transport

    @override
    def _client(self) -> DriveResource:
        def build(_user: User, _refresh: GoogleRefreshHandler | None) -> DriveResource:
            client: DriveResource = dependency(discovery).build_from_document(
                drive_discovery_document(), http=self.transport
            )
            return client

        return reuse_drive_client(self._user, self._clients, client_factory=build)


@pytest.mark.parametrize("incomplete", [False, True], ids=["complete", "incomplete"])
async def test_search_uses_documented_completeness_and_retains_owned_personal_scope(
    incomplete: bool,
) -> None:
    transport = _SearchTransport(incomplete=incomplete)
    results = await _SearchDrive(transport).search("document", limit_per_drive=20)

    assert {result.id for result in results} == {"owned", "document-0", "document-1", "document-2"}
    assert next(result for result in results if result.id == "owned").drive_id == "my-root"
    all_drives = [call for call in transport.calls if call["corpora"] == "allDrives"]
    assert len(all_drives) == 1
    assert all_drives[0]["fields"].startswith("incompleteSearch,")
    assert all_drives[0]["pageSize"] == "50"
    personal = [call for call in transport.calls if call["corpora"] == "user"]
    assert len(personal) == 1
    assert personal[0]["pageSize"] == "20"
    assert "'me' in owners" in personal[0]["q"]
    shared = [call for call in transport.calls if call["corpora"] == "drive"]
    assert len(shared) == (3 if incomplete else 0)
    assert all(call["pageSize"] == "20" for call in shared)
    assert len(transport.inventory_calls) == 2


async def test_search_rereads_owned_personal_visibility() -> None:
    transport = _SearchTransport()
    drive = _SearchDrive(transport)
    assert "owned" in {result.id for result in await drive.search("document")}
    transport.owned_visible = False
    assert "owned" not in {result.id for result in await drive.search("document")}
    assert len(transport.calls) == 4


@pytest.mark.parametrize(
    ("shared_drives", "files_per_drive", "limit", "expected_calls"),
    [(3, 25, 20, 5), (2, 6, 3, 4)],
    ids=["all-drive-page-truncated", "complete-page-exceeds-per-drive-limit"],
)
async def test_search_preserves_each_drive_limit_and_does_not_starve_later_drives(
    shared_drives: int, files_per_drive: int, limit: int, expected_calls: int
) -> None:
    transport = _SearchTransport(shared_drives=shared_drives, files_per_drive=files_per_drive)
    results = await _SearchDrive(transport).search("document", limit_per_drive=limit)

    assert {result.id for result in results} == {
        "owned",
        *(
            f"document-{drive_index}-{file_index}"
            for drive_index in range(shared_drives)
            for file_index in range(limit)
        ),
    }
    assert len(transport.calls) == expected_calls
    assert len([call for call in transport.calls if call["corpora"] == "user"]) == 1
    assert all(
        call["pageSize"] == str(limit) for call in transport.calls if call["corpora"] != "allDrives"
    )


@pytest.mark.parametrize("count", [6, 100, 205])
def test_metadata_batches_build_one_resource_and_keep_each_fresh_get(count: int) -> None:
    class _Resources(_MetadataProvider):
        def __init__(self) -> None:
            super().__init__(
                {
                    FileId(f"file-{index}"): {"id": f"file-{index}", "driveId": "shared"}
                    for index in range(count)
                }
            )
            self.resources = 0

        @override
        def files(self) -> Self:
            self.resources += 1
            return self

    provider = _Resources()
    service = _MetadataFiles(_CountingDrive(), provider)
    records = service._get_accessible_records_many_sync(
        list(provider.records), fields="id,parents,driveId,trashed", model=GoogleFileAccessLocation
    )
    assert list(records) == list(provider.records)
    assert len(provider.requests) == count
    assert provider.resources == 1
    assert provider.batches == (count + 99) // 100


class _SnippetDrive(GoogleDriveService):
    def __init__(self, *, mime_type: str = MIME_DOC) -> None:
        super().__init__(_user())
        self.metadata_reads = 0
        self.exports = 0
        self.mime_type = mime_type

    @override
    def _client(self) -> DriveResource:
        class _Metadata:
            def __init__(self, owner: _SnippetDrive) -> None:
                self.owner = owner

            def files(self) -> _Metadata:
                return self

            def get(self, **_params: Any) -> _Metadata:
                self.owner.metadata_reads += 1
                return self

            def execute(self) -> dict[str, str]:
                return {"id": "document", "mimeType": self.owner.mime_type}

        client: DriveResource = dependency(_Metadata(self))
        return client

    @override
    def _export_text_sync(self, file_id: FileId) -> str:
        self.exports += 1
        return "Document ABcd İİ and documentation"


@pytest.mark.parametrize("query", ["a", "abc", "the doc", "\t\n abc İ a"])
@pytest.mark.parametrize("mime_type", [MIME_DOC, MIME_SLIDE, _SHEET_MIME_TYPE, None])
async def test_unmatchable_snippet_queries_skip_export_and_preserve_mime(
    query: str, mime_type: str | None
) -> None:
    drive = _SnippetDrive()
    result = await drive.fetch_snippet(FileId("document"), query, mime_type=mime_type)
    assert result.snippet is None
    assert result.match_term is None
    assert result.mime_type == (mime_type or MIME_DOC)
    assert drive.metadata_reads == (1 if mime_type is None else 0)
    assert drive.exports == 0


@pytest.mark.parametrize("query", ["ABcd", "a documentation", "documents", "İİ", "document " * 200])
async def test_matchable_snippet_terms_still_export(query: str) -> None:
    drive = _SnippetDrive()
    result = await drive.fetch_snippet(FileId("document"), query, mime_type=MIME_DOC)
    assert drive.exports == 1
    assert result.snippet is not None
    assert result.match_term is not None


@pytest.mark.parametrize("field", ["can_edit", "can_add_children"])
async def test_absent_capabilities_stay_immutable_and_do_not_alias_public_output(
    field: str,
) -> None:
    first = GoogleFile.model_validate({"id": "a", "name": "A", "mimeType": MIME_DOC})
    second = GoogleFile.model_validate({"id": "b", "name": "B", "mimeType": MIME_DOC})
    supplied = GoogleFile.model_validate(
        {"id": "c", "name": "C", "mimeType": MIME_DOC, "capabilities": {"canEdit": True}}
    )
    with pytest.raises(ValidationError, match="frozen_instance"):
        setattr(first.capabilities, field, True)
    serialized = first.model_dump()
    serialized["capabilities"]["can_edit"] = True
    files = _SingleFiles(
        _CountingDrive(),
        _SingleProvider(
            {"id": "document", "name": "Document", "mimeType": MIME_DOC, "driveId": "shared"}
        ),
    )
    public = await files.get(FileId("document"))
    public.can_edit = True
    assert not first.capabilities.can_edit
    assert not second.capabilities.can_edit
    assert supplied.capabilities.can_edit
    assert not supplied.capabilities.can_add_children
    assert not (await files.get(FileId("document"))).can_edit


@pytest.mark.parametrize(
    "capabilities", [None, {"canEdit": []}], ids=["null-capabilities", "invalid-can-edit"]
)
def test_provided_invalid_capabilities_are_still_validated(capabilities: Any) -> None:
    with pytest.raises(ValidationError):
        GoogleFile.model_validate(
            {"id": "a", "name": "A", "mimeType": MIME_DOC, "capabilities": capabilities}
        )
