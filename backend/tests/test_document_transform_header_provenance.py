from copy import deepcopy
from dataclasses import replace
from typing import Any, NamedTuple
import uuid

from pydantic import ValidationError
import pytest

from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.errors import InPlaceNeedsReviewError
from app.document_transform.gateway import SourceSnapshot
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.header_provenance import HeaderIdentityEvidence, HeaderIdentityProof
from app.document_transform.schemas import DriveFileSource
from app.files.schemas import FileResponse
from app.files.service import GoogleFilesService
from app.identifiers import DocsRevisionId, FileId
from app.models.user import User
from tests.test_document_transform import _branded_google_document, _pending_output_draft


class _Captured(NamedTuple):
    document: dict[str, Any]
    evidence: HeaderIdentityEvidence


def _snapshot(document: dict[str, Any]) -> SourceSnapshot:
    return SourceSnapshot(
        title="Decision — Sarj formatted (12345678)",
        parent_folder_id=FileId("folder"),
        modified_time="later",
        revision_id=DocsRevisionId(document["revisionId"]),
        media_type="application/vnd.google-apps.document",
        google_document=document,
        owned_by_me=True,
        can_edit=True,
        is_personal_drive=True,
    )


async def _capture(monkeypatch: pytest.MonkeyPatch) -> _Captured:
    document = _branded_google_document()
    document["documentId"] = "output"
    gateway = GoogleDocumentGateway(
        user=User(),
        files=object.__new__(GoogleFilesService),
        logo_uri=CURRENT_BRAND_MANIFEST.assets.logo_uri,
    )
    monkeypatch.setattr(gateway, "_get_document_sync", lambda _file_id: deepcopy(document))
    draft = replace(
        _pending_output_draft(pending_mutation_sha256="", mutation_base_revision_sha256=""),
        revision_id=DocsRevisionId(document["revisionId"]),
    )
    assert gateway.capture_header_identity is not None
    evidence = await gateway.capture_header_identity(
        draft=draft,
        operation_id=uuid.uuid7(),
        managed_headers={"tab-a": "header-a"},
        title="Decision",
    )
    assert evidence is not None
    return _Captured(document, evidence)


@pytest.mark.parametrize(
    "changed", ["body", "download_uri", "header", "logo", "file", "tab", "named_style"]
)
async def test_repeat_header_recognition_preserves_body_edits_but_requires_exact_identity(
    monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    document, evidence = await _capture(monkeypatch)
    before = deepcopy(document)
    tab = document["tabs"][0]["documentTab"]
    match changed:
        case "body":
            tab["body"]["content"][0]["paragraph"]["elements"][0]["textRun"]["content"] = (
                "Later body edit.\n"
            )
        case "download_uri":
            tab["inlineObjects"]["logo-1"]["inlineObjectProperties"]["embeddedObject"][
                "imageProperties"
            ]["contentUri"] = "https://temporary.example/new"
        case "header":
            tab["headers"]["header-a"]["content"][0]["paragraph"]["elements"][0]["textRun"][
                "content"
            ] = "Changed\t"
        case "logo":
            tab["inlineObjects"]["logo-1"]["inlineObjectProperties"]["embeddedObject"][
                "imageProperties"
            ]["sourceUri"] = "https://other.example/logo"
        case "file":
            document["documentId"] = "other-file"
        case "tab":
            document["tabs"][0]["tabProperties"]["tabId"] = "other-tab"
        case _:
            tab["namedStyles"] = {
                "styles": [{"namedStyleType": "NORMAL_TEXT", "textStyle": {"strikethrough": True}}]
            }
    document["revisionId"] = "later-body-revision"
    proof = HeaderIdentityProof(
        evidence=evidence, result_sha256="a" * 64, verified_revision_sha256="b" * 64
    )
    gateway = GoogleDocumentGateway(
        user=User(),
        files=object.__new__(GoogleFilesService),
        logo_uri=CURRENT_BRAND_MANIFEST.assets.logo_uri,
        header_identity=proof,
    )
    snapshot = _snapshot(document)
    if changed in {"body", "download_uri"}:
        inspection = await gateway.inspect_in_place(snapshot=snapshot, title=snapshot.title)
        assert inspection.unknown_header_tab_ids == ()
        assert len(inspection.preserved_headers) == 1
        assert inspection.header_identity == evidence
    else:
        with pytest.raises(InPlaceNeedsReviewError):
            await gateway.inspect_in_place(snapshot=snapshot, title=snapshot.title)
    assert "Decision" not in evidence.model_dump_json()
    assert before["documentId"] == evidence.file_id


async def test_unmanaged_headers_are_never_attested(monkeypatch: pytest.MonkeyPatch) -> None:
    document, _ = await _capture(monkeypatch)
    gateway = GoogleDocumentGateway(
        user=User(),
        files=object.__new__(GoogleFilesService),
        logo_uri=CURRENT_BRAND_MANIFEST.assets.logo_uri,
    )
    monkeypatch.setattr(gateway, "_get_document_sync", lambda _file_id: deepcopy(document))
    assert gateway.capture_header_identity is not None
    assert (
        await gateway.capture_header_identity(
            draft=_pending_output_draft(
                pending_mutation_sha256="", mutation_base_revision_sha256=""
            ),
            operation_id=uuid.uuid7(),
            managed_headers={},
            title="Decision",
        )
        is None
    )


def test_provenance_table_survives_task_expiry() -> None:
    from app.models.wiki import DocumentHeaderProvenance

    assert {
        foreign.target_fullname for foreign in DocumentHeaderProvenance.__table__.foreign_keys
    } == {"users.id"}


def test_unknown_evidence_version_is_rejected() -> None:
    with pytest.raises(ValidationError):
        HeaderIdentityEvidence.model_validate({"version": 3})


async def test_partial_managed_evidence_cannot_authorize_a_custom_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document, evidence = await _capture(monkeypatch)
    custom = deepcopy(document["tabs"][0])
    custom["tabProperties"]["tabId"] = "custom-tab"
    document["tabs"].append(custom)
    evidence = evidence.model_copy(update={"tab_ids": (*evidence.tab_ids, "custom-tab")})
    proof = HeaderIdentityProof(
        evidence=evidence, result_sha256="a" * 64, verified_revision_sha256="b" * 64
    )
    gateway = GoogleDocumentGateway(
        user=User(),
        files=object.__new__(GoogleFilesService),
        logo_uri=CURRENT_BRAND_MANIFEST.assets.logo_uri,
        header_identity=proof,
    )
    snapshot = _snapshot(document)
    with pytest.raises(InPlaceNeedsReviewError):
        await gateway.inspect_in_place(snapshot=snapshot, title=snapshot.title)


@pytest.mark.parametrize("change", ["none", "operation", "result", "revision", "state", "missing"])
async def test_fresh_metadata_matches_historical_finalization_not_current_body_revision(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    document, evidence = await _capture(monkeypatch)
    document["revisionId"] = "later-body-edit"
    proof = HeaderIdentityProof(
        evidence=evidence, result_sha256="a" * 64, verified_revision_sha256="b" * 64
    )
    properties = {
        "sarjTransformOperation": str(evidence.operation_id),
        "sarjTransformState": "complete",
        "sarjTransformResultHash": proof.result_sha256,
        "sarjTransformVerifiedRevisionHash": proof.verified_revision_sha256,
    }
    keys = {
        "operation": "sarjTransformOperation",
        "result": "sarjTransformResultHash",
        "revision": "sarjTransformVerifiedRevisionHash",
        "state": "sarjTransformState",
    }
    if change == "missing":
        properties.clear()
    elif change != "none":
        properties[keys[change]] = "forged"
    metadata = {
        "id": "output",
        "name": "Copy name",
        "mimeType": "application/vnd.google-apps.document",
        "parents": ["folder"],
        "modifiedTime": "later",
        "appProperties": properties,
        "ownedByMe": True,
        "capabilities": {"canEdit": True},
    }
    files = object.__new__(GoogleFilesService)

    async def get_file(file_id: FileId) -> FileResponse:
        return FileResponse(
            id=file_id,
            name="Copy name",
            mime_type="application/vnd.google-apps.document",
            parent_folder_id=FileId("folder"),
            drive_id=None,
            modified_time="later",
        )

    monkeypatch.setattr(files, "get", get_file)
    gateway = GoogleDocumentGateway(
        user=User(),
        files=files,
        logo_uri=CURRENT_BRAND_MANIFEST.assets.logo_uri,
        header_identity=proof,
    )
    monkeypatch.setattr(gateway, "_get_document_sync", lambda _file_id: deepcopy(document))
    monkeypatch.setattr(gateway, "_files_get_sync", lambda _file_id: deepcopy(metadata))
    source = DriveFileSource(
        kind="drive_file", file_id=FileId("output"), expected_modified_time="later"
    )
    if change == "none":
        snapshot = await gateway.load_drive_source(source)
        assert snapshot.revision_id == "later-body-edit"
    else:
        with pytest.raises(InPlaceNeedsReviewError):
            await gateway.load_drive_source(source)
