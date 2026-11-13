from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import override
import uuid

from pydantic import JsonValue
import pytest

from app.document_transform.brand import SarjBrandProfile, build_copy_mutations
from app.document_transform.errors import OutputConflictError
from app.document_transform.ir import parse_google_document, utf16_length
from app.document_transform.native_pagination import (
    apply_native_pagination,
    existing_native_pagination,
    plan_native_pagination,
)
from app.document_transform.native_terminal import (
    plan_terminal_blank,
    terminal_blank_requests,
    verify_terminal_blank,
)
from app.document_transform.readability_json import array, integer, obj
from app.document_transform.schemas import (
    CopyCheckpoint,
    DocumentTransformCheckpoint,
    DocumentTransformRequest,
    DriveFileSource,
    PlannedCopyCheckpoint,
    TransformMode,
)
from app.document_transform.unslop import UNSLOP_CATALOG_SHA256
from app.identifiers import FileId, TaskId
from tests.test_authored_labels import _snapshot
from tests.test_document_transform import _copy_executor, _CopyCheckpointStore, _Gateway
from tests.test_native_pagination import _document as grouped_document


def _document() -> dict[str, JsonValue]:
    return obj(
        json.loads(
            (Path(__file__).parent / "data/native_terminal/google-default-tail.json").read_text()
        )
    )


def _content(document: dict[str, JsonValue]) -> list[JsonValue]:
    return array(obj(obj(obj(array(document["tabs"])[0])["documentTab"])["body"])["content"])


def _normal(document: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return obj(array(obj(document["namedStyles"])["styles"])[0])


def test_copy_compacts_only_the_default_empty_tail_and_verifies_readback() -> None:
    native = _document()
    original = parse_google_document(native, title="QA")
    expected = apply_native_pagination(original, plan_native_pagination(native, original))
    requests = terminal_blank_requests(native, expected)
    terminal = obj(_content(native)[-1])
    assert requests == [
        {
            "updateTextStyle": {
                "range": {
                    "tabId": "t.0",
                    "startIndex": terminal["startIndex"],
                    "endIndex": terminal["endIndex"],
                },
                "textStyle": {"fontSize": {"magnitude": 1, "unit": "PT"}},
                "fields": "fontSize",
            }
        }
    ]
    all_requests = build_copy_mutations(
        original,
        expected,
        profile=SarjBrandProfile(),
        headers=(),
        logo_uri="",
        source_document=native,
    )
    assert requests[0] in all_requests
    actual = deepcopy(native)
    run = obj(array(obj(obj(_content(actual)[-1])["paragraph"])["elements"])[0])
    obj(run["textRun"])["textStyle"] = {"fontSize": {"magnitude": 1, "unit": "PT"}}
    assert verify_terminal_blank(native, actual, expected)
    obj(run["textRun"])["textStyle"] = {"fontSize": {"magnitude": 2, "unit": "PT"}}
    assert not verify_terminal_blank(native, actual, expected)


def test_legacy_checkpoint_without_terminal_authorization_keeps_its_mutations() -> None:
    native = _document()
    original = parse_google_document(native, title="QA")
    assert terminal_blank_requests(native, original) == []
    assert verify_terminal_blank(native, native, original)


def test_post_edit_terminal_position_uses_utf16_width() -> None:
    native = _document()
    original = parse_google_document(native, title="QA")
    changed = original.replace_blocks({original.blocks[0].id: original.blocks[0].text + " 🧭"})
    plan = plan_terminal_blank(native, changed)
    assert plan is not None
    assert plan.target_start == 1 + utf16_length(changed.blocks[0].text) + 1


def test_native_list_before_final_prose_keeps_the_actual_utf16_displacement() -> None:
    native = _document()
    content = _content(native)
    item = deepcopy(obj(content[-2]))
    obj(item["paragraph"])["bullet"] = {"listId": "list-qa", "nestingLevel": 0}
    content.insert(1, item)
    start = 1
    for value in content[1:]:
        element = obj(value)
        run = obj(array(obj(element["paragraph"])["elements"])[0])
        end = start + utf16_length(str(obj(run["textRun"])["content"]))
        element["startIndex"], element["endIndex"] = start, end
        run["startIndex"], run["endIndex"] = start, end
        start = end
    original = parse_google_document(native, title="QA")
    assert original.blocks[0].native_bullet_json is not None
    assert original.blocks[0].editable
    changed = original.replace_blocks({original.blocks[0].id: original.blocks[0].text + " 🧭"})
    plan = plan_terminal_blank(native, changed)
    assert plan is not None
    assert plan.target_start == integer(obj(content[-1])["startIndex"]) + utf16_length(" 🧭")


@pytest.mark.parametrize(
    "style",
    [
        pytest.param({"bold": True}, id="bold"),
        pytest.param({"fontSize": {"magnitude": 11, "unit": "PT"}}, id="authored-font"),
        pytest.param({"link": {"url": "https://example.test/"}}, id="link"),
    ],
)
def test_preserves_authored_terminal_text_style(style: dict[str, JsonValue]) -> None:
    native = _document()
    run = obj(array(obj(obj(_content(native)[-1])["paragraph"])["elements"])[0])
    obj(run["textRun"])["textStyle"] = style
    assert plan_terminal_blank(native, parse_google_document(native, title="QA")) is None


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param({"bullet": {"listId": "authored-list"}}, id="list"),
        pytest.param({"positionedObjectIds": ["authored-object"]}, id="positioned-object"),
        pytest.param({"suggestedParagraphStyleChanges": {"pending": {}}}, id="suggestion"),
    ],
)
def test_preserves_opaque_terminal_paragraph_properties(extra: dict[str, JsonValue]) -> None:
    native = _document()
    obj(obj(_content(native)[-1])["paragraph"]).update(extra)
    assert plan_terminal_blank(native, parse_google_document(native, title="QA")) is None


@pytest.mark.parametrize("inherited", [False, True])
def test_preserves_an_authored_page_break(inherited: bool) -> None:
    native = _document()
    style = (
        obj(_normal(native)["paragraphStyle"])
        if inherited
        else obj(obj(obj(_content(native)[-1])["paragraph"])["paragraphStyle"])
    )
    style["pageBreakBefore"] = True
    assert plan_terminal_blank(native, parse_google_document(native, title="QA")) is None


def test_declines_pageless_and_visible_inherited_layout() -> None:
    native = _document()
    obj(obj(native["documentStyle"])["documentFormat"])["documentMode"] = "PAGELESS"
    assert plan_terminal_blank(native, parse_google_document(native, title="QA")) is None
    native = _document()
    obj(_normal(native)["paragraphStyle"])["indentStart"] = {"magnitude": 12, "unit": "PT"}
    assert plan_terminal_blank(native, parse_google_document(native, title="QA")) is None


def test_preserves_multiple_trailing_paragraphs() -> None:
    native = _document()
    content = _content(native)
    terminal = deepcopy(obj(content[-1]))
    start = integer(obj(content[-1])["endIndex"])
    terminal["startIndex"], terminal["endIndex"] = start, start + 1
    run = obj(array(obj(terminal["paragraph"])["elements"])[0])
    run["startIndex"], run["endIndex"] = start, start + 1
    content.append(terminal)
    assert plan_terminal_blank(native, parse_google_document(native, title="QA")) is None


def test_converged_google_omitted_zero_needs_no_terminal_write() -> None:
    native = _document()
    content = _content(native)
    obj(obj(obj(content[-2])["paragraph"])["paragraphStyle"])["spaceBelow"] = {"unit": "PT"}
    run = obj(array(obj(obj(content[-1])["paragraph"])["elements"])[0])
    obj(run["textRun"])["textStyle"] = {"fontSize": {"magnitude": 1, "unit": "PT"}}
    original = parse_google_document(native, title="QA")
    pagination = existing_native_pagination(native, original)
    assert pagination
    expected = apply_native_pagination(original, pagination)
    assert terminal_blank_requests(native, expected) == []
    assert verify_terminal_blank(native, native, expected)


def test_pre_upgrade_formatted_document_keeps_its_existing_pagination() -> None:
    native = obj(grouped_document())
    defaults = _document()
    native["documentStyle"] = defaults["documentStyle"]
    native["namedStyles"] = defaults["namedStyles"]
    content = _content(native)
    for node in content:
        element = obj(node)
        run = obj(array(obj(element["paragraph"])["elements"])[0])
        run["startIndex"], run["endIndex"] = element["startIndex"], element["endIndex"]
    for node in content[:2]:
        obj(obj(obj(node)["paragraph"])["paragraphStyle"]).update(
            {"keepWithNext": True, "keepLinesTogether": True}
        )
    terminal = deepcopy(obj(_content(defaults)[-1]))
    start = integer(obj(content[-1])["endIndex"])
    terminal["startIndex"], terminal["endIndex"] = start, start + 1
    run = obj(array(obj(terminal["paragraph"])["elements"])[0])
    run["startIndex"], run["endIndex"] = start, start + 1
    content.append(terminal)
    original = parse_google_document(native, title="QA")
    plan = plan_terminal_blank(native, original)
    assert plan is not None
    assert plan.needs_write
    assert len(existing_native_pagination(native, original)) == 2


@dataclass(frozen=True, slots=True)
class _CheckpointFixture:
    gateway: _Gateway
    store: _CopyCheckpointStore
    task_id: TaskId
    request: DocumentTransformRequest


def _checkpoint_fixture(*, source: dict[str, JsonValue] | None = None) -> _CheckpointFixture:
    native = _document() if source is None else deepcopy(source)
    obj(obj(array(native["tabs"])[0])["tabProperties"])["tabId"] = "tab-1"
    native["revisionId"] = "source-revision-1"
    request = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file", file_id=FileId("source"), expected_modified_time="initial"
        ),
        mode=TransformMode.FORMAT,
    )
    return _CheckpointFixture(
        gateway=_Gateway(_snapshot(native)),
        store=_CopyCheckpointStore(),
        task_id=TaskId(uuid.uuid7()),
        request=request,
    )


class _InterruptedStore(_CopyCheckpointStore):
    @override
    async def save_document_transform_checkpoint(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        expected_generation: int,
        checkpoint: DocumentTransformCheckpoint,
    ) -> int:
        await super().save_document_transform_checkpoint(
            task_id,
            lease_token=lease_token,
            expected_generation=expected_generation,
            checkpoint=checkpoint,
        )
        message = "interrupted after durable terminal plan"
        raise RuntimeError(message)


@pytest.mark.parametrize(
    "legacy_checkpoint",
    [None, "pre-upgrade-copy-checkpoint.json", "pre-middle-dot-copy-checkpoint.json"],
)
async def test_interrupted_current_and_compatible_pre_upgrade_plans_resume_without_extra_writes(
    legacy_checkpoint: str | None,
) -> None:
    fixture = _checkpoint_fixture()
    checkpoint: CopyCheckpoint
    legacy = legacy_checkpoint is not None
    if legacy:
        assert legacy_checkpoint is not None
        saved = json.loads(
            (Path(__file__).parent / "data/native_terminal" / legacy_checkpoint).read_text()
        )
        checkpoint = PlannedCopyCheckpoint.model_validate(saved["checkpoint"])
        if checkpoint.unslop_catalog_sha256 != UNSLOP_CATALOG_SHA256:
            with pytest.raises(OutputConflictError):
                await _copy_executor(
                    gateway=fixture.gateway,
                    store=fixture.store,
                    task_id=fixture.task_id,
                    checkpoint=checkpoint,
                ).execute(operation_id=fixture.task_id, request=fixture.request)
            assert fixture.gateway.applies == 0
            return
    else:
        interrupted_store = _InterruptedStore()
        with pytest.raises(RuntimeError, match="durable terminal plan"):
            await _copy_executor(
                gateway=fixture.gateway, store=interrupted_store, task_id=fixture.task_id
            ).execute(operation_id=fixture.task_id, request=fixture.request)
        checkpoint = interrupted_store.checkpoints[-1]
    assert fixture.gateway.applies == 0
    result = await _copy_executor(
        gateway=fixture.gateway, store=fixture.store, task_id=fixture.task_id, checkpoint=checkpoint
    ).execute(operation_id=fixture.task_id, request=fixture.request)
    completed = fixture.store.checkpoints[-1]
    first_requests = deepcopy(fixture.gateway.requests)
    assert completed.request_builder_version.endswith("-terminal-v1") is not legacy
    assert (
        any(
            item.get("updateTextStyle", {}).get("textStyle")
            == {"fontSize": {"magnitude": 1, "unit": "PT"}}
            for item in first_requests
        )
        is not legacy
    )
    replay = await _copy_executor(
        gateway=fixture.gateway, store=fixture.store, task_id=fixture.task_id, checkpoint=completed
    ).execute(operation_id=fixture.task_id, request=fixture.request)
    assert replay == result
    assert fixture.gateway.requests == first_requests
    assert fixture.gateway.applies == 1


async def test_older_factual_policy_checkpoint_is_rejected_before_any_write() -> None:
    fixture = _checkpoint_fixture()
    saved = json.loads(
        (
            Path(__file__).parent / "data/native_terminal/pre-quotation-policy-copy-checkpoint.json"
        ).read_text()
    )
    checkpoint = PlannedCopyCheckpoint.model_validate(saved["checkpoint"])
    with pytest.raises(OutputConflictError):
        await _copy_executor(
            gateway=fixture.gateway,
            store=fixture.store,
            task_id=fixture.task_id,
            checkpoint=checkpoint,
        ).execute(operation_id=fixture.task_id, request=fixture.request)
    assert fixture.gateway.applies == 0


async def test_unplanned_terminal_builder_marker_is_rejected_before_any_write() -> None:
    native = _document()
    run = obj(array(obj(obj(_content(native)[-1])["paragraph"])["elements"])[0])
    obj(run["textRun"])["textStyle"] = {"bold": True}
    fixture = _checkpoint_fixture(source=native)
    interrupted_store = _InterruptedStore()
    with pytest.raises(RuntimeError, match="durable terminal plan"):
        await _copy_executor(
            gateway=fixture.gateway, store=interrupted_store, task_id=fixture.task_id
        ).execute(operation_id=fixture.task_id, request=fixture.request)
    checkpoint = interrupted_store.checkpoints[-1]
    assert not checkpoint.request_builder_version.endswith("-terminal-v1")
    tampered = checkpoint.model_copy(
        update={"request_builder_version": checkpoint.request_builder_version + "-terminal-v1"}
    )
    with pytest.raises(OutputConflictError):
        await _copy_executor(
            gateway=fixture.gateway,
            store=fixture.store,
            task_id=fixture.task_id,
            checkpoint=tampered,
        ).execute(operation_id=fixture.task_id, request=fixture.request)
    assert fixture.gateway.applies == 0
