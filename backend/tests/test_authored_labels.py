from copy import deepcopy
from dataclasses import replace
from typing import Any
import uuid

import pytest

from app.document_transform.authored_labels import apply_authored_labels, plan_authored_labels
from app.document_transform.errors import OutputConflictError
from app.document_transform.gateway import SourceSnapshot
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.ir import BlockKind, parse_google_document
from app.document_transform.schemas import (
    DocumentTransformCheckpoint,
    DocumentTransformRequest,
    DriveFileSource,
    TransformMode,
)
from app.document_transform.tab_names import NativeTabProperties, plan_tab_names
from app.files.service import GoogleFilesService
from app.identifiers import DocsRevisionId, DocsSegmentId, DocsTabId, FileId, TaskId
from app.models.user import User
from tests.test_document_transform import (
    _copy_executor,
    _CopyCheckpointStore,
    _Gateway,
    _pending_output_draft,
)


def _document() -> dict[str, Any]:
    content = []
    start = 1
    for text, bold in [
        ("Context:", True),
        ("The team reviewed the draft.", False),
        ("Next steps:", True),
        ("Keep the existing deadline.", False),
    ]:
        end = start + len(text) + 1
        content.append(
            {
                "startIndex": start,
                "endIndex": end,
                "paragraph": {
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    "elements": [
                        {"textRun": {"content": text + "\n", "textStyle": {"bold": bold}}}
                    ],
                },
            }
        )
        start = end
    return {
        "revisionId": "source-revision-1",
        "tabs": [
            {
                "tabProperties": {"tabId": "tab-1"},
                "documentTab": {"body": {"content": content}},
            }
        ],
    }


def _snapshot(document: dict[str, Any]) -> SourceSnapshot:
    return SourceSnapshot(
        title="Review",
        parent_folder_id=FileId("folder"),
        modified_time="initial",
        revision_id=DocsRevisionId("source-revision-1"),
        media_type="application/vnd.google-apps.document",
        google_document=document,
    )


def _with_native_title(document: dict[str, Any]) -> None:
    content = document["tabs"][0]["documentTab"]["body"]["content"]
    title = deepcopy(content[0])
    title["paragraph"]["paragraphStyle"]["namedStyleType"] = "TITLE"
    title["paragraph"]["elements"][0]["textRun"]["content"] = "Review\n"
    for element in content:
        element["startIndex"] += 7
        element["endIndex"] += 7
    title["startIndex"] = 1
    title["endIndex"] = 8
    content.insert(0, title)


def _split_terminal_runs(document: dict[str, Any]) -> None:
    for element in document["tabs"][0]["documentTab"]["body"]["content"]:
        text_run = element["paragraph"]["elements"][0]["textRun"]
        element["paragraph"]["elements"] = [
            {
                "startIndex": element["startIndex"],
                "endIndex": element["endIndex"] - 1,
                "textRun": {**text_run, "content": text_run["content"][:-1]},
            },
            {
                "startIndex": element["endIndex"] - 1,
                "endIndex": element["endIndex"],
                "textRun": {"content": "\n", "textStyle": {"bold": False}},
            },
        ]


def test_role_plan_preserves_all_text_ranges_and_is_a_repeat_noop() -> None:
    original = parse_google_document(_document(), title="Review")
    roles = plan_authored_labels(original)
    transformed = apply_authored_labels(original, roles)
    assert len(roles) == 2
    assert [block.kind for block in transformed.blocks] == [
        BlockKind.HEADING,
        BlockKind.PARAGRAPH,
        BlockKind.HEADING,
        BlockKind.PARAGRAPH,
    ]
    assert (
        tuple(
            replace(after, kind=before.kind, level=before.level)
            for before, after in zip(original.blocks, transformed.blocks, strict=True)
        )
        == original.blocks
    )
    assert plan_authored_labels(transformed) == ()


@pytest.mark.parametrize("style", ["TITLE", "HEADING_1", "SUBTITLE"])
@pytest.mark.parametrize("position", [0, 2])
def test_only_single_leading_native_title_can_precede_sections(style: str, position: int) -> None:
    document = _document()
    content = document["tabs"][0]["documentTab"]["body"]["content"]
    title = deepcopy(content[0])
    title["paragraph"]["paragraphStyle"]["namedStyleType"] = style
    title["paragraph"]["elements"][0]["textRun"]["content"] = "Review\n"
    content.insert(position, title)
    start = 1
    for element in content:
        element["startIndex"] = start
        start += len(element["paragraph"]["elements"][0]["textRun"]["content"])
        element["endIndex"] = start
    original = parse_google_document(document, title="Review")
    roles = plan_authored_labels(original)
    expected_count = 2 if style == "TITLE" and position == 0 else 0
    assert len(roles) == expected_count
    transformed = apply_authored_labels(original, roles)
    assert transformed.blocks[position] == original.blocks[position]
    assert plan_authored_labels(transformed) == ()


def test_multiple_native_titles_do_not_enable_promotion() -> None:
    original = parse_google_document(_document(), title="Review")
    title = replace(original.blocks[0], kind=BlockKind.HEADING, source_named_style="TITLE")
    assert plan_authored_labels(replace(original, blocks=(title, title, *original.blocks))) == ()


@pytest.mark.parametrize(
    "kind", [BlockKind.TABLE, BlockKind.CODE, BlockKind.QUOTE, BlockKind.LIST_ITEM]
)
def test_non_prose_roles_are_never_promoted(kind: BlockKind) -> None:
    original = parse_google_document(_document(), title="Review")
    blocked = replace(original.blocks[0], kind=kind)
    assert plan_authored_labels(replace(original, blocks=(blocked, *original.blocks[1:]))) == ()


def test_furniture_and_unknown_named_styles_are_never_promoted() -> None:
    original = parse_google_document(_document(), title="Review")
    first = original.blocks[0]
    assert first.source_range is not None
    furniture = replace(
        first, source_range=replace(first.source_range, segment_id=DocsSegmentId("footer"))
    )
    assert plan_authored_labels(replace(original, blocks=(furniture, *original.blocks[1:]))) == ()
    unknown = replace(first, source_named_style="UNKNOWN")
    assert plan_authored_labels(replace(original, blocks=(unknown, *original.blocks[1:]))) == ()


@pytest.mark.parametrize(
    "change",
    [
        "one",
        "heading",
        "list",
        "link",
        "chip",
        "suggestion",
        "prose",
        "duplicate",
        "empty_section",
        "not_bold",
        "multitab",
    ],
)
def test_ambiguous_or_protected_documents_are_not_promoted(change: str) -> None:
    document = _document()
    content = document["tabs"][0]["documentTab"]["body"]["content"]
    paragraph = content[0]["paragraph"]
    match change:
        case "one":
            del content[2:]
        case "heading":
            paragraph["paragraphStyle"]["namedStyleType"] = "HEADING_1"
        case "list":
            paragraph["bullet"] = {"listId": "authored-list"}
        case "link":
            paragraph["elements"][0]["textRun"]["textStyle"]["link"] = {"url": "https://sarj.ai"}
        case "chip":
            paragraph["elements"].append({"inlineObjectElement": {"inlineObjectId": "authored"}})
        case "suggestion":
            paragraph["elements"][0]["suggestedInsertionIds"] = ["authored"]
        case "prose":
            paragraph["elements"][0]["textRun"]["content"] = "We decided:\n"
        case "duplicate":
            content[2]["paragraph"]["elements"] = deepcopy(paragraph["elements"])
        case "empty_section":
            del content[1]
        case "not_bold":
            paragraph["elements"][0]["textRun"]["textStyle"]["bold"] = False
        case _:
            document["tabs"].append(deepcopy(document["tabs"][0]))
            document["tabs"][1]["tabProperties"]["tabId"] = "second"
    assert plan_authored_labels(parse_google_document(document, title="Review")) == ()


@pytest.mark.parametrize("mode", [TransformMode.FORMAT, TransformMode.BOTH, TransformMode.UNSLOP])
@pytest.mark.parametrize("interruption", ["none", "planned", "old_builder", "missing_roles"])
@pytest.mark.parametrize("native_title", [False, True])
@pytest.mark.parametrize("terminal_runs", [False, True])
async def test_copy_roles_are_persisted_reconstructed_and_replayed(
    monkeypatch: pytest.MonkeyPatch,
    mode: TransformMode,
    interruption: str,
    *,
    native_title: bool,
    terminal_runs: bool,
) -> None:
    document = _document()
    if native_title:
        _with_native_title(document)
    if terminal_runs:
        _split_terminal_runs(document)
    original_document = deepcopy(document)
    snapshot = _snapshot(document)
    if native_title:
        snapshot = replace(snapshot, title="Review — Unslopped")
    gateway = _Gateway(snapshot)
    naming_preferences: list[object] = []
    tab_titles: list[str] = []

    async def record_naming(**kwargs: object) -> None:
        naming_preferences.append(kwargs["prefer_document_title"])
        parsed = parse_google_document(document, title=snapshot.title)
        expected = apply_authored_labels(parsed, plan_authored_labels(parsed))
        naming = plan_tab_names(
            expected,
            (NativeTabProperties(DocsTabId("tab-1"), "Tab 1"),),
            prefer_document_title=kwargs["prefer_document_title"] is True,
        )
        tab_titles.extend(rename.title for rename in naming.renames)

    monkeypatch.setattr(gateway, "plan_tab_naming", record_naming)
    store = _CopyCheckpointStore()
    task_id = TaskId(uuid.uuid7())
    request = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file",
            file_id=FileId("source"),
            expected_modified_time="initial",
        ),
        mode=mode,
    )
    saved = store.save_document_transform_checkpoint

    async def interrupt_save(
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        expected_generation: int,
        checkpoint: DocumentTransformCheckpoint,
    ) -> int:
        await saved(
            task_id,
            lease_token=lease_token,
            expected_generation=expected_generation,
            checkpoint=checkpoint,
        )
        message = "simulated interruption after durable plan"
        raise RuntimeError(message)

    checkpoint = None
    if interruption != "none":
        monkeypatch.setattr(store, "save_document_transform_checkpoint", interrupt_save)
        with pytest.raises(RuntimeError, match="simulated interruption"):
            await _copy_executor(gateway=gateway, store=store, task_id=task_id).execute(
                operation_id=task_id, request=request
            )
        assert gateway.draft is None
        assert gateway.applies == 0
        monkeypatch.setattr(store, "save_document_transform_checkpoint", saved)
        checkpoint = store.checkpoints[-1]
        if interruption == "old_builder":
            checkpoint = checkpoint.model_copy(
                update={"request_builder_version": "google-docs-requests-v13"}
            )
        elif interruption == "missing_roles" and mode is not TransformMode.UNSLOP:
            checkpoint = checkpoint.model_copy(update={"authored_labels": ()})
        if interruption == "old_builder" or (
            interruption == "missing_roles" and mode is not TransformMode.UNSLOP
        ):
            with pytest.raises(OutputConflictError):
                await _copy_executor(
                    gateway=gateway, store=store, task_id=task_id, checkpoint=checkpoint
                ).execute(operation_id=task_id, request=request)
            assert gateway.draft is None
            assert gateway.applies == 0
            return
    result = await _copy_executor(
        gateway=gateway, store=store, task_id=task_id, checkpoint=checkpoint
    ).execute(
        operation_id=task_id,
        request=request,
    )
    count = 0 if mode is TransformMode.UNSLOP else 2
    assert len(store.checkpoints[0].authored_labels) == count
    assert result.promoted_heading_count == count
    assert naming_preferences == ([] if mode is TransformMode.UNSLOP else [not native_title])
    assert tab_titles == ([] if mode is TransformMode.UNSLOP else ["Review"])
    requests = deepcopy(gateway.requests)
    replay = await _copy_executor(
        gateway=gateway, store=store, task_id=task_id, checkpoint=store.checkpoints[-1]
    ).execute(operation_id=task_id, request=request)
    assert replay == result
    assert gateway.requests == requests
    assert gateway.applies == 1
    assert document == original_document


def test_modified_label_or_duplicate_role_is_rejected() -> None:
    original = parse_google_document(_document(), title="Review")
    roles = plan_authored_labels(original)
    with pytest.raises(OutputConflictError):
        apply_authored_labels(original.replace_blocks({roles[0].block_id: "Changed:"}), roles)
    with pytest.raises(OutputConflictError):
        apply_authored_labels(original, (roles[0], roles[0]))


@pytest.mark.parametrize("label", ["Owner:", "Date:", "Status:", "Name:", "Approver:", "Budget:"])
def test_scalar_field_labels_do_not_invent_document_sections(label: str) -> None:
    document = _document()
    document["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["elements"][0]["textRun"][
        "content"
    ] = label + "\n"
    assert plan_authored_labels(parse_google_document(document, title="Review")) == ()


@pytest.mark.parametrize("title", ["Decision review", "Untitled document"])
def test_promoted_section_does_not_become_whole_document_tab_identity(title: str) -> None:
    original = parse_google_document(_document(), title=title)
    expected = apply_authored_labels(original, plan_authored_labels(original))
    tabs = (NativeTabProperties(DocsTabId("tab-1"), "Tab 1"),)
    plan = plan_tab_names(expected, tabs, prefer_document_title=True)
    assert [rename.title for rename in plan.renames] == (
        ["Decision Review"] if title == "Decision review" else ["Tab 1"]
    )


@pytest.mark.parametrize("tamper", ["none", "missing_role", "extra_role", "text", "link"])
async def test_native_readback_requires_exact_planned_roles_and_content(
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    source = _document()
    original = parse_google_document(source, title="Review")
    expected = apply_authored_labels(original, plan_authored_labels(original))
    output = deepcopy(source)
    content = output["tabs"][0]["documentTab"]["body"]["content"]
    for index in (0, 2):
        content[index]["paragraph"]["paragraphStyle"] = {
            "namedStyleType": "HEADING_1",
            "headingId": f"generated-{index}",
        }
    if tamper == "missing_role":
        content[0]["paragraph"]["paragraphStyle"] = {"namedStyleType": "NORMAL_TEXT"}
    elif tamper == "extra_role":
        content[1]["paragraph"]["paragraphStyle"] = {"namedStyleType": "HEADING_1"}
    elif tamper == "text":
        content[1]["paragraph"]["elements"][0]["textRun"]["content"] = "Changed facts.\n"
    elif tamper == "link":
        content[0]["paragraph"]["elements"][0]["textRun"]["textStyle"]["link"] = {
            "url": "https://sarj.ai"
        }
    gateway = GoogleDocumentGateway(
        user=User(), files=object.__new__(GoogleFilesService), logo_uri=""
    )
    monkeypatch.setattr(gateway, "_get_document_sync", lambda _file_id: output)
    snapshot = _snapshot(source)
    verification = await gateway.verify_output(
        draft=_pending_output_draft(pending_mutation_sha256="", mutation_base_revision_sha256=""),
        source=snapshot,
        expected=expected,
        verify_brand=False,
    )
    assert (verification.content_preserved and verification.structure_preserved) is (
        tamper == "none"
    )
