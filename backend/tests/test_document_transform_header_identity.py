from copy import deepcopy
from dataclasses import replace
from typing import Any
import uuid

from loguru import logger
import pytest

from app.document_transform import google_gateway as google_gateway_module
from app.document_transform.brand import SarjBrandProfile, build_copy_mutations
from app.document_transform.brand_manifest import HEADER_TITLE_SEPARATOR
from app.document_transform.gateway import SourceSnapshot, TabHeader
from app.document_transform.google_gateway import GoogleDocumentGateway, verify_brand_application
from app.document_transform.ir import DocumentIR, parse_google_document
from app.document_transform.schemas import DocumentTransformRequest, DriveFileSource, TransformMode
from app.identifiers import DocsRevisionId, DocsSegmentId, DocsTabId, FileId, TaskId
from tests.test_document_transform import (
    _branded_google_document,
    _copy_executor,
    _CopyCheckpointStore,
    _Gateway,
    _in_place_document,
    _pending_output_draft,
)


def _source() -> dict[str, Any]:
    document = _in_place_document("Platform reliability plan", revision="source-revision")
    document["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["paragraphStyle"] = {
        "namedStyleType": "HEADING_1"
    }
    return document


@pytest.mark.parametrize(
    "title", ["Platform reliability plan", "خطة العمل 🛰️"], ids=["english", "unicode"]
)
def test_copy_header_override_is_explicit_and_does_not_change_source_identity(title: str) -> None:
    original = parse_google_document(_source(), title="Untitled document")
    transformed = replace(original, title="Untitled document")
    headers = (TabHeader(tab_id=DocsTabId("tab-a"), header_id=DocsSegmentId("header-a")),)
    profile = SarjBrandProfile()
    logo_uri = "https://example.test/logo.png"
    # Default remains the authored source identity for in-place callers.
    default = build_copy_mutations(
        original, transformed, profile=profile, headers=headers, logo_uri=logo_uri
    )
    copied = build_copy_mutations(
        original,
        transformed,
        header_title=title,
        profile=profile,
        headers=headers,
        logo_uri=logo_uri,
    )
    assert any(
        item.get("insertText", {}).get("text") == "Untitled document" + HEADER_TITLE_SEPARATOR
        for item in default
    )
    assert any(
        item.get("insertText", {}).get("text") == title + HEADER_TITLE_SEPARATOR for item in copied
    )
    assert original.title == transformed.title == "Untitled document"
    assert copied == build_copy_mutations(
        original,
        transformed,
        header_title=title,
        profile=profile,
        headers=headers,
        logo_uri=logo_uri,
    )
    assert not any(
        item.get("insertText", {}).get("location", {}).get("segmentId")
        for item in build_copy_mutations(
            original,
            transformed,
            profile=SarjBrandProfile(),
            headers=(),
            logo_uri="https://example.test/logo.png",
            header_title=title,
        )
    )


def test_header_verification_override_does_not_weaken_in_place_identity() -> None:
    document = _branded_google_document()
    parsed = parse_google_document(document, title="Untitled document")
    assert not verify_brand_application(document, parsed)
    assert verify_brand_application(document, parsed, expected_header_title="Decision")
    assert not verify_brand_application(document, parsed, expected_header_title="Different title")
    assert parsed.title == "Untitled document"


async def test_custom_header_preflight_preserves_privacy() -> None:
    document = _branded_google_document()
    document["documentId"] = "private-document"
    gateway = object.__new__(GoogleDocumentGateway)
    gateway._logo_uri = "https://private.example/secret-logo"
    snapshot = SourceSnapshot(
        title="Decision — Sarj formatted (12345678)",
        parent_folder_id=FileId("private-folder"),
        modified_time="private-time",
        revision_id=DocsRevisionId("private-revision"),
        media_type="application/vnd.google-apps.document",
        google_document=document,
        owned_by_me=True,
        can_edit=True,
        is_personal_drive=True,
    )
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)), format="{message}")
    try:
        inspection = await gateway.inspect_in_place(snapshot=snapshot, title=snapshot.title)
    finally:
        logger.remove(sink)
    assert not inspection.unknown_header_tab_ids
    assert inspection.custom_furniture
    assert not inspection.headers
    assert not inspection.preserved_headers
    assert not inspection.fully_branded
    logged = "\n".join(messages)
    assert "document in-place preflight" in logged
    assert "'unknown_header_count': 0" in logged
    assert "'in_place_logo_available': True" in logged
    assert "Decision" not in logged
    assert "private" not in logged
    assert "12345678" not in logged


async def test_copy_auto_title_and_header_agree_and_retry_keeps_identity() -> None:
    source = _source()
    original = deepcopy(source)
    gateway = _Gateway(
        SourceSnapshot(
            title="Untitled document",
            parent_folder_id=FileId("folder"),
            modified_time="before",
            revision_id=DocsRevisionId("source-revision"),
            media_type="application/vnd.google-apps.document",
            google_document=source,
        )
    )
    store = _CopyCheckpointStore()
    task_id = TaskId(uuid.uuid7())
    request = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file", file_id=FileId("source"), expected_modified_time="before"
        ),
        mode=TransformMode.FORMAT,
    )
    result = await _copy_executor(gateway=gateway, store=store, task_id=task_id).execute(
        operation_id=task_id, request=request
    )
    title = "Platform Reliability Plan — Sarj Formatted"
    assert result.output.name == title
    assert any(
        item.get("insertText", {}).get("text") == title + HEADER_TITLE_SEPARATOR
        for item in gateway.requests
    )
    assert set(gateway.verified_header_titles) == {title}
    assert {checkpoint.source_title for checkpoint in store.checkpoints} == {"Untitled document"}
    repeated = await _copy_executor(
        gateway=gateway, store=store, task_id=task_id, checkpoint=store.checkpoints[-1]
    ).execute(operation_id=task_id, request=request)
    assert repeated.output == result.output
    assert gateway.applies == 1
    assert source == original


@pytest.mark.parametrize("header_title", [None, "Chosen Title — Sarj Formatted"])
async def test_copy_gateway_verifies_header_once_against_supplied_durable_title(
    monkeypatch: pytest.MonkeyPatch,
    header_title: str | None,
) -> None:
    document = _source()
    gateway = object.__new__(GoogleDocumentGateway)
    monkeypatch.setattr(gateway, "_get_document_sync", lambda _file_id: deepcopy(document))
    observed: list[str | None] = []

    def capture(
        _document: dict[str, Any], _parsed: DocumentIR, *, expected_header_title: str | None = None
    ) -> bool:
        observed.append(expected_header_title)
        return True

    monkeypatch.setattr(google_gateway_module, "verify_brand_application", capture)
    source = SourceSnapshot(
        title="Untitled document",
        parent_folder_id=FileId("folder"),
        modified_time="before",
        revision_id=DocsRevisionId("source-revision"),
        media_type="application/vnd.google-apps.document",
        google_document=document,
    )
    expected = parse_google_document(document, title=source.title)
    # A provider collision name cannot override the supplied durable header title.
    draft = _pending_output_draft(
        pending_mutation_sha256="hash", mutation_base_revision_sha256="hash"
    )
    draft = replace(
        draft, output=draft.output.model_copy(update={"name": "Generated — Sarj formatted"})
    )
    for _ in range(2):
        await gateway.verify_output(
            draft=draft,
            source=source,
            expected=expected,
            verify_brand=True,
            header_title=header_title,
        )
    # Each output readback performs exactly one brand-verifier pass.
    assert observed == [header_title or "Platform reliability plan"] * 2
