from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import uuid

from pydantic import JsonValue
import pytest

from app.document_transform.brand import SarjBrandProfile, build_copy_mutations
from app.document_transform.errors import OutputConflictError
from app.document_transform.google_gateway import verify_brand_application
from app.document_transform.ir import parse_google_document, utf16_length
from app.document_transform.native_pagination import apply_native_pagination, plan_native_pagination
from app.document_transform.readability_json import array, integer, obj
from app.document_transform.schemas import (
    DocumentTransformRequest,
    DriveFileSource,
    PlannedCopyCheckpoint,
    TransformMode,
)
from app.document_transform.unslop import UNSLOP_CATALOG_SHA256
from app.identifiers import FileId, TaskId
from tests.test_authored_labels import _snapshot
from tests.test_document_transform import (
    _branded_google_document,
    _copy_executor,
    _CopyCheckpointStore,
    _Gateway,
)


def _native(fields: int = 2) -> dict[str, JsonValue]:
    atoms: list[tuple[str, dict[str, JsonValue]]] = [
        (
            "When 🧭: ",
            {
                "dateElement": {
                    "dateId": "date-one",
                    "dateElementProperties": {
                        "displayText": "Nov 14, 2026",
                        "timestamp": "2026-11-14T00:00:00Z",
                    },
                }
            },
        ),
        (
            "Where: ",
            {
                "richLink": {
                    "richLinkId": "place-one",
                    "richLinkProperties": {
                        "title": "QA place",
                        "uri": "https://www.google.com/maps/place/QA/data=!4m2!3m1!19sqa-place",
                    },
                }
            },
        ),
        (
            "Owner: ",
            {
                "person": {
                    "personId": "person-one",
                    "personProperties": {"name": "QA Owner", "email": "qa-owner@example.invalid"},
                }
            },
        ),
        (
            "Source: ",
            {
                "richLink": {
                    "richLinkId": "file-one",
                    "richLinkProperties": {
                        "title": "QA reference",
                        "uri": "https://docs.google.com/document/d/qa-reference/edit",
                    },
                }
            },
        ),
    ]
    content: list[JsonValue] = []
    start = 1
    entries: list[tuple[str, dict[str, JsonValue] | None]] = [
        ("Event", None),
        *atoms[:fields],
        ("A factual description with an admission condition.", None),
    ]
    for label, atom in entries:
        elements: list[JsonValue] = []
        cursor = start
        parts: list[JsonValue] = (
            [{"textRun": {"content": label + "\n"}}]
            if atom is None
            else [{"textRun": {"content": label}}, atom, {"textRun": {"content": "\n"}}]
        )
        for source_element in parts:
            element = obj(deepcopy(source_element))
            width = (
                utf16_length(str(obj(element["textRun"])["content"])) if "textRun" in element else 1
            )
            elements.append({"startIndex": cursor, "endIndex": cursor + width, **element})
            cursor += width
        content.append(
            {
                "startIndex": start,
                "endIndex": cursor,
                "paragraph": {
                    "paragraphStyle": {
                        "namedStyleType": "HEADING_2" if not content else "NORMAL_TEXT"
                    },
                    "elements": elements,
                },
            }
        )
        start = cursor
    return {
        "revisionId": "source-revision-1",
        "tabs": [
            {"tabProperties": {"tabId": "tab-1"}, "documentTab": {"body": {"content": content}}}
        ],
    }


def _content(native: dict[str, JsonValue]) -> list[JsonValue]:
    tab = obj(array(native["tabs"])[0])
    return array(obj(obj(tab["documentTab"])["body"])["content"])


def _elements(value: JsonValue) -> list[JsonValue]:
    return array(obj(obj(value)["paragraph"])["elements"])


@pytest.mark.parametrize("fields", [1, 2, 3])
def test_metadata_groups_preserve_atoms_and_follow_actual_utf16_ranges(fields: int) -> None:
    native = _native(fields)
    original = parse_google_document(native, title="Review")
    rules = plan_native_pagination(native, original)
    expected = apply_native_pagination(original, rules)
    assert [rule.block_id for rule in rules] == [b.id for b in original.blocks[:-1]]
    assert all(rule.keep_with_next is True for rule in rules)
    assert expected.semantic_sha256 == original.semantic_sha256
    assert [b.native_content for b in expected.blocks] == [
        b.native_content for b in original.blocks
    ]
    requests = build_copy_mutations(
        original, expected, profile=SarjBrandProfile(), headers=(), logo_uri=""
    )
    assert not any(
        "insertText" in request or "deleteContentRange" in request for request in requests
    )
    native_ranges = [
        r["updateParagraphStyle"]["range"]
        for r in requests
        if r.get("updateParagraphStyle", {}).get("fields") == "keepWithNext,keepLinesTogether"
    ]
    assert native_ranges == [
        {
            "startIndex": b.source_range.start_index,
            "endIndex": b.source_range.end_index,
            "tabId": "tab-1",
        }
        for b in original.blocks[1:-1]
        if b.source_range is not None
    ]
    assert plan_native_pagination(native, expected) == rules
    assert plan_native_pagination(native, original, native_fields=False) == ()
    assert native == _native(fields)


@pytest.mark.parametrize(
    "boundary",
    [
        "blank",
        "section",
        "table",
        "unlabeled",
        "long_field",
        "long_body",
        "bad_width",
        "bad_gap",
        "unknown_atom",
        "page_break",
        "four_fields",
    ],
)
def test_native_field_groups_stop_at_unsupported_boundaries(boundary: str) -> None:
    native = _native(4 if boundary == "four_fields" else 2)
    content = _content(native)
    if boundary in {"blank", "section", "table"}:
        boundaries: dict[str, JsonValue] = {
            "blank": {"paragraph": {"elements": [{"textRun": {"content": "\n"}}]}},
            "section": {"sectionBreak": {}},
            "table": {"table": {"tableRows": []}},
        }
        content.insert(1, boundaries[boundary])
    elif boundary in {"unlabeled", "long_field"}:
        run = obj(_elements(content[1])[0])["textRun"]
        obj(run)["content"] = "During " if boundary == "unlabeled" else "x" * 121 + ": "
    elif boundary == "long_body":
        obj(obj(_elements(content[-1])[0])["textRun"])["content"] = "x" * 1_001 + "\n"
    elif boundary == "bad_width":
        obj(_elements(content[1])[1])["endIndex"] = (
            int(str(obj(_elements(content[1])[1])["endIndex"])) + 1
        )
    elif boundary == "bad_gap":
        obj(_elements(content[1])[1])["startIndex"] = (
            int(str(obj(_elements(content[1])[1])["startIndex"])) + 1
        )
    elif boundary == "unknown_atom":
        obj(_elements(content[1])[1])["inlineObjectElement"] = {"inlineObjectId": "unknown"}
    elif boundary == "page_break":
        obj(obj(obj(content[1])["paragraph"])["paragraphStyle"])["pageBreakBefore"] = True
    original = parse_google_document(native, title="Review")
    assert plan_native_pagination(native, original) == ()


@pytest.mark.parametrize("tamper", [False, True])
def test_native_group_readback_requires_both_exact_paragraph_flags(tamper: bool) -> None:
    native = _native()
    original = parse_google_document(native, title="Decision")
    expected = apply_native_pagination(original, plan_native_pagination(native, original))
    output = _branded_google_document()
    output["tabs"][0]["tabProperties"]["tabId"] = "tab-1"
    output["tabs"][0]["documentTab"]["body"] = {"content": deepcopy(_content(native))}
    content = _content(output)
    paragraphs = {integer(obj(item)["startIndex"]): obj(obj(item)["paragraph"]) for item in content}
    for request in build_copy_mutations(
        original, expected, profile=SarjBrandProfile(), headers=(), logo_uri=""
    ):
        update = request.get("updateParagraphStyle", request.get("updateTextStyle"))
        if update is None:
            continue
        paragraph = paragraphs[update["range"]["startIndex"]]
        if "paragraphStyle" in update:
            obj(paragraph["paragraphStyle"]).update(update["paragraphStyle"])
        else:
            obj(obj(array(paragraph["elements"])[0])["textRun"]).setdefault("textStyle", {})
            obj(obj(obj(array(paragraph["elements"])[0])["textRun"])["textStyle"]).update(
                update["textStyle"]
            )
    if tamper:
        obj(obj(obj(content[1])["paragraph"])["paragraphStyle"])["keepLinesTogether"] = False
    assert verify_brand_application(output, expected) is (not tamper)


async def test_completed_native_group_checkpoint_recovers_without_replaying_writes() -> None:
    gateway = _Gateway(_snapshot(_native()))
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
    assert "-native-fields-v1" in checkpoint.request_builder_version
    assert len(checkpoint.pagination) == 3
    previous = deepcopy(gateway.requests)
    assert (
        await _copy_executor(
            gateway=gateway, store=store, task_id=task_id, checkpoint=checkpoint
        ).execute(operation_id=task_id, request=request)
        == result
    )
    assert gateway.applies == 1
    assert gateway.requests == previous


async def test_compatible_legacy_copy_keeps_its_original_plan_after_upgrade() -> None:
    saved = json.loads(
        (Path(__file__).parent / "data/native_fields/legacy-planned-copy.json").read_text()
    )
    checkpoint = PlannedCopyCheckpoint.model_validate(saved["checkpoint"])
    gateway = _Gateway(_snapshot(saved["source"]))
    store = _CopyCheckpointStore()
    task_id = TaskId(uuid.UUID(saved["operation_id"]))
    request = DocumentTransformRequest.model_validate(saved["request"])
    executor = _copy_executor(gateway=gateway, store=store, task_id=task_id, checkpoint=checkpoint)
    if checkpoint.unslop_catalog_sha256 != UNSLOP_CATALOG_SHA256:
        with pytest.raises(OutputConflictError):
            await executor.execute(operation_id=task_id, request=request)
        assert gateway.applies == 0
        return
    result = await executor.execute(operation_id=task_id, request=request)
    assert checkpoint.pagination == ()
    assert "-native-fields-v1" not in checkpoint.request_builder_version
    assert not any(
        r.get("updateParagraphStyle", {}).get("fields") == "keepWithNext,keepLinesTogether"
        for r in gateway.requests
    )
    previous = deepcopy(gateway.requests)
    assert (
        await _copy_executor(
            gateway=gateway, store=store, task_id=task_id, checkpoint=store.checkpoints[-1]
        ).execute(operation_id=task_id, request=request)
        == result
    )
    assert gateway.applies == 1
    assert gateway.requests == previous


def test_metadata_stays_with_description_containing_a_native_date() -> None:
    native = _native()
    content = _content(native)
    body = obj(content[-1])
    start = integer(body["startIndex"])
    atom = deepcopy(obj(_elements(content[1])[1]))
    obj(atom["dateElement"])["dateId"] = "end-date"
    props = obj(obj(atom["dateElement"])["dateElementProperties"])
    props.update({"displayText": "Jan 10, 2027", "timestamp": "2027-01-10T00:00:00Z"})
    prefix = "Published through "
    middle = start + utf16_length(prefix)
    body["endIndex"] = middle + 3
    obj(body["paragraph"])["elements"] = [
        {"startIndex": start, "endIndex": middle, "textRun": {"content": prefix}},
        {**atom, "startIndex": middle, "endIndex": middle + 1},
        {"startIndex": middle + 1, "endIndex": middle + 3, "textRun": {"content": ".\n"}},
    ]
    original = parse_google_document(native, title="QA")
    rules = plan_native_pagination(native, original)
    assert len(rules) == 3
    assert apply_native_pagination(original, rules).semantic_sha256 == original.semantic_sha256


def test_identical_display_text_cannot_hide_another_native_object_identity() -> None:
    native = _native()
    original = parse_google_document(native, title="QA")
    block = original.blocks[2]
    atom = block.native_content[0]
    protected = json.loads(atom.payload_json)
    protected["element"]["richLinkId"] = "different-place-with-same-label"
    changed = replace(block, native_content=(replace(atom, payload_json=json.dumps(protected)),))
    stale = replace(
        original, blocks=tuple(changed if b.id == block.id else b for b in original.blocks)
    )
    assert [b.text for b in stale.blocks] == [b.text for b in original.blocks]
    assert stale.semantic_sha256 != original.semantic_sha256
    assert plan_native_pagination(native, stale) == ()


@pytest.fixture
def native_description_after_lead() -> dict[str, JsonValue]:
    native = _native(1)
    content = _content(native)
    heading, dated, lead = content
    obj(obj(_elements(dated)[0])["textRun"])["content"] = "A dated fact through "
    obj(obj(_elements(lead)[0])["textRun"]).update(
        {"content": "Important context.\n", "textStyle": {"bold": True}}
    )
    content[:] = [heading, lead, dated]
    start = 1
    for value in content:
        element = obj(value)
        element["startIndex"] = start
        for part_value in _elements(element):
            part = obj(part_value)
            width = utf16_length(str(obj(part["textRun"])["content"])) if "textRun" in part else 1
            part["startIndex"] = start
            start += width
            part["endIndex"] = start
        element["endIndex"] = start
    return native


async def test_native_description_after_plain_lead_pins_the_new_builder_before_recovery(
    native_description_after_lead: dict[str, JsonValue],
) -> None:
    native = native_description_after_lead
    original = parse_google_document(native, title="QA")
    current = plan_native_pagination(native, original)
    assert len(current) == 2
    assert all(not original.blocks[int(rule.block_id[1:])].native_content for rule in current)
    assert plan_native_pagination(native, original, native_fields=False) == ()
    gateway = _Gateway(_snapshot(native))
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
    assert "-native-fields-v1" in checkpoint.request_builder_version
    assert (
        await _copy_executor(
            gateway=gateway, store=store, task_id=task_id, checkpoint=checkpoint
        ).execute(operation_id=task_id, request=request)
        == result
    )
    assert gateway.applies == 1
