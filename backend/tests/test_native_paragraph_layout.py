from copy import deepcopy
from dataclasses import replace
from typing import Any
import uuid

import pytest

from app.document_transform.brand import SarjBrandProfile, build_copy_mutations
from app.document_transform.errors import (
    DocumentGatewayError,
    InPlaceNeedsReviewError,
    OutputConflictError,
)
from app.document_transform.executor import DocumentTransformExecutor
from app.document_transform.gateway import InPlaceStageResult, SourceSnapshot
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.ir import parse_google_document, utf16_length
from app.document_transform.native_paragraph_layout import (
    NativeParagraphLayout,
    apply_paragraph_layout,
    capture_paragraph_layout,
    paragraph_layout_requests_safe,
    verify_paragraph_layout,
)
from app.document_transform.schemas import (
    DocumentTransformRequest,
    DriveFileSource,
    OutputStrategy,
    TransformMode,
)
from app.identifiers import DocsRevisionId, FileId, TaskId
from tests.test_document_transform import (
    _branded_google_document,
    _CheckpointStore,
    _Gateway,
    _Generator,
)
from tests.test_document_transform_repeat import _inspection_snapshot


def _document(alignment: str = "CENTER") -> dict[str, Any]:
    document = _branded_google_document()
    document["documentId"] = "source"
    style = _paragraph(document)["paragraphStyle"]
    style.update(
        alignment=alignment,
        spaceAbove={"magnitude": 90, "unit": "PT"},
        spaceBelow={"magnitude": 18, "unit": "PT"},
        keepWithNext=True,
        keepLinesTogether=True,
    )
    return document


def _paragraph(document: dict[str, Any]) -> dict[str, Any]:
    return document["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]


@pytest.mark.parametrize("alignment", ["CENTER", "END", "JUSTIFIED"])
def test_exact_layout_proof_preserves_padding_and_ordinary_copy_policy(alignment: str) -> None:
    document = _document(alignment)
    original = parse_google_document(document, title="Decision")
    proof = capture_paragraph_layout(document, original, original)
    assert len(proof) == 1
    restored = tuple(
        NativeParagraphLayout.model_validate(item.model_dump(mode="json")) for item in proof
    )
    assert restored == proof
    assert verify_paragraph_layout(document, restored, applied=False)
    serialized = proof[0].model_dump_json()
    assert "Approved" not in serialized
    assert "paragraphStyle" not in serialized
    scoped = apply_paragraph_layout(original, proof)
    requests = build_copy_mutations(
        scoped, scoped, profile=SarjBrandProfile(), headers=(), logo_uri=""
    )
    assert not any("updateParagraphStyle" in item for item in requests)
    assert any("updateTextStyle" in item for item in requests)
    ordinary = build_copy_mutations(
        original, original, profile=SarjBrandProfile(), headers=(), logo_uri=""
    )
    assert any(
        item.get("updateParagraphStyle", {}).get("paragraphStyle", {}).get("alignment") == "START"
        for item in ordinary
    )
    assert _paragraph(document)["paragraphStyle"]["spaceAbove"]["magnitude"] == 90


@pytest.mark.parametrize("alignment", [None, "START", "ALIGNMENT_UNSPECIFIED", "unknown"])
def test_only_explicit_non_start_alignment_creates_layout_scope(alignment: str | None) -> None:
    document = _document()
    style = _paragraph(document)["paragraphStyle"]
    if alignment is None:
        style.pop("alignment")
    else:
        style["alignment"] = alignment
    parsed = parse_google_document(document, title="Decision")
    assert capture_paragraph_layout(document, parsed, parsed) == ()


@pytest.mark.parametrize(
    "field",
    [
        "alignment",
        "spaceAbove",
        "spaceBelow",
        "keepWithNext",
        "keepLinesTogether",
        "pageBreakBefore",
        "unknown",
    ],
)
def test_layout_proof_rejects_any_paragraph_style_change(field: str) -> None:
    document = _document()
    parsed = parse_google_document(document, title="Decision")
    proof = capture_paragraph_layout(document, parsed, parsed)
    _paragraph(document)["paragraphStyle"][field] = "tampered"
    assert not verify_paragraph_layout(document, proof)


@pytest.mark.parametrize("change", ["parent", "text", "bounds", "duplicate", "missing"])
def test_layout_proof_rejects_context_identity_and_text_tampering(change: str) -> None:
    document = _document()
    parsed = parse_google_document(document, title="Decision")
    proof = capture_paragraph_layout(document, parsed, parsed)
    match change:
        case "parent":
            document["namedStyles"] = {"styles": []}
        case "text":
            _paragraph(document)["elements"][0]["textRun"]["content"] = "Modified.\n"
        case "bounds":
            document["tabs"][0]["documentTab"]["body"]["content"][0]["startIndex"] = True
        case "duplicate":
            proof += proof
        case _:
            document["tabs"][0]["documentTab"]["body"]["content"] = []
    assert not verify_paragraph_layout(document, proof)


def test_layout_proof_tracks_utf16_replacements_and_complete_phase() -> None:
    document = _document()
    original = parse_google_document(document, title="Decision")
    transformed = original.replace_blocks({original.blocks[0].id: "Approved 😀."})
    proof = capture_paragraph_layout(document, original, transformed)
    assert verify_paragraph_layout(document, proof, applied=False)
    assert not verify_paragraph_layout(document, proof, applied=True)
    _paragraph(document)["elements"][0]["textRun"]["content"] = "Approved 😀.\n"
    document["tabs"][0]["documentTab"]["body"]["content"][0]["endIndex"] = 1 + utf16_length(
        "Approved 😀.\n"
    )
    assert verify_paragraph_layout(document, proof, applied=True)
    assert not verify_paragraph_layout(document, proof, applied=False)


def test_title_subtitle_status_layout_and_ordinary_body_are_distinct() -> None:
    document = _document()
    body = document["tabs"][0]["documentTab"]["body"]["content"]
    template = deepcopy(body[0])
    body.clear()
    cursor = 1
    for role, alignment in [
        ("TITLE", "CENTER"),
        ("SUBTITLE", "CENTER"),
        ("NORMAL_TEXT", "CENTER"),
        ("NORMAL_TEXT", "END"),
        ("NORMAL_TEXT", "START"),
    ]:
        element = deepcopy(template)
        element["startIndex"], element["endIndex"] = cursor, cursor + 10
        element["paragraph"]["paragraphStyle"].update(namedStyleType=role, alignment=alignment)
        body.append(element)
        cursor += 10
    original = parse_google_document(document, title="Decision")
    proof = capture_paragraph_layout(document, original, original)
    assert len(proof) == 4
    scoped = apply_paragraph_layout(original, proof)
    requests = build_copy_mutations(
        scoped, scoped, profile=SarjBrandProfile(), headers=(), logo_uri=""
    )
    paragraph_requests = [
        item["updateParagraphStyle"] for item in requests if "updateParagraphStyle" in item
    ]
    assert len(paragraph_requests) == 1
    assert paragraph_requests[0]["range"]["startIndex"] == 41
    assert paragraph_requests[0]["paragraphStyle"]["alignment"] == "START"
    assert verify_paragraph_layout(document, proof)
    assert not paragraph_layout_requests_safe(
        [{"updateParagraphStyle": {"range": {"startIndex": 1, "endIndex": 50, "tabId": "tab-a"}}}],
        proof,
    )


@pytest.mark.parametrize(
    "target",
    [
        None,
        {},
        {"startIndex": True, "endIndex": 9},
        {"startIndex": 1, "endIndex": 10},
        {"startIndex": 1, "endIndex": 10, "tabId": "tab-a"},
        {"startIndex": 1, "endIndex": 10, "segmentId": 1},
    ],
    ids=["missing", "empty", "boolean-start", "implicit-tab", "overlap", "invalid-segment"],
)
def test_layout_request_guard_rejects_ambiguous_or_overlapping_targets(target: object) -> None:
    document = _document()
    parsed = parse_google_document(document, title="Decision")
    proof = capture_paragraph_layout(document, parsed, parsed)
    assert not paragraph_layout_requests_safe([{"updateParagraphStyle": {"range": target}}], proof)


@pytest.mark.parametrize(
    "target",
    [
        {"startIndex": 11, "endIndex": 20, "tabId": "tab-a"},
        {"startIndex": 1, "endIndex": 10, "tabId": "other"},
        {"startIndex": 1, "endIndex": 10, "tabId": "tab-a", "segmentId": "header"},
    ],
    ids=["following-body", "other-tab", "header"],
)
def test_layout_request_guard_keeps_unrelated_paragraph_targets(target: dict[str, Any]) -> None:
    document = _document()
    parsed = parse_google_document(document, title="Decision")
    proof = capture_paragraph_layout(document, parsed, parsed)
    assert paragraph_layout_requests_safe([{"updateParagraphStyle": {"range": target}}], proof)


@pytest.mark.parametrize("field", ["alignment", "spaceAbove", "keepWithNext"])
async def test_public_verifier_rejects_changed_scoped_layout(field: str) -> None:
    document = _document()
    native = object.__new__(GoogleDocumentGateway)
    native._logo_uri = "https://example.test/logo.png"
    inspection = await native.inspect_in_place(
        snapshot=_inspection_snapshot(document), title="Decision"
    )
    original = parse_google_document(document, title="Decision")
    proof = capture_paragraph_layout(document, original, original)
    expected = apply_paragraph_layout(original, proof)
    good = await native.verify_in_place(
        document=document,
        title="Decision",
        expected=expected,
        structural_sha256=inspection.structural_sha256,
        protected_structure_sha256=inspection.protected_structure_sha256,
        verify_brand=True,
    )
    assert good.supported_brand_applied
    assert not good.brand_applied
    _paragraph(document)["paragraphStyle"][field] = "changed"
    bad = await native.verify_in_place(
        document=document,
        title="Decision",
        expected=expected,
        structural_sha256=inspection.structural_sha256,
        protected_structure_sha256=inspection.protected_structure_sha256,
        verify_brand=True,
    )
    assert not bad.supported_brand_applied
    assert not bad.brand_applied


@pytest.mark.parametrize("mode", [TransformMode.FORMAT, TransformMode.BOTH])
@pytest.mark.parametrize("outage", [False, True])
async def test_in_place_layout_preservation_retry_and_repeat(
    monkeypatch: pytest.MonkeyPatch, mode: TransformMode, *, outage: bool
) -> None:
    document = _document()
    _paragraph(document)["elements"][0]["textRun"]["textStyle"]["weightedFontFamily"][
        "fontFamily"
    ] = "Arial"
    original_style = deepcopy(_paragraph(document)["paragraphStyle"])
    native = object.__new__(GoogleDocumentGateway)
    native._logo_uri = "https://example.test/logo.png"
    gateway = _Gateway(_inspection_snapshot(document))
    store = _CheckpointStore([])
    writes: list[str] = []
    operation = uuid.uuid7()
    task_id = TaskId(uuid.uuid7())

    async def load(_source: DriveFileSource) -> SourceSnapshot:
        return replace(
            _inspection_snapshot(document), revision_id=DocsRevisionId(document["revisionId"])
        )

    async def apply(**kwargs: Any) -> InPlaceStageResult:
        assert not writes
        assert store.checkpoints[-1].paragraph_layout
        assert not any("updateParagraphStyle" in item for item in kwargs["requests"])
        _paragraph(document)["elements"][0]["textRun"]["textStyle"]["weightedFontFamily"][
            "fontFamily"
        ] = "Nunito"
        document["revisionId"] = "formatted"
        writes.append("content")
        if outage:
            raise DocumentGatewayError
        return InPlaceStageResult(DocsRevisionId("formatted"), deepcopy(document))

    async def retry_apply(**_kwargs: Any) -> InPlaceStageResult:
        return InPlaceStageResult(DocsRevisionId("formatted"), deepcopy(document))

    monkeypatch.setattr(gateway, "load_drive_source", load)
    monkeypatch.setattr(gateway, "inspect_in_place", native.inspect_in_place)
    monkeypatch.setattr(gateway, "verify_in_place", native.verify_in_place)
    monkeypatch.setattr(gateway, "apply_in_place_stage", apply)
    request = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file", file_id=FileId("source"), expected_modified_time="now"
        ),
        mode=mode,
        output_strategy=OutputStrategy.IN_PLACE,
    )

    def executor(checkpoint: Any = None) -> DocumentTransformExecutor:
        return DocumentTransformExecutor(
            gateway=gateway,
            generator=_Generator(),
            checkpoint_store=store,
            task_id=task_id,
            lease_token=uuid.uuid7(),
            checkpoint=checkpoint,
            checkpoint_generation=store.generation,
        )

    if outage:
        with pytest.raises(DocumentGatewayError):
            await executor().execute(operation_id=operation, request=request)
        monkeypatch.setattr(gateway, "apply_in_place_stage", retry_apply)
    result = await executor(store.checkpoints[-1] if outage else None).execute(
        operation_id=operation, request=request
    )
    assert result.changed
    assert not result.needs_review
    assert [finding.code for finding in result.findings] == ["native_paragraph_layout_preserved"]
    assert result.findings[0].severity.value == "informational"
    assert _paragraph(document)["paragraphStyle"] == original_style
    assert (
        await executor(store.checkpoints[-1]).execute(operation_id=operation, request=request)
        == result
    )
    again = await executor().execute(operation_id=uuid.uuid7(), request=request)
    assert not again.changed
    assert not again.needs_review
    assert writes == ["content"]
    missing = store.checkpoints[-1].model_copy(update={"paragraph_layout": ()})
    with pytest.raises(InPlaceNeedsReviewError):
        await executor(missing).execute(operation_id=operation, request=request)
    old = store.checkpoints[-1].model_copy(
        update={"request_builder_version": "google-docs-requests-v22"}
    )
    with pytest.raises(OutputConflictError):
        await executor(old).execute(operation_id=operation, request=request)


@pytest.mark.parametrize("outage", [False, True])
async def test_layout_evidence_survives_header_creation_and_recovery(
    monkeypatch: pytest.MonkeyPatch, *, outage: bool
) -> None:
    document = _document()
    tab = document["tabs"][0]["documentTab"]
    tab["headers"] = {}
    tab["documentStyle"].pop("defaultHeaderId")
    original_style = deepcopy(_paragraph(document)["paragraphStyle"])
    native = object.__new__(GoogleDocumentGateway)
    native._logo_uri = "https://example.test/logo.png"
    gateway = _Gateway(_inspection_snapshot(document))
    store = _CheckpointStore([])
    events: list[str] = []
    operation = uuid.uuid7()
    task_id = TaskId(uuid.uuid7())

    async def load(_source: DriveFileSource) -> SourceSnapshot:
        return replace(
            _inspection_snapshot(document), revision_id=DocsRevisionId(document["revisionId"])
        )

    async def apply(**kwargs: Any) -> InPlaceStageResult:
        assert store.checkpoints[-1].paragraph_layout
        assert paragraph_layout_requests_safe(
            kwargs["requests"], store.checkpoints[-1].paragraph_layout
        )
        if "createHeader" in kwargs["requests"][0]:
            if not events:
                tab["headers"] = {"header-a": {"content": []}}
                tab["documentStyle"]["defaultHeaderId"] = "header-a"
                document["revisionId"] = "headers"
                events.append("headers")
                if outage:
                    raise DocumentGatewayError
        else:
            tab["headers"] = deepcopy(
                _branded_google_document()["tabs"][0]["documentTab"]["headers"]
            )
            document["revisionId"] = "formatted"
            events.append("content")
        return InPlaceStageResult(DocsRevisionId(document["revisionId"]), deepcopy(document))

    monkeypatch.setattr(gateway, "load_drive_source", load)
    monkeypatch.setattr(gateway, "inspect_in_place", native.inspect_in_place)
    monkeypatch.setattr(gateway, "verify_in_place", native.verify_in_place)
    monkeypatch.setattr(gateway, "apply_in_place_stage", apply)
    request = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file", file_id=FileId("source"), expected_modified_time="now"
        ),
        mode=TransformMode.FORMAT,
        output_strategy=OutputStrategy.IN_PLACE,
    )

    def executor(checkpoint: Any = None) -> DocumentTransformExecutor:
        return DocumentTransformExecutor(
            gateway=gateway,
            generator=_Generator(),
            checkpoint_store=store,
            task_id=task_id,
            lease_token=uuid.uuid7(),
            checkpoint=checkpoint,
            checkpoint_generation=store.generation,
        )

    if outage:
        with pytest.raises(DocumentGatewayError):
            await executor().execute(operation_id=operation, request=request)
    result = await executor(store.checkpoints[-1] if outage else None).execute(
        operation_id=operation, request=request
    )
    assert result.changed
    assert not result.needs_review
    assert all(finding.severity.value == "informational" for finding in result.findings)
    assert events == ["headers", "content"]
    assert all(
        item.paragraph_layout == store.checkpoints[0].paragraph_layout for item in store.checkpoints
    )
    assert _paragraph(document)["paragraphStyle"] == original_style
