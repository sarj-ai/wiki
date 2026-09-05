from copy import deepcopy
from dataclasses import replace
from typing import Any
import uuid

import pytest

from app.document_transform.gateway import OutputDraft, OutputVerification
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.ir import parse_google_document
from app.document_transform.native_date_flow import plan_copy_dates
from app.identifiers import FileId
from tests.test_document_transform import _branded_google_document, _pending_output_draft
from tests.test_document_transform_footer_flow import _plan
from tests.test_document_transform_header_provenance import _snapshot
from tests.test_document_transform_native_date_flow import _converted, _document


def _rich(start: int = 1, *, formatted: bool = False) -> dict[str, Any]:
    parts: list[dict[str, Any]] = [
        {
            "startIndex": start,
            "endIndex": start + 6,
            "textRun": {"content": "Alpha ", "textStyle": {"bold": True}},
        },
        {
            "startIndex": start + 6,
            "endIndex": start + 10,
            "textRun": {"content": "Beta", "textStyle": {"bold": False}},
        },
        {
            "startIndex": start + 10,
            "endIndex": start + 11,
            "textRun": {"content": "\n", "textStyle": {"bold": False}},
        },
    ]
    if formatted:
        for part in parts[:-1]:
            part["textRun"]["textStyle"]["weightedFontFamily"] = {"fontFamily": "Nunito"}
    return {"startIndex": start, "endIndex": start + 11, "paragraph": {"elements": parts}}


def _source(*, formatted: bool = False) -> dict[str, Any]:
    document = _branded_google_document()
    document["documentId"] = "source"
    document["tabs"][0]["documentTab"]["body"] = {"content": [_rich(formatted=formatted)]}
    return document


async def _verify(
    monkeypatch: pytest.MonkeyPatch,
    source: dict[str, Any],
    actual: dict[str, Any],
    draft: OutputDraft | None = None,
) -> OutputVerification:
    gateway = object.__new__(GoogleDocumentGateway)
    monkeypatch.setattr(gateway, "_get_document_sync", lambda _file_id: deepcopy(actual))
    return await gateway.verify_output(
        draft=draft
        or _pending_output_draft(
            pending_mutation_sha256="hash", mutation_base_revision_sha256="hash"
        ),
        source=_snapshot(source),
        expected=parse_google_document(source, title="Decision"),
        verify_brand=True,
        header_title="Decision",
    )


@pytest.mark.parametrize("style", ["bold", "italic", "underline", "unknownStyle"])
async def test_public_copy_verification_rejects_non_font_style_loss(
    monkeypatch: pytest.MonkeyPatch, style: str
):
    source, actual = _source(), _source(formatted=True)
    actual["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["elements"][0]["textRun"][
        "textStyle"
    ][style] = False
    result = await _verify(monkeypatch, source, actual)
    assert result.content_preserved
    assert result.brand_applied
    assert not result.structure_preserved


async def test_public_copy_verification_allows_exact_font_override_fragmentation_and_repeat(
    monkeypatch: pytest.MonkeyPatch,
):
    source, actual = _source(), _source(formatted=True)
    parts = actual["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["elements"]
    second = deepcopy(parts[0])
    second["startIndex"] = 3
    second["textRun"]["content"] = "pha "
    parts[0]["endIndex"] = 3
    parts[0]["textRun"]["content"] = "Al"
    parts.insert(1, second)
    result = await _verify(monkeypatch, source, actual)
    assert result.content_preserved
    assert result.structure_preserved
    assert result.brand_applied
    assert await _verify(monkeypatch, source, actual) == result


async def test_copy_does_not_treat_partial_font_ranges_as_full_override_permission(
    monkeypatch: pytest.MonkeyPatch,
):
    source, actual = _source(), _source(formatted=True)
    source["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["elements"][0]["textRun"][
        "textStyle"
    ]["weightedFontFamily"] = {"fontFamily": "Courier New"}
    block = parse_google_document(source, title="Decision").blocks[0]
    assert block.font_style_ranges
    assert block.font_style_ranges[0].start_index == 7
    result = await _verify(monkeypatch, source, actual)
    assert not result.structure_preserved


@pytest.mark.parametrize("change", ["none", "bold", "family", "fragmentation"])
async def test_copy_zero_font_plain_text_requires_exact_styles(
    monkeypatch: pytest.MonkeyPatch, change: str
):
    source = _source()
    parts = source["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["elements"]
    for part in parts:
        part["textRun"]["textStyle"]["weightedFontFamily"] = {"fontFamily": "Courier New"}
    block = parse_google_document(source, title="Decision").blocks[0]
    assert not block.editable
    assert not block.font_style_ranges
    actual = deepcopy(source)
    actual_parts = actual["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["elements"]
    if change == "bold":
        actual_parts[0]["textRun"]["textStyle"]["bold"] = False
    elif change == "family":
        actual_parts[0]["textRun"]["textStyle"]["weightedFontFamily"] = {"fontFamily": "Nunito"}
    elif change == "fragmentation":
        remainder = deepcopy(actual_parts[0])
        actual_parts[0]["endIndex"] = 3
        actual_parts[0]["textRun"]["content"] = "Al"
        remainder["startIndex"] = 3
        remainder["textRun"]["content"] = "pha "
        actual_parts.insert(1, remainder)
    result = await _verify(monkeypatch, source, actual)
    assert result.structure_preserved is (change in {"none", "fragmentation"})


@pytest.mark.parametrize("tamper", ["none", "definition", "style"])
async def test_copy_list_identity_requires_complete_bijection_and_exact_non_font_styles(
    monkeypatch: pytest.MonkeyPatch, tamper: str
):
    source, actual = _source(), _source(formatted=True)
    for document, list_id in [(source, "source-list"), (actual, "copy-list")]:
        tab = document["tabs"][0]["documentTab"]
        tab["body"]["content"][0]["paragraph"]["bullet"] = {"listId": list_id, "nestingLevel": 0}
        tab["lists"] = {list_id: {"listId": list_id}}
    if tamper == "definition":
        actual["tabs"][0]["documentTab"]["lists"]["copy-list"]["unexpected"] = True
    elif tamper == "style":
        actual["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["elements"][0][
            "textRun"
        ]["textStyle"]["bold"] = False
    result = await _verify(monkeypatch, source, actual)
    assert result.structure_preserved is (tamper == "none")


async def test_copy_font_proof_runs_after_exact_native_date_projection(
    monkeypatch: pytest.MonkeyPatch,
):
    source, actual = _document(), _converted()
    source["tabs"][0]["documentTab"]["body"]["content"][2] = _rich(18)
    before_date = deepcopy(source)
    before_date["tabs"][0]["documentTab"]["body"]["content"][2] = _rich(18, formatted=True)
    actual["tabs"][0]["documentTab"]["body"]["content"][2] = _rich(9, formatted=True)
    checkpoint = plan_copy_dates(before_date, operation_id=uuid.uuid7())
    assert checkpoint is not None
    draft = replace(
        _pending_output_draft(pending_mutation_sha256="hash", mutation_base_revision_sha256="hash"),
        native_dates=checkpoint,
    )
    result = await _verify(monkeypatch, source, actual, draft)
    assert result.content_preserved
    assert result.structure_preserved


async def test_copy_font_proof_runs_after_exact_native_footer_projection(
    monkeypatch: pytest.MonkeyPatch,
):
    source, actual = _source(), _source(formatted=True)
    actual["revisionId"] = "r1"
    gateway, initial, _provider = await _plan(monkeypatch, actual)
    created = await gateway.apply_copy_footer_stage(document_id=FileId("doc"), checkpoint=initial)
    complete = await gateway.apply_copy_footer_stage(document_id=FileId("doc"), checkpoint=created)
    draft = replace(
        _pending_output_draft(pending_mutation_sha256="hash", mutation_base_revision_sha256="hash"),
        native_footer=complete,
    )
    result = await _verify(monkeypatch, source, actual, draft)
    assert result.content_preserved
    assert result.structure_preserved
    assert result.brand_applied
