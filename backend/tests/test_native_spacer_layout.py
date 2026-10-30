from copy import deepcopy
from dataclasses import replace
from typing import Any
import uuid

import pytest

from app.document_transform.brand import SarjBrandProfile, build_copy_mutations, paragraph_spacing
from app.document_transform.errors import InPlaceNeedsReviewError, OutputConflictError
from app.document_transform.executor import DocumentTransformExecutor
from app.document_transform.gateway import InPlaceInspection, SourceSnapshot
from app.document_transform.google_gateway import GoogleDocumentGateway, verify_brand_application
from app.document_transform.ir import parse_google_document, utf16_length
from app.document_transform.naming import title_case_document_name
from app.document_transform.native_pagination import apply_native_pagination, plan_native_pagination
from app.document_transform.schemas import DocumentTransformRequest, DriveFileSource, TransformMode
from app.identifiers import DocsRevisionId, FileId, TaskId
from tests.test_authored_labels import _snapshot
from tests.test_document_transform import (
    _branded_google_document,
    _CheckpointStore,
    _copy_executor,
    _CopyCheckpointStore,
    _Gateway,
    _Generator,
    _in_place_request,
)
from tests.test_native_pagination import _document


def _spaced_document() -> dict[str, Any]:
    native = _document()
    content = native["tabs"][0]["documentTab"]["body"]["content"]
    text = "Context 😀."
    end = 1 + utf16_length(text) + 1
    for item in content:
        item["startIndex"] += end
        item["endIndex"] += end
    content[:0] = [
        {
            "startIndex": 1,
            "endIndex": end,
            "paragraph": {
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "elements": [{"textRun": {"content": text + "\n", "textStyle": {}}}],
            },
        },
        {
            "startIndex": end,
            "endIndex": end + 1,
            "paragraph": {"elements": [{"textRun": {"content": "\n"}}]},
        },
    ]
    for item in content:
        cursor = item["startIndex"]
        for element in item["paragraph"]["elements"]:
            element["startIndex"] = cursor
            cursor += utf16_length(element["textRun"]["content"])
            element["endIndex"] = cursor
        assert cursor == item["endIndex"]
    return native


def test_spacer_proof_suppresses_only_generated_padding_and_preserves_heading_group() -> None:
    native = _spaced_document()
    original_native = deepcopy(native)
    original = parse_google_document(native, title="Layout")
    rules = plan_native_pagination(native, original)
    expected = apply_native_pagination(original, rules)
    assert [rule.keep_with_next for rule in rules] == [None, True, True]
    assert rules[0].below_spacer_sha256 == rules[1].above_spacer_sha256
    assert rules[0].below_spacer_sha256 is not None
    assert [paragraph_spacing(block, SarjBrandProfile()).above for block in expected.blocks] == [
        2,
        0,
        2,
        2,
    ]
    assert [paragraph_spacing(block, SarjBrandProfile()).below for block in expected.blocks] == [
        0,
        6,
        2,
        6,
    ]
    assert expected.semantic_sha256 == original.semantic_sha256
    assert expected.text_sha256 == original.text_sha256
    assert apply_native_pagination(expected, rules) == expected
    assert plan_native_pagination(native, expected) == rules
    requests = build_copy_mutations(
        original, expected, profile=SarjBrandProfile(), headers=(), logo_uri=""
    )
    updates = [item["updateParagraphStyle"] for item in requests if "updateParagraphStyle" in item]
    spacer_start = native["tabs"][0]["documentTab"]["body"]["content"][1]["startIndex"]
    assert all(
        not item["range"]["startIndex"] <= spacer_start < item["range"]["endIndex"]
        for item in updates
    )
    assert [item["paragraphStyle"]["keepWithNext"] for item in updates] == [
        False,
        True,
        True,
        False,
    ]
    assert native == original_native


def _styled_document() -> dict[str, Any]:
    native = _spaced_document()
    original = parse_google_document(native, title="Decision")
    expected = apply_native_pagination(original, plan_native_pagination(native, original))
    requests = build_copy_mutations(
        original, expected, profile=SarjBrandProfile(), headers=(), logo_uri=""
    )
    output = _branded_google_document()
    output["tabs"][0]["tabProperties"]["tabId"] = "tab-1"
    output["tabs"][0]["documentTab"]["body"] = deepcopy(native["tabs"][0]["documentTab"]["body"])
    content = output["tabs"][0]["documentTab"]["body"]["content"]
    paragraphs = {item["startIndex"]: item["paragraph"] for item in content}
    for item in requests:
        update = item.get("updateParagraphStyle", item.get("updateTextStyle"))
        if update is None:
            continue
        paragraph = paragraphs[update["range"]["startIndex"]]
        if "paragraphStyle" in update:
            paragraph["paragraphStyle"].update(update["paragraphStyle"])
        else:
            paragraph["elements"][0]["textRun"]["textStyle"].update(update["textStyle"])
    return output


@pytest.mark.parametrize("tamper", ["none", "above", "below"])
def test_shared_verifier_checks_the_exact_suppressed_spacing(tamper: str) -> None:
    native = _spaced_document()
    original = parse_google_document(native, title="Decision")
    expected = apply_native_pagination(original, plan_native_pagination(native, original))
    output = _styled_document()
    content = output["tabs"][0]["documentTab"]["body"]["content"]
    if tamper == "above":
        content[2]["paragraph"]["paragraphStyle"]["spaceAbove"]["magnitude"] = 14
    elif tamper == "below":
        content[0]["paragraph"]["paragraphStyle"]["spaceBelow"]["magnitude"] = 6
    assert content[1] == native["tabs"][0]["documentTab"]["body"]["content"][1]
    assert verify_brand_application(output, expected) is (tamper == "none")


@pytest.mark.parametrize("field", ["borderBottom", "shading", "indentStart", "unknown"])
@pytest.mark.parametrize("scope", ["document", "tab"])
def test_inherited_custom_styles_block_spacers_without_changing_keep_groups(
    field: str, scope: str
) -> None:
    native = _spaced_document()
    target = native if scope == "document" else native["tabs"][0]["documentTab"]
    target["namedStyles"] = {
        "styles": [{"namedStyleType": "NORMAL_TEXT", "paragraphStyle": {field: {}}}]
    }
    original = parse_google_document(native, title="Layout")
    rules = plan_native_pagination(native, original)
    assert [rule.keep_with_next for rule in rules] == [True, True]
    assert all(
        rule.above_spacer_sha256 is None and rule.below_spacer_sha256 is None for rule in rules
    )


@pytest.mark.parametrize("tamper", ["none", "spacing", "custom_spacer", "body_font"])
@pytest.mark.parametrize("unused_custom_style", [False, True])
@pytest.mark.parametrize("branded_naming", [False, True])
async def test_copy_to_in_place_is_noop_or_truthful_review_never_padding_reset(
    tamper: str, monkeypatch: pytest.MonkeyPatch, unused_custom_style: bool, branded_naming: bool
) -> None:
    document = _styled_document()
    if branded_naming:
        document["tabs"][0]["tabProperties"].update(
            title=title_case_document_name("decision"), iconEmoji="⚖️"
        )
        document["tabs"][0]["documentTab"]["headers"]["header-a"]["content"][0]["paragraph"][
            "elements"
        ][0]["textRun"]["content"] = "Decision — Sarj Polished\u00a0\u00a0\u00a0\u00a0\t"
    if unused_custom_style:
        document["namedStyles"] = {
            "styles": [{"namedStyleType": "HEADING_6", "paragraphStyle": {"shading": {}}}]
        }
    document["documentId"] = "source"
    content = document["tabs"][0]["documentTab"]["body"]["content"]
    if tamper == "spacing":
        content[2]["paragraph"]["paragraphStyle"]["spaceAbove"]["magnitude"] = 3
    elif tamper == "custom_spacer":
        content[1]["paragraph"]["paragraphStyle"] = {"borderBottom": {}}
    elif tamper == "body_font":
        content[0]["paragraph"]["elements"][0]["textRun"]["textStyle"]["weightedFontFamily"][
            "fontFamily"
        ] = "Arial"
    before = deepcopy(document)
    snapshot = replace(
        _snapshot(document),
        title="Decision — Sarj Polished" if branded_naming else "Decision",
        modified_time="2026-09-03T00:00:00Z",
        revision_id=DocsRevisionId("revision-2"),
        owned_by_me=True,
        can_edit=True,
        is_personal_drive=True,
    )
    gateway = _Gateway(snapshot)
    native_gateway = object.__new__(GoogleDocumentGateway)
    monkeypatch.setattr(
        native_gateway, "_logo_uri", "https://wiki.sarj.ai/brand/sarj-logo-light.png", raising=False
    )

    async def inspect(*, snapshot: SourceSnapshot, title: str) -> InPlaceInspection:
        return native_gateway._inspect_in_place_sync(snapshot, title)

    monkeypatch.setattr(gateway, "inspect_in_place", inspect)
    store = _CheckpointStore([])
    task_id = TaskId(uuid.uuid7())
    executor = DocumentTransformExecutor(
        gateway=gateway,
        generator=_Generator(),
        checkpoint_store=store,
        task_id=task_id,
        lease_token=uuid.uuid7(),
    )
    if tamper != "none":
        with pytest.raises(InPlaceNeedsReviewError):
            await executor.execute(
                operation_id=task_id, request=_in_place_request(TransformMode.FORMAT)
            )
    else:
        result = await executor.execute(
            operation_id=task_id, request=_in_place_request(TransformMode.FORMAT)
        )
        assert result.changed is False
        assert result.output.file_id == "source"
        assert result.output_revision_id == "revision-2"
        assert store.checkpoints[-1].pagination[0].below_spacer_sha256 is not None
        assert (
            await executor.execute(
                operation_id=task_id, request=_in_place_request(TransformMode.FORMAT)
            )
            == result
        )
    assert gateway.applies == 0
    assert document == before


@pytest.mark.parametrize(
    "change",
    [
        "gap",
        "utf16_width",
        "bool_index",
        "text",
        "two_runs",
        "link",
        "chip",
        "unknown",
        "paragraph_unknown",
        "border",
        "page_break",
        "heading",
        "neighbor_border",
        "neighbor_break",
        "section",
        "table",
        "duplicate_blank",
        "stale_text",
        "uneditable",
        "unknown_dimension",
        "bool_dimension",
        "nonfinite_dimension",
    ],
)
def test_ambiguous_spacers_never_produce_padding_rules(change: str) -> None:
    native = _spaced_document()
    content = native["tabs"][0]["documentTab"]["body"]["content"]
    spacer = content[1]
    paragraph = spacer["paragraph"]
    if change == "gap":
        spacer["startIndex"] += 1
    elif change == "utf16_width":
        content[0]["endIndex"] -= 1
    elif change == "bool_index":
        paragraph["elements"][0]["startIndex"] = True
    elif change == "text":
        paragraph["elements"][0]["textRun"]["content"] = " \n"
    elif change == "two_runs":
        paragraph["elements"].append({"textRun": {"content": ""}})
    elif change == "link":
        paragraph["elements"][0]["textRun"]["textStyle"] = {"link": {"url": "https://example.com"}}
    elif change == "chip":
        paragraph["elements"] = [
            {"dateElement": {"dateElementProperties": {"displayText": "Date"}}}
        ]
    elif change == "unknown":
        spacer["unknown"] = {}
    elif change == "paragraph_unknown":
        paragraph["unknown"] = {}
    elif change == "border":
        paragraph["paragraphStyle"] = {"borderBottom": {}}
    elif change == "page_break":
        paragraph["paragraphStyle"] = {"pageBreakBefore": True}
    elif change == "heading":
        paragraph["paragraphStyle"] = {"namedStyleType": "HEADING_1"}
    elif change == "neighbor_border":
        content[0]["paragraph"]["paragraphStyle"]["borderBottom"] = {}
    elif change == "neighbor_break":
        content[0]["paragraph"]["paragraphStyle"]["pageBreakBefore"] = True
    elif change in {"section", "table"}:
        del spacer["paragraph"]
        spacer["sectionBreak" if change == "section" else "table"] = {}
    elif change == "duplicate_blank":
        content.insert(1, deepcopy(spacer))
    elif change in {"unknown_dimension", "bool_dimension", "nonfinite_dimension"}:
        dimension: dict[str, Any] = {"magnitude": 2, "unit": "PT"}
        if change == "unknown_dimension":
            dimension["unknown"] = True
        else:
            dimension["magnitude"] = True if change == "bool_dimension" else float("inf")
        paragraph["paragraphStyle"] = {"spaceBelow": dimension}
    original = parse_google_document(native, title="Layout")
    if change in {"stale_text", "uneditable"}:
        original = replace(
            original,
            blocks=(
                replace(
                    original.blocks[0],
                    **({"text": "Changed"} if change == "stale_text" else {"editable": False}),
                ),
                *original.blocks[1:],
            ),
        )
    rules = plan_native_pagination(native, original)
    assert all(
        rule.above_spacer_sha256 is None and rule.below_spacer_sha256 is None for rule in rules
    )


@pytest.mark.parametrize("tamper", ["none", "missing", "digest", "old_version"])
async def test_spacer_checkpoint_recomputes_proof_before_replay(tamper: str) -> None:
    gateway = _Gateway(_snapshot(_spaced_document()))
    store = _CopyCheckpointStore()
    task_id = TaskId(uuid.uuid7())
    request = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file", file_id=FileId("source"), expected_modified_time="initial"
        ),
        mode=TransformMode.FORMAT,
    )
    result = await _copy_executor(gateway=gateway, store=store, task_id=task_id).execute(
        operation_id=task_id, request=request
    )
    checkpoint = store.checkpoints[-1]
    assert checkpoint.request_builder_version == "google-docs-requests-v26"
    assert checkpoint.pagination[0].below_spacer_sha256 is not None
    if tamper == "old_version":
        checkpoint = checkpoint.model_copy(
            update={"request_builder_version": "google-docs-requests-v21"}
        )
    elif tamper != "none":
        checkpoint = checkpoint.model_copy(
            update={
                "pagination": (
                    checkpoint.pagination[0].model_copy(
                        update={"below_spacer_sha256": None if tamper == "missing" else "0" * 64}
                    ),
                    *checkpoint.pagination[1:],
                )
            }
        )
    executor = _copy_executor(gateway=gateway, store=store, task_id=task_id, checkpoint=checkpoint)
    if tamper == "none":
        assert await executor.execute(operation_id=task_id, request=request) == result
    else:
        with pytest.raises(OutputConflictError):
            await executor.execute(operation_id=task_id, request=request)
    assert gateway.applies == 1
