from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from typing import Any, NamedTuple, override
import uuid

from pydantic import JsonValue
import pytest

from app.document_transform.authored_labels import plan_authored_labels
from app.document_transform.copy_font_defense import copy_preserved_font_ids
from app.document_transform.copy_visual_authority import verify_copy_visual_authority
from app.document_transform.errors import OutputConflictError, OutputVerificationError
from app.document_transform.gateway import OutputVerification, SourceSnapshot
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.ir import DocumentIR, parse_google_document
from app.document_transform.native_font_defense import apply_native_font_preservation
from app.document_transform.native_visual_authority import (
    apply_visual_authority,
)
from app.document_transform.readability_json import array, bodies, obj
from app.document_transform.schemas import OutputStrategy, TransformMode
from app.document_transform.tab_structure import plan_tab_structure
from app.document_transform.tab_structure_execution import (
    MovingStructureCheckpoint,
    PlannedStructureCheckpoint,
    StructureCheckpoint,
    bind_created_tabs,
    complete_structure,
    prepare_content_move,
    prepare_tab_creation,
    verify_structure_content,
)
from app.identifiers import DocsRevisionId, DocsTabId, FileId, TaskId
from tests.test_authored_labels import _document as _label_document
from tests.test_copy_font_preservation import _verify
from tests.test_document_transform import (
    _branded_google_document,
    _copy_executor,
    _CopyCheckpointStore,
    _Gateway,
    _in_place_request,
    _pending_output_draft,
)
from tests.test_document_transform_header_provenance import _snapshot
from tests.test_document_transform_tab_structure import _document as _sectioned_document
from tests.test_document_transform_tab_structure_execution import _output
from tests.test_native_visual_authority import _document, _paragraph, _prepend


@pytest.mark.parametrize("mode", [TransformMode.FORMAT, TransformMode.BOTH])
async def test_copy_preserves_manual_title_and_replays_without_new_writes(mode: TransformMode):
    snapshot = replace(_snapshot(_document()), modified_time="2026-09-03T00:00:00Z")
    gateway, store, operation = _Gateway(snapshot), _CopyCheckpointStore(), TaskId(uuid.uuid7())
    request = _in_place_request(mode).model_copy(update={"output_strategy": OutputStrategy.COPY})
    result = await _copy_executor(gateway=gateway, store=store, task_id=operation).execute(
        operation_id=operation, request=request
    )
    assert any(finding.code == "native_visual_authority_preserved" for finding in result.findings)
    assert store.checkpoints[0].replacements == {}
    assert not any(
        mutation.get(kind, {}).get("range", {}).get("startIndex") == 1
        and "segmentId" not in mutation[kind]["range"]
        for mutation in gateway.requests
        for kind in ("updateTextStyle", "updateParagraphStyle")
    )
    writes = gateway.applies, gateway.brand_prepares
    repeated = await _copy_executor(
        gateway=gateway, store=store, task_id=operation, checkpoint=store.checkpoints[-1]
    ).execute(operation_id=operation, request=request)
    assert repeated == result
    assert (gateway.applies, gateway.brand_prepares) == writes
    for update in (
        {"request_builder_version": "google-docs-requests-v26"},
        {"replacements": {"b0": "Changed title"}},
        {"findings": []},
    ):
        checkpoint = store.checkpoints[-1].model_copy(update=update)
        with pytest.raises(OutputConflictError):
            await _copy_executor(
                gateway=gateway, store=store, task_id=operation, checkpoint=checkpoint
            ).execute(operation_id=operation, request=request)
    assert (gateway.applies, gateway.brand_prepares) == writes


@pytest.mark.parametrize(
    "change", ["none", "size", "color", "family", "alignment", "terminal", "parent"]
)
def test_copy_visual_proof_requires_exact_authored_styles(change: str):
    source = _document()
    actual = deepcopy(source)
    paragraph = _paragraph(actual)
    style = paragraph["elements"][0]["textRun"]["textStyle"]
    if change == "size":
        style["fontSize"]["magnitude"] = 11
    elif change == "color":
        style["foregroundColor"]["color"]["rgbColor"] = {}
    elif change == "family":
        style["weightedFontFamily"] = {"fontFamily": "Nunito"}
    elif change == "alignment":
        paragraph["paragraphStyle"]["alignment"] = "START"
    elif change == "terminal":
        paragraph["elements"][-1]["textRun"]["textStyle"]["fontSize"]["magnitude"] = 11
    elif change == "parent":
        actual["tabs"][0]["documentTab"]["namedStyles"]["styles"][0]["textStyle"]["fontSize"][
            "magnitude"
        ] = 11
    assert verify_copy_visual_authority(
        source, actual, parse_google_document(source, title="")
    ) is (change == "none")


def test_copy_visual_proof_accounts_for_preceding_utf16_text_edits():
    source, actual = _document(), _document()
    _prepend(source, "An important sentence.")
    _prepend(actual, "A sentence.")
    expected = parse_google_document(source, title="").replace_blocks({"b0": "A sentence."})
    assert verify_copy_visual_authority(source, actual, expected)
    assert not verify_copy_visual_authority(source, actual, parse_google_document(source, title=""))


def _manual_title_with_linked_prose() -> dict[str, JsonValue]:
    source = _document()
    _prepend(source, "Earlier facts. Reference")
    parts = _paragraph(source)["elements"]
    parts[0]["endIndex"] = 16
    parts[0]["textRun"] = {
        "content": "Earlier facts. ",
        "textStyle": {"bold": False, "weightedFontFamily": {"fontFamily": "Arial", "weight": 400}},
    }
    parts.append(
        {
            "startIndex": 16,
            "endIndex": 25,
            "textRun": {
                "content": "Reference",
                "textStyle": {
                    "bold": False,
                    "weightedFontFamily": {"fontFamily": "Arial", "weight": 400},
                    "link": {"url": "https://example.test/reference"},
                },
            },
        }
    )
    parts.append(
        {
            "startIndex": 25,
            "endIndex": 26,
            "textRun": {
                "content": "\n",
                "textStyle": {
                    "bold": False,
                    "weightedFontFamily": {"fontFamily": "Arial", "weight": 400},
                },
            },
        }
    )
    return source


def test_exact_copy_keeps_visual_proof_independent_of_linked_prose_font():
    source = _manual_title_with_linked_prose()
    expected = parse_google_document(source, title="Source")
    assert expected.blocks[0].protected_suffix == "Reference"
    assert verify_copy_visual_authority(source, deepcopy(source), expected)


@pytest.mark.parametrize("change", ["none", "title", "citation_bold", "citation_url"])
async def test_visual_proof_scope_keeps_other_native_preservation_checks(
    change: str,
    copy_verification_gateway_factory: Callable[
        [FileId, dict[str, JsonValue]], GoogleDocumentGateway
    ],
):
    source = _manual_title_with_linked_prose()
    actual = deepcopy(source)
    tab = dict(bodies(actual))[DocsTabId("t.0")]
    paragraphs = [obj(obj(value)["paragraph"]) for value in array(obj(tab["body"])["content"])]
    paragraphs[0]["paragraphStyle"] = {"alignment": "JUSTIFIED"}
    runs = [obj(obj(value)["textRun"]) for value in array(paragraphs[0]["elements"])]
    for run in runs[:-1]:
        obj(obj(run["textStyle"])["weightedFontFamily"])["fontFamily"] = "Nunito"
    if change == "title":
        title = obj(obj(array(paragraphs[1]["elements"])[0])["textRun"])
        obj(obj(title["textStyle"])["fontSize"])["magnitude"] = 11
    elif change == "citation_bold":
        obj(runs[1]["textStyle"])["bold"] = True
    elif change == "citation_url":
        obj(obj(runs[1]["textStyle"])["link"])["url"] = "https://example.test/changed"
    expected = replace(
        apply_visual_authority(parse_google_document(source, title="Decision"), ("b1",)),
        copy_font_defended=True,
    )
    expected = apply_native_font_preservation(
        expected, copy_preserved_font_ids(source, expected, expected)
    )
    draft = _pending_output_draft(
        pending_mutation_sha256="hash", mutation_base_revision_sha256="hash"
    )
    gateway = copy_verification_gateway_factory(draft.document_id, actual)
    result = await gateway.verify_output(
        draft=draft,
        source=_snapshot(source),
        expected=expected,
        verify_brand=True,
        header_title="Decision",
    )
    assert result.structure_preserved is (change == "none")


def test_copy_cannot_promote_preserved_manual_labels_to_new_heading_roles():
    original = parse_google_document(_label_document(), title="Review")
    assert len(plan_authored_labels(original)) == 2
    assert not plan_authored_labels(apply_visual_authority(original, ("b0",)))


@pytest.mark.parametrize("tamper", [False, True])
async def test_copy_gateway_enforces_visual_proof(monkeypatch: pytest.MonkeyPatch, tamper: bool):
    source, actual = _document(), _document()
    if tamper:
        _paragraph(actual)["elements"][0]["textRun"]["textStyle"]["fontSize"]["magnitude"] = 11
    result = await _verify(monkeypatch, source, actual)
    assert result.content_preserved
    assert result.structure_preserved is not tamper


@pytest.mark.parametrize("rename", ["list", "tab", "object"])
async def test_copy_visual_guard_with_list_renaming_and_unproved_tab_identity(
    monkeypatch: pytest.MonkeyPatch, rename: str
):
    source, actual = _document(), _document()
    for document, identifier in ((source, "source-list"), (actual, "copy-list")):
        _prepend(document, "List item")
        tab = document["tabs"][0]["documentTab"]
        tab["body"]["content"][0]["paragraph"]["bullet"] = {"listId": identifier, "nestingLevel": 0}
        tab["lists"] = {identifier: {"listId": identifier}}
        tab["body"]["content"][0]["paragraph"]["elements"].append(
            {"inlineObjectElement": {"inlineObjectId": "image"}}
        )
        tab["inlineObjects"] = {
            "image": {"inlineObjectProperties": {"embeddedObject": {"title": "Protected Logo"}}}
        }
    if rename == "tab":
        actual["tabs"][0]["tabProperties"]["tabId"] = "unproved-copy-tab"
    elif rename == "object":
        tab = actual["tabs"][0]["documentTab"]
        tab["body"]["content"][0]["paragraph"]["elements"][-1]["inlineObjectElement"][
            "inlineObjectId"
        ] = "unproved-image"
        tab["inlineObjects"]["unproved-image"] = tab["inlineObjects"].pop("image")
    result = await _verify(monkeypatch, source, actual)
    assert result.structure_preserved is (rename == "list")


class _ManualTitleMove(NamedTuple):
    source: dict[str, Any]
    moving: StructureCheckpoint
    actual: dict[str, Any]


def _manual_title_move() -> _ManualTitleMove:
    source = _sectioned_document()
    native = source["tabs"][0]["documentTab"]
    native["namedStyles"] = deepcopy(_document()["tabs"][0]["documentTab"]["namedStyles"])
    content = native["body"]["content"]
    start = content[4]["startIndex"]
    title = deepcopy(_document()["tabs"][0]["documentTab"]["body"]["content"][0])
    for item in [title, *title["paragraph"]["elements"]]:
        item["startIndex"] += start - 1
        item["endIndex"] += start - 1
    for paragraph in content[4:]:
        for item in [paragraph, *paragraph["paragraph"]["elements"]]:
            item["startIndex"] += 6
            item["endIndex"] += 6
    content.insert(4, title)
    plan = plan_tab_structure(source, comments_verified_absent=True)
    assert plan is not None
    intent, _ = prepare_tab_creation(PlannedStructureCheckpoint(plan))
    created = bind_created_tabs(
        intent,
        tab_ids=(DocsTabId("new-delivery"), DocsTabId("new-risks")),
        revision_id=DocsRevisionId("r2"),
    )
    moving, _ = prepare_content_move(created)
    actual = _output(moving)
    actual["revisionId"] = "r3"
    for tab in actual["tabs"]:
        tab["documentTab"]["namedStyles"] = deepcopy(native["namedStyles"])
    assert verify_structure_content(moving, actual)
    return _ManualTitleMove(source, moving, actual)


async def test_visual_authority_survives_verified_move_to_new_tab(monkeypatch: pytest.MonkeyPatch):
    source, moving, actual = _manual_title_move()
    assert isinstance(moving, MovingStructureCheckpoint)
    snapshot = replace(_snapshot(source), modified_time="2026-09-03T00:00:00Z")
    gateway, store, operation = _Gateway(snapshot), _CopyCheckpointStore(), TaskId(uuid.uuid7())
    request = _in_place_request(TransformMode.BOTH).model_copy(
        update={"output_strategy": OutputStrategy.COPY}
    )
    await _copy_executor(gateway=gateway, store=store, task_id=operation).execute(
        operation_id=operation, request=request
    )
    checkpoint = store.checkpoints[-1].model_copy(
        update={"tab_structure": complete_structure(moving, moving.revision_id)}
    )
    original_verify = gateway.verify_output
    checks: list[bool] = []

    async def verify(
        *, source: SourceSnapshot, expected: DocumentIR, **kwargs: Any
    ) -> OutputVerification:
        preserved = [block for block in expected.blocks if block.preserved_visual_authority]
        assert len(preserved) == 1
        assert preserved[0].source_range is not None
        assert preserved[0].source_range.tab_id == "new-delivery"
        valid = verify_copy_visual_authority(source.google_document, actual, expected)
        checks.append(valid)
        return replace(
            await original_verify(source=source, expected=expected, **kwargs),
            structure_preserved=valid,
        )

    monkeypatch.setattr(gateway, "verify_output", verify)
    await _copy_executor(
        gateway=gateway, store=store, task_id=operation, checkpoint=checkpoint
    ).execute(operation_id=operation, request=request)
    assert checks
    assert all(checks)
    actual["tabs"][1]["documentTab"]["body"]["content"][2]["paragraph"]["elements"][0]["textRun"][
        "textStyle"
    ]["fontSize"]["magnitude"] = 11
    with pytest.raises(OutputVerificationError):
        await _copy_executor(
            gateway=gateway, store=store, task_id=operation, checkpoint=checkpoint
        ).execute(operation_id=operation, request=request)
    assert checks[-1] is False


class _CopyVerificationGateway(GoogleDocumentGateway):
    def __init__(self, file_id: FileId, document: dict[str, JsonValue]) -> None:
        self._documents = {file_id: deepcopy(document)}

    @override
    def _get_document_sync(self, file_id: FileId) -> dict[str, JsonValue]:
        return deepcopy(self._documents[file_id])


@pytest.fixture
def copy_verification_gateway_factory() -> Callable[
    [FileId, dict[str, JsonValue]], GoogleDocumentGateway
]:
    return _CopyVerificationGateway


@pytest.mark.parametrize("change", ["none", "visual", "nonexempt", "body", "structure"])
async def test_copy_supported_brand_requires_real_visual_and_structure_proof(
    change: str,
    copy_verification_gateway_factory: Callable[
        [FileId, dict[str, JsonValue]], GoogleDocumentGateway
    ],
) -> None:
    source = _branded_google_document()
    tab = source["tabs"][0]["documentTab"]
    ordinary = deepcopy(tab["body"]["content"][0])
    ordinary["startIndex"], ordinary["endIndex"] = 11, 21
    ordinary["paragraph"]["elements"][0]["startIndex"] = 11
    ordinary["paragraph"]["elements"][0]["endIndex"] = 21
    tab["body"]["content"].append(ordinary)
    authored = deepcopy(_paragraph(_document()))
    paragraph = tab["body"]["content"][0]["paragraph"]
    paragraph["paragraphStyle"] = authored["paragraphStyle"]
    paragraph["elements"][0]["textRun"]["textStyle"] = authored["elements"][0]["textRun"][
        "textStyle"
    ]
    actual = deepcopy(source)
    actual_tab = actual["tabs"][0]["documentTab"]
    if change == "visual":
        actual_tab["body"]["content"][0]["paragraph"]["elements"][0]["textRun"]["textStyle"][
            "fontSize"
        ]["magnitude"] = 11
    elif change == "nonexempt":
        actual_tab["body"]["content"][1]["paragraph"]["paragraphStyle"]["alignment"] = "CENTER"
    elif change == "body":
        actual_tab["body"]["content"].append({"table": {"tableRows": []}})
    elif change == "structure":
        actual["tabs"][0]["tabProperties"]["tabId"] = "unproved-copy-tab"
    expected = apply_visual_authority(parse_google_document(source, title="Decision"), ("b0",))
    assert tuple(block.id for block in expected.blocks if block.preserved_visual_authority) == (
        "b0",
    )
    draft = _pending_output_draft(
        pending_mutation_sha256="hash", mutation_base_revision_sha256="hash"
    )
    gateway = copy_verification_gateway_factory(draft.document_id, actual)
    result = await gateway.verify_output(
        draft=draft,
        source=_snapshot(source),
        expected=expected,
        verify_brand=True,
        header_title="Decision",
    )
    assert result.content_preserved
    assert not result.brand_applied
    assert result.supported_brand_applied is (change == "none")
    assert result.structure_preserved is (change in {"none", "nonexempt"})
