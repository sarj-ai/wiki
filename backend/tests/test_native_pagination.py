from copy import deepcopy
from dataclasses import replace
from typing import Any
import uuid

import pytest

from app.document_transform.brand import SarjBrandProfile, build_copy_mutations
from app.document_transform.errors import OutputConflictError
from app.document_transform.google_gateway import verify_brand_application
from app.document_transform.ir import parse_google_document, utf16_length
from app.document_transform.native_pagination import apply_native_pagination, plan_native_pagination
from app.document_transform.schemas import (
    DocumentTransformCheckpoint,
    DocumentTransformRequest,
    DriveFileSource,
    TransformMode,
)
from app.identifiers import FileId, TaskId
from tests.test_authored_labels import _snapshot
from tests.test_document_transform import (
    _branded_google_document,
    _copy_executor,
    _CopyCheckpointStore,
    _Gateway,
)


def _document(*, listing: bool = False) -> dict[str, Any]:
    content = []
    start = 1
    entries = (
        [
            ("Behaviors", "HEADING_2", True),
            ("First item", "NORMAL_TEXT", False),
            ("Second item", "NORMAL_TEXT", False),
            ("Third item", "NORMAL_TEXT", False),
        ]
        if listing
        else [
            ("Scalability", "HEADING_1", True),
            ("Leave capacity behind.", "NORMAL_TEXT", True),
            ("Build reusable tools and teach others.", "NORMAL_TEXT", False),
        ]
    )
    for index, (text, style, bold) in enumerate(entries):
        end = start + utf16_length(text) + 1
        paragraph: dict[str, Any] = {
            "paragraphStyle": {"namedStyleType": style},
            "elements": [{"textRun": {"content": text + "\n", "textStyle": {"bold": bold}}}],
        }
        if listing and index:
            paragraph["bullet"] = {"listId": "list-one", "nestingLevel": 0}
        content.append({"startIndex": start, "endIndex": end, "paragraph": paragraph})
        start = end
    return {
        "revisionId": "source-revision-1",
        "tabs": [
            {"tabProperties": {"tabId": "tab-1"}, "documentTab": {"body": {"content": content}}}
        ],
    }


@pytest.mark.parametrize("listing", [False, True])
def test_bounded_groups_preserve_content_and_end_before_the_rest_of_list(listing: bool) -> None:
    native = _document(listing=listing)
    original = parse_google_document(native, title="Review")
    rules = plan_native_pagination(native, original)
    expected = apply_native_pagination(original, rules)
    assert [rule.keep_with_next for rule in rules] == (
        [True, True, False] if listing else [True, True]
    )
    assert (
        tuple(replace(block, pagination_keep_with_next=None) for block in expected.blocks)
        == original.blocks
    )
    assert expected.semantic_sha256 == original.semantic_sha256
    assert expected.text_sha256 == original.text_sha256
    assert apply_native_pagination(expected, rules) == expected
    assert plan_native_pagination(native, expected) == rules
    requests = build_copy_mutations(
        original, expected, profile=SarjBrandProfile(), headers=(), logo_uri=""
    )
    styles = [item["updateParagraphStyle"] for item in requests if "updateParagraphStyle" in item]
    assert [item["paragraphStyle"]["keepWithNext"] for item in styles] == (
        [True, True, False, False] if listing else [True, True, False]
    )
    assert [item["paragraphStyle"].get("keepLinesTogether") for item in styles] == (
        [True, True, True, None] if listing else [True, True, None]
    )
    assert native == _document(listing=listing)


def test_unplanned_native_formatting_retains_existing_layout_policy() -> None:
    native = _document(listing=True)
    original = parse_google_document(native, title="Review")
    requests = build_copy_mutations(
        original, original, profile=SarjBrandProfile(), headers=(), logo_uri=""
    )
    styles = [
        item["updateParagraphStyle"]["paragraphStyle"]
        for item in requests
        if "updateParagraphStyle" in item
    ]
    assert [item["keepWithNext"] for item in styles] == [True, False, False, False]
    assert all("keepLinesTogether" not in item for item in styles)


def test_negative_native_ranges_cannot_establish_pagination_provenance() -> None:
    native = _document()
    for element in native["tabs"][0]["documentTab"]["body"]["content"]:
        element["startIndex"] -= 1_000
        element["endIndex"] -= 1_000
    original = parse_google_document(native, title="Review")
    assert plan_native_pagination(native, original) == ()


@pytest.mark.parametrize(
    "boundary",
    [
        "blank",
        "section",
        "table",
        "hardbreak",
        "tab",
        "child",
        "furniture",
        "page_before",
        "inherited_page_before",
        "pageless",
        "malformed_mode",
        "unknown_element",
        "uneditable",
        "long_tagline",
        "gap",
    ],
)
def test_raw_boundaries_and_unsupported_groups_are_never_inferred_away(boundary: str) -> None:
    native = _document()
    original = parse_google_document(native, title="Review")
    tab = native["tabs"][0]
    body = tab["documentTab"]
    content = body["body"]["content"]
    match boundary:
        case "blank" | "section" | "table":
            inserted = {
                "blank": {"paragraph": {"elements": [{"textRun": {"content": "\n"}}]}},
                "section": {"sectionBreak": {}},
                "table": {"table": {"tableRows": []}},
            }[boundary]
            content.insert(1, inserted)
        case "hardbreak":
            content[1]["paragraph"]["elements"].append({"pageBreak": {}})
        case "tab":
            native["tabs"].append(deepcopy(tab))
        case "child":
            tab["childTabs"] = [deepcopy(tab)]
        case "furniture":
            body["headers"] = {"header": body.pop("body")}
        case "page_before":
            content[2]["paragraph"]["paragraphStyle"]["pageBreakBefore"] = True
        case "inherited_page_before":
            body["namedStyles"] = {"styles": [{"paragraphStyle": {"pageBreakBefore": True}}]}
        case "pageless" | "malformed_mode":
            native["documentStyle"] = {
                "documentFormat": {"documentMode": "PAGELESS" if boundary == "pageless" else {}}
            }
        case "unknown_element":
            content[1]["unknownBoundary"] = {}
        case "uneditable":
            original = replace(
                original,
                blocks=(
                    original.blocks[0],
                    replace(original.blocks[1], editable=False),
                    original.blocks[2],
                ),
            )
        case "long_tagline":
            original = original.replace_blocks({original.blocks[1].id: "x" * 121})
        case _:
            content[1]["startIndex"] += 1
    assert plan_native_pagination(native, original) == ()


@pytest.mark.parametrize(
    "change", ["identity", "nesting", "missing_identity", "oversized", "one_item"]
)
def test_list_groups_need_two_short_same_identity_same_level_items(change: str) -> None:
    native = _document(listing=True)
    content = native["tabs"][0]["documentTab"]["body"]["content"]
    if change == "identity":
        content[2]["paragraph"]["bullet"]["listId"] = "other"
    elif change == "nesting":
        content[2]["paragraph"]["bullet"]["nestingLevel"] = 1
    elif change == "missing_identity":
        for item in content[1:]:
            item["paragraph"]["bullet"] = {}
    elif change == "one_item":
        del content[2:]
    original = parse_google_document(native, title="Review")
    if change == "oversized":
        original = original.replace_blocks({original.blocks[1].id: "x" * 241})
    assert plan_native_pagination(native, original) == ()


@pytest.mark.parametrize("tamper", ["none", "keep_next", "keep_lines", "text"])
def test_verifier_checks_exact_pagination_expectations(tamper: str) -> None:
    native = _document()
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
    paragraph = content[1]["paragraph"]
    if tamper == "keep_next":
        paragraph["paragraphStyle"]["keepWithNext"] = False
    elif tamper == "keep_lines":
        paragraph["paragraphStyle"].pop("keepLinesTogether")
    elif tamper == "text":
        changed = original.replace_blocks({original.blocks[1].id: "Changed."})
        with pytest.raises(OutputConflictError):
            apply_native_pagination(changed, plan_native_pagination(native, original))
        return
    assert verify_brand_application(output, expected) is (tamper == "none")


@pytest.mark.parametrize("mode", [TransformMode.FORMAT, TransformMode.BOTH, TransformMode.UNSLOP])
@pytest.mark.parametrize("tamper", ["none", "missing", "old_version", "wrong_flag"])
async def test_copy_checkpoint_attests_rules_and_never_replays_body(
    mode: TransformMode, tamper: str
) -> None:
    gateway = _Gateway(_snapshot(_document()))
    store = _CopyCheckpointStore()
    task_id = TaskId(uuid.uuid7())
    request = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file", file_id=FileId("source"), expected_modified_time="initial"
        ),
        mode=mode,
    )
    result = await _copy_executor(gateway=gateway, store=store, task_id=task_id).execute(
        operation_id=task_id, request=request
    )
    checkpoint = store.checkpoints[-1]
    assert len(checkpoint.pagination) == (0 if mode is TransformMode.UNSLOP else 2)
    if tamper == "missing" and mode is not TransformMode.UNSLOP:
        checkpoint = checkpoint.model_copy(update={"pagination": ()})
    elif tamper == "old_version":
        checkpoint = checkpoint.model_copy(
            update={"request_builder_version": "google-docs-requests-v11"}
        )
    elif tamper == "wrong_flag" and checkpoint.pagination:
        checkpoint = checkpoint.model_copy(
            update={
                "pagination": (
                    checkpoint.pagination[0].model_copy(update={"keep_with_next": False}),
                    *checkpoint.pagination[1:],
                )
            }
        )
    executor = _copy_executor(gateway=gateway, store=store, task_id=task_id, checkpoint=checkpoint)
    if tamper == "old_version" or (
        tamper in {"missing", "wrong_flag"} and mode is not TransformMode.UNSLOP
    ):
        with pytest.raises(OutputConflictError):
            await executor.execute(operation_id=task_id, request=request)
    else:
        assert await executor.execute(operation_id=task_id, request=request) == result
    assert gateway.applies == 1


async def test_interruption_after_durable_pagination_plan_reconstructs_identical_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = _document(listing=True)
    gateway = _Gateway(_snapshot(native))
    store = _CopyCheckpointStore()
    task_id = TaskId(uuid.uuid7())
    request = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file", file_id=FileId("source"), expected_modified_time="initial"
        ),
        mode=TransformMode.FORMAT,
    )
    save = store.save_document_transform_checkpoint

    async def interrupt_save(
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        expected_generation: int,
        checkpoint: DocumentTransformCheckpoint,
    ) -> int:
        await save(
            task_id,
            lease_token=lease_token,
            expected_generation=expected_generation,
            checkpoint=checkpoint,
        )
        message = "interrupted after pagination plan"
        raise RuntimeError(message)

    monkeypatch.setattr(store, "save_document_transform_checkpoint", interrupt_save)
    with pytest.raises(RuntimeError, match="interrupted after pagination plan"):
        await _copy_executor(gateway=gateway, store=store, task_id=task_id).execute(
            operation_id=task_id, request=request
        )
    assert gateway.applies == 0
    checkpoint = store.checkpoints[-1]
    assert len(checkpoint.pagination) == 3
    monkeypatch.setattr(store, "save_document_transform_checkpoint", save)
    await _copy_executor(
        gateway=gateway, store=store, task_id=task_id, checkpoint=checkpoint
    ).execute(operation_id=task_id, request=request)
    first_requests = deepcopy(gateway.requests)
    await _copy_executor(
        gateway=gateway, store=store, task_id=task_id, checkpoint=store.checkpoints[-1]
    ).execute(operation_id=task_id, request=request)
    assert gateway.requests == first_requests
    assert gateway.applies == 1
