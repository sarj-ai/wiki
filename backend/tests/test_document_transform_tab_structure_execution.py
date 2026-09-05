from copy import deepcopy
from dataclasses import replace
import json
from typing import Any, override
import uuid

import pytest

from app.document_transform.gateway import OutputDraft, SourceSnapshot
from app.document_transform.schemas import (
    CopyCheckpoint,
    DocumentTransformRequest,
    DriveFileSource,
    TransformMode,
)
from app.document_transform.tab_structure import plan_tab_structure
from app.document_transform.tab_structure_execution import (
    StructureCheckpoint,
    StructureProvider,
    StructureStage,
    StructureStateError,
    bind_created_tabs,
    executable_structure,
    prepare_content_move,
    prepare_tab_creation,
    recover_created_tabs,
    verify_structure_content,
)
from app.identifiers import DocsRevisionId, DocsTabId, FileId, TaskId
from tests.test_document_transform import _copy_executor, _CopyCheckpointStore, _Gateway
from tests.test_document_transform_tab_structure import _document


def _checkpoint():
    plan = plan_tab_structure(_document(), comments_verified_absent=True)
    assert plan is not None
    return StructureCheckpoint(plan)


def _moving():
    intent, _ = prepare_tab_creation(_checkpoint())
    created = bind_created_tabs(
        intent,
        tab_ids=(DocsTabId("delivery"), DocsTabId("risks")),
        revision_id=DocsRevisionId("r2"),
    )
    return prepare_content_move(created)


def _output(checkpoint: StructureCheckpoint) -> dict[str, Any]:
    tabs: list[dict[str, Any]] = []
    for tab_id, section in zip(
        (checkpoint.plan.source_tab_id, *checkpoint.created_tab_ids),
        checkpoint.plan.sections,
        strict=True,
    ):
        content = [json.loads(checkpoint.plan.section_break_json)]
        for snapshot in section.paragraphs:
            element = json.loads(snapshot)
            offset = section.start_index - 1
            element["startIndex"] -= offset
            element["endIndex"] -= offset
            for run in element["paragraph"]["elements"]:
                run["startIndex"] -= offset
                run["endIndex"] -= offset
            content.append(element)
        tabs.append(
            {
                "tabProperties": {"tabId": tab_id, "title": section.title},
                "documentTab": {"body": {"content": content}},
            }
        )
    marker = "wiki.tab-structure." + checkpoint.plan.inventory_sha256
    tabs[0]["documentTab"]["namedRanges"] = {
        marker: {"namedRanges": [{"name": marker, "ranges": [{"startIndex": 1, "endIndex": 2}]}]}
    }
    return {"tabs": tabs}


def test_creation_requires_persisted_intent_and_never_retries_ambiguous_dispatch():
    checkpoint = _checkpoint()
    intent, body = prepare_tab_creation(checkpoint)
    assert intent.stage is StructureStage.CREATING
    assert body["writeControl"] == {"requiredRevisionId": "rev1"}
    assert len(body["requests"]) == 3
    with pytest.raises(StructureStateError):
        prepare_tab_creation(intent)
    with pytest.raises(StructureStateError):
        prepare_content_move(intent)


def test_native_keep_lines_together_is_preserved_in_tab_move_requests():
    document = _document()
    for element in document["tabs"][0]["documentTab"]["body"]["content"]:
        if "paragraph" in element:
            element["paragraph"]["paragraphStyle"]["keepLinesTogether"] = True
    plan = plan_tab_structure(document, comments_verified_absent=True)
    assert plan is not None
    assert executable_structure(plan, copy_mode=True)
    intent, _ = prepare_tab_creation(StructureCheckpoint(plan))
    created = bind_created_tabs(
        intent,
        tab_ids=(DocsTabId("delivery"), DocsTabId("risks")),
        revision_id=DocsRevisionId("r2"),
    )
    _, body = prepare_content_move(created)
    updates = [
        request["updateParagraphStyle"]
        for request in body["requests"]
        if "updateParagraphStyle" in request
    ]
    assert updates
    assert all(update["paragraphStyle"]["keepLinesTogether"] for update in updates)
    assert all("keepLinesTogether" in update["fields"] for update in updates)


def test_header_footer_margins_still_require_exact_cross_tab_style_preservation():
    checkpoint, _ = _moving()
    document = _output(checkpoint)
    style = {"marginHeader": {"magnitude": 36, "unit": "PT"}}
    for tab in document["tabs"]:
        tab["documentTab"]["documentStyle"] = deepcopy(style)
    assert verify_structure_content(checkpoint, document)
    document["tabs"][1]["documentTab"]["documentStyle"]["marginHeader"]["magnitude"] = 72
    assert not verify_structure_content(checkpoint, document)


@pytest.mark.parametrize(
    "ids",
    [("same", "same"), ("tab1", "new"), ("one",), ("", "two")],
    ids=["duplicate", "source-id", "missing", "empty"],
)
def test_created_ids_must_be_complete_unique_and_owned(ids: tuple[str, ...]):
    intent, _ = prepare_tab_creation(_checkpoint())
    with pytest.raises(StructureStateError):
        bind_created_tabs(
            intent,
            tab_ids=tuple(DocsTabId(value) for value in ids),
            revision_id=DocsRevisionId("r2"),
        )


def test_move_is_one_atomic_cas_preserving_terminal_newline_and_utf16():
    checkpoint, body = _moving()
    assert checkpoint.stage is StructureStage.MOVING
    assert body["writeControl"] == {"requiredRevisionId": "r2"}
    inserts = [request["insertText"] for request in body["requests"] if "insertText" in request]
    assert [request["text"] + "\n" for request in inserts] == [
        section.text for section in checkpoint.plan.sections[1:]
    ]
    deletion = next(
        request["deleteContentRange"]["range"]
        for request in body["requests"]
        if "deleteContentRange" in request
    )
    assert deletion["endIndex"] == checkpoint.plan.sections[-1].end_index - 1
    assert deletion["startIndex"] == checkpoint.plan.sections[0].end_index - 1
    with pytest.raises(StructureStateError):
        prepare_content_move(checkpoint)


def test_exact_readback_recovers_lost_move_reply_without_replaying():
    checkpoint, _ = _moving()
    output = _output(checkpoint)
    assert verify_structure_content(checkpoint, output)
    assert verify_structure_content(replace(checkpoint, stage=StructureStage.VERIFIED), output)
    assert not verify_structure_content(replace(checkpoint, stage=StructureStage.CREATED), output)
    altered = deepcopy(output)
    altered["tabs"][1]["documentTab"]["body"]["content"][2]["paragraph"]["elements"][0]["textRun"][
        "content"
    ] += " changed"
    assert not verify_structure_content(checkpoint, altered)
    altered = deepcopy(output)
    altered["tabs"].reverse()
    assert not verify_structure_content(checkpoint, altered)


def test_in_place_lists_and_unknown_style_fields_are_never_reconstructed():
    checkpoint = _checkpoint()
    assert executable_structure(checkpoint.plan, copy_mode=True)
    assert not executable_structure(checkpoint.plan, copy_mode=False)
    document = _document()
    document["tabs"][0]["documentTab"]["lists"] = {"list1": {}}
    plan = plan_tab_structure(document, comments_verified_absent=True)
    assert plan is not None
    assert not executable_structure(plan, copy_mode=True)


def test_atomic_creation_marker_recovers_ids_but_never_guesses_from_titles():
    intent, _ = prepare_tab_creation(_checkpoint())
    moving, _ = _moving()
    created_document = _output(moving)
    original = _document()["tabs"][0]["documentTab"]["body"]
    created_document["tabs"][0]["documentTab"]["body"] = original
    for tab in created_document["tabs"][1:]:
        tab["documentTab"]["body"]["content"] = [
            {"endIndex": 1, "sectionBreak": {"sectionStyle": {}}},
            {"paragraph": {"elements": [{"textRun": {"content": "\n"}}]}},
        ]
    created_document["revisionId"] = "created"
    recovered = recover_created_tabs(intent, created_document)
    assert recovered.created_tab_ids == ("delivery", "risks")
    assert recovered.revision_id == "created"
    del created_document["tabs"][0]["documentTab"]["namedRanges"]
    with pytest.raises(StructureStateError):
        recover_created_tabs(intent, created_document)
    document = _document()
    document["tabs"][0]["documentTab"]["body"]["content"][1]["paragraph"]["paragraphStyle"][
        "unknownStyle"
    ] = "protected"
    plan = plan_tab_structure(document, comments_verified_absent=True)
    assert plan is not None
    assert not executable_structure(plan, copy_mode=True)


def test_native_heading_ids_regenerate_only_on_link_free_copy_and_styles_remain_exact():
    document = _document()
    document["tabs"][0]["documentTab"]["body"]["content"][0]["sectionBreak"]["sectionStyle"] = {
        "contentDirection": "LEFT_TO_RIGHT"
    }
    document["tabs"][0]["documentTab"]["body"]["content"][1]["paragraph"]["paragraphStyle"][
        "headingId"
    ] = "original-heading"
    plan = plan_tab_structure(document, comments_verified_absent=True)
    assert plan is not None
    assert executable_structure(plan, copy_mode=True)
    intent, _ = prepare_tab_creation(StructureCheckpoint(plan))
    created = bind_created_tabs(
        intent,
        tab_ids=(DocsTabId("delivery"), DocsTabId("risks")),
        revision_id=DocsRevisionId("r2"),
    )
    moving, body = prepare_content_move(created)
    assert all(
        "headingId" not in request.get("updateParagraphStyle", {}).get("paragraphStyle", {})
        for request in body["requests"]
    )
    output = _output(moving)
    output["tabs"][0]["documentTab"]["body"]["content"][1]["paragraph"]["paragraphStyle"][
        "headingId"
    ] = "generated-heading"
    assert verify_structure_content(moving, output)
    output["tabs"][1]["documentTab"]["body"]["content"][0]["sectionBreak"]["sectionStyle"] = {}
    assert not verify_structure_content(moving, output)


@pytest.mark.parametrize("comments_absent", [True, False])
async def test_provider_requires_explicit_comment_absence(comments_absent: bool):
    writes = []
    provider = StructureProvider(
        lambda _: _document(), lambda *args: writes.append(args), lambda _: comments_absent
    )
    plan = await provider.plan(
        document_id=FileId("copy"), source_id=FileId("source"), revision_id=DocsRevisionId("rev1")
    )
    assert (plan is not None) is comments_absent
    assert not writes


@pytest.mark.parametrize("crash_stage", [None, StructureStage.CREATING, StructureStage.MOVING])
async def test_executor_persists_before_dispatch_and_recovers_without_duplicate_tabs(
    crash_stage: StructureStage | None,
):
    store = _CopyCheckpointStore()
    source = _document()
    source["tabs"][0]["tabProperties"]["tabId"] = "tab-1"
    current = deepcopy(source)
    current["revisionId"] = "output-revision-1"
    writes: list[StructureStage] = []
    crashed = False

    def read(document_id: FileId) -> dict[str, Any]:
        assert document_id == "output"
        return deepcopy(current)

    def write(document_id: FileId, body: dict[str, Any]) -> None:
        nonlocal current, crashed
        assert document_id == "output"
        structure = store.checkpoints[-1].tab_structure
        assert structure is not None
        assert body["writeControl"]["requiredRevisionId"] == current["revisionId"]
        writes.append(structure.stage)
        bound = replace(structure, created_tab_ids=("delivery", "risks"))
        current = _output(bound)
        if structure.stage is StructureStage.CREATING:
            current["tabs"][0]["documentTab"]["body"] = deepcopy(
                source["tabs"][0]["documentTab"]["body"]
            )
            for tab in current["tabs"][1:]:
                tab["documentTab"]["body"]["content"] = [
                    {"endIndex": 1, "sectionBreak": {"sectionStyle": {}}},
                    {"paragraph": {"elements": [{"textRun": {"content": "\n"}}]}},
                ]
        current["revisionId"] = f"structure-{len(writes)}"
        if structure.stage is crash_stage and not crashed:
            crashed = True
            raise TimeoutError

    class Gateway(_Gateway):
        @override
        async def apply_requests_once(self, **kwargs: Any) -> OutputDraft:
            kwargs["required_revision_id"] = DocsRevisionId("output-revision-1")
            return await super().apply_requests_once(**kwargs)

    gateway = Gateway(
        snapshot=SourceSnapshot(
            title="Long report",
            parent_folder_id=FileId("folder"),
            modified_time="before",
            revision_id=DocsRevisionId("rev1"),
            media_type="application/vnd.google-apps.document",
            google_document=source,
        )
    )
    gateway.structure_provider = StructureProvider(read, write, lambda _: True)
    task_id = TaskId(uuid.uuid7())
    request = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file", file_id=FileId("source"), expected_modified_time="before"
        ),
        mode=TransformMode.FORMAT,
    )
    checkpoint = None
    if crash_stage is not None:
        with pytest.raises(TimeoutError):
            await _copy_executor(gateway=gateway, store=store, task_id=task_id).execute(
                operation_id=task_id, request=request
            )
        checkpoint = CopyCheckpoint.model_validate_json(store.checkpoints[-1].model_dump_json())
    result = await _copy_executor(
        gateway=gateway, store=store, task_id=task_id, checkpoint=checkpoint
    ).execute(operation_id=task_id, request=request)
    assert result.output.file_id == "output"
    assert writes == [StructureStage.CREATING, StructureStage.MOVING]
    assert store.checkpoints[-1].tab_structure is not None
    assert store.checkpoints[-1].tab_structure.stage is StructureStage.VERIFIED
    await _copy_executor(
        gateway=gateway, store=store, task_id=task_id, checkpoint=store.checkpoints[-1]
    ).execute(operation_id=task_id, request=request)
    assert len(writes) == 2
