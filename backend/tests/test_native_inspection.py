from dataclasses import replace

from httpx import ASGITransport, AsyncClient
import pytest

from app.auth.dependencies import auth_service_dep, get_current_user
from app.document_transform.dependencies import CurrentPolishRuntime, current_polish_runtime_dep
from app.document_transform.errors import SourceChangedError
from app.document_transform.gateway import SourceSnapshot
from app.document_transform.inspection import inspect_native_document
from app.document_transform.schemas import DriveFileSource
from app.identifiers import DocsRevisionId, FileId
from app.main import app
from tests.support import dependency
from tests.test_document_readability import _fixture
from tests.test_files_service import _user


class _Reader:
    def __init__(self) -> None:
        self.calls: list[DriveFileSource] = []
        self.snapshot = SourceSnapshot(
            title="Private native fixture",
            parent_folder_id=FileId("private-folder"),
            modified_time="2026-11-12T13:00:00Z",
            revision_id=DocsRevisionId("native-revision"),
            media_type="application/vnd.google-apps.document",
            google_document={**_fixture("before"), "revisionId": "native-revision"},
        )

    async def load_drive_source(self, source: DriveFileSource) -> SourceSnapshot:
        self.calls.append(source)
        return self.snapshot


def _source() -> DriveFileSource:
    return DriveFileSource(
        kind="drive_file", file_id=FileId("private"), expected_modified_time="2026-11-12T13:00:00Z"
    )


async def test_native_inspection_preserves_provider_payload_and_binds_requested_revision() -> None:
    reader = _Reader()
    result = await inspect_native_document(_source(), reader=reader, account_email="owner@sarj.ai")
    assert result.account_email == "owner@sarj.ai"
    assert result.revision_id == "native-revision"
    assert result.document == reader.snapshot.google_document
    reader.snapshot = replace(reader.snapshot, modified_time="2026-11-12T14:00:00Z")
    with pytest.raises(SourceChangedError):
        await inspect_native_document(_source(), reader=reader, account_email="owner@sarj.ai")


async def test_native_inspection_requires_wiki_auth_and_disables_response_caching() -> None:
    reader = _Reader()
    runtime = CurrentPolishRuntime(
        files=dependency(reader), revisions=dependency(reader), native_sources=reader
    )
    app.dependency_overrides[current_polish_runtime_dep] = lambda: runtime
    app.dependency_overrides[auth_service_dep] = lambda: None
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            denied = await client.post("/document-transforms/inspect", json=_source().model_dump())
            assert denied.status_code == 401
            assert not reader.calls
            app.dependency_overrides[get_current_user] = _user
            response = await client.post(
                "/document-transforms/inspect", json=_source().model_dump()
            )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        assert response.json()["document"] == reader.snapshot.google_document
        assert reader.calls == [_source()]
    finally:
        app.dependency_overrides.pop(current_polish_runtime_dep, None)
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(auth_service_dep, None)
