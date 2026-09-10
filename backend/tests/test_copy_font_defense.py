from copy import deepcopy
from dataclasses import replace
from typing import Any
import uuid

import pytest

from app.document_transform.brand import SarjBrandProfile, build_copy_mutations
from app.document_transform.copy_font_defense import (
    copy_preserved_font_ids,
    defend_copy_fonts,
    verify_copy_fonts,
)
from app.document_transform.errors import (
    DocumentGatewayError,
    OutputConflictError,
    OutputVerificationError,
)
from app.document_transform.gateway import OutputVerification, SourceSnapshot
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.ir import DocumentIR, parse_google_document, utf16_length
from app.document_transform.native_font_defense import apply_native_font_preservation
from app.document_transform.native_visual_authority import (
    apply_visual_authority,
    visual_authority_block_ids,
)
from app.document_transform.schemas import (
    CopyCheckpoint,
    FindingSeverity,
    OutputStrategy,
    TransformMode,
)
from app.document_transform.tab_structure import plan_tab_structure
from app.document_transform.tab_structure_execution import (
    StructureCheckpoint,
    StructureStage,
    bind_created_tabs,
    planned_structure_document,
    prepare_content_move,
    prepare_tab_creation,
)
from app.identifiers import DocsRevisionId, DocsTabId, TaskId
from tests import (
    test_copy_font_preservation as legacy,
    test_document_transform as transform,
    test_document_transform_header_provenance as provenance,
    test_document_transform_tab_structure as structure,
)


def document(*, known: bool = True, formatted: bool = False) -> dict[str, Any]:
    value = legacy._source()
    if known:
        for part in parts(value):
            part["textRun"]["textStyle"]["weightedFontFamily"] = {
                "fontFamily": "Nunito" if formatted else "Arial",
                "weight": 700 if part["textRun"]["textStyle"]["bold"] else 400,
            }
    return value


def parts(value: dict[str, Any]) -> list[dict[str, Any]]:
    return value["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["elements"]


def planned(value: dict[str, Any]) -> DocumentIR:
    original = replace(parse_google_document(value, title="Decision"), copy_font_defended=True)
    return apply_native_font_preservation(
        original, copy_preserved_font_ids(value, original, original)
    )


def mutations(value: dict[str, Any]) -> list[dict[str, Any]]:
    original = planned(value)
    return defend_copy_fonts(
        build_copy_mutations(
            original,
            original,
            profile=SarjBrandProfile(),
            headers=(),
            logo_uri="https://example.test/logo.png",
        ),
        document=value,
        original=original,
        transformed=original,
        font_family="Nunito",
    )


def test_copy_font_family_updates_explicitly_preserve_bold_weight_and_terminal():
    value = document()
    before = deepcopy(value)
    requests = [
        item["updateTextStyle"]
        for item in mutations(value)
        if "updateTextStyle" in item and not item["updateTextStyle"]["range"].get("segmentId")
    ]
    assert len(requests) == 3
    for update, part in zip(requests, parts(value), strict=True):
        style = part["textRun"]["textStyle"]
        assert update["fields"] == "weightedFontFamily,bold"
        assert update["textStyle"] == {
            "bold": style["bold"],
            "weightedFontFamily": {
                "fontFamily": "Nunito",
                "weight": style["weightedFontFamily"]["weight"],
            },
        }
        assert update["range"]["startIndex"] == part["startIndex"]
        assert update["range"]["endIndex"] == part["endIndex"]
    assert value == before
    assert verify_copy_fonts(
        value, document(formatted=True), planned(value), list_identity_equivalent=False
    )


@pytest.mark.parametrize("change", ["bold", "weight", "italic", "terminal", "family", "parent"])
def test_copy_font_semantics_reject_any_non_authorized_change(change: str):
    source, actual = document(), document(formatted=True)
    first = parts(actual)[0]["textRun"]["textStyle"]
    match change:
        case "bold":
            first["bold"] = False
        case "weight":
            first["weightedFontFamily"]["weight"] = 400
        case "italic":
            first["italic"] = True
        case "family":
            first["weightedFontFamily"]["fontFamily"] = "Arial"
        case "terminal":
            parts(actual)[-1]["textRun"]["textStyle"]["bold"] = True
        case _:
            actual["tabs"][0]["documentTab"]["namedStyles"]["styles"][0]["textStyle"]["bold"] = True
    assert not verify_copy_fonts(source, actual, planned(source), list_identity_equivalent=False)


def test_unknown_weight_preserves_exact_family_without_unsafe_requests():
    source = document(known=False)
    expected = planned(source)
    assert expected.blocks[0].preserved_font_family
    assert [set(item) for item in mutations(source)] == [{"updateDocumentStyle"}]
    assert verify_copy_fonts(source, source, expected, list_identity_equivalent=False)
    actual = deepcopy(source)
    parts(actual)[0]["textRun"]["textStyle"]["weightedFontFamily"] = {"fontFamily": "Nunito"}
    assert not verify_copy_fonts(source, actual, expected, list_identity_equivalent=False)


def test_already_target_font_is_exact_noop():
    source = document(formatted=True)
    assert [set(item) for item in mutations(source)] == [{"updateDocumentStyle"}]
    assert verify_copy_fonts(source, source, planned(source), list_identity_equivalent=False)


@pytest.mark.parametrize("known", [True, False])
async def test_copy_plan_binds_fonts_findings_manifest_and_completed_replay(*, known: bool):
    snapshot = replace(
        provenance._snapshot(document(known=known)), modified_time="2026-09-03T00:00:00Z"
    )
    gateway, store, operation = (
        transform._Gateway(snapshot),
        transform._CopyCheckpointStore(),
        TaskId(uuid.uuid7()),
    )
    request = transform._in_place_request(TransformMode.FORMAT).model_copy(
        update={"output_strategy": OutputStrategy.COPY}
    )
    result = await transform._copy_executor(
        gateway=gateway, store=store, task_id=operation
    ).execute(operation_id=operation, request=request)
    checkpoint = store.checkpoints[-1]
    assert checkpoint.request_builder_version.endswith("-copy-font-v1")
    assert (
        any(finding.code == "native_font_family_preserved" for finding in result.findings)
        is not known
    )
    if not known:
        assert result.needs_review is False
        assert all(
            finding.severity is FindingSeverity.INFORMATIONAL
            for finding in result.findings
            if finding.code
            in {"google_doc_text_target_unsupported", "native_font_family_preserved"}
        )
    serialized_checkpoint = checkpoint.model_dump(mode="json")
    for finding, serialized_finding in zip(
        checkpoint.findings, serialized_checkpoint["findings"], strict=True
    ):
        assert ("severity" in serialized_finding) is (
            finding.severity is FindingSeverity.INFORMATIONAL
        )
    reloaded_checkpoint = CopyCheckpoint.model_validate(serialized_checkpoint)
    writes = gateway.applies, gateway.brand_prepares
    assert (
        await transform._copy_executor(
            gateway=gateway, store=store, task_id=operation, checkpoint=reloaded_checkpoint
        ).execute(operation_id=operation, request=request)
        == result
    )
    assert (gateway.applies, gateway.brand_prepares) == writes
    for update in (
        {"request_builder_version": "google-docs-requests-v26"},
        {"manifest_sha256": "wrong"},
        {"findings": []} if not known else {"plan_sha256": "wrong"},
    ):
        with pytest.raises(OutputConflictError):
            await transform._copy_executor(
                gateway=gateway,
                store=store,
                task_id=operation,
                checkpoint=checkpoint.model_copy(update=update),
            ).execute(operation_id=operation, request=request)
    assert (gateway.applies, gateway.brand_prepares) == writes


async def test_interrupted_copy_replays_identical_defended_requests(
    monkeypatch: pytest.MonkeyPatch,
):
    snapshot = replace(provenance._snapshot(document()), modified_time="2026-09-03T00:00:00Z")
    gateway, store, operation = (
        transform._Gateway(snapshot),
        transform._CopyCheckpointStore(),
        TaskId(uuid.uuid7()),
    )
    request = transform._in_place_request(TransformMode.FORMAT).model_copy(
        update={"output_strategy": OutputStrategy.COPY}
    )
    original_apply = gateway.apply_requests_once
    attempts: list[list[dict[str, Any]]] = []

    async def interrupted(**kwargs: Any):
        attempts.append(deepcopy(kwargs["requests"]))
        if len(attempts) == 1:
            raise DocumentGatewayError
        return await original_apply(**kwargs)

    monkeypatch.setattr(gateway, "apply_requests_once", interrupted)
    with pytest.raises(DocumentGatewayError):
        await transform._copy_executor(gateway=gateway, store=store, task_id=operation).execute(
            operation_id=operation, request=request
        )
    await transform._copy_executor(
        gateway=gateway, store=store, task_id=operation, checkpoint=store.checkpoints[-1]
    ).execute(operation_id=operation, request=request)
    assert len(attempts) == 2
    assert attempts[0] == attempts[1]


@pytest.mark.parametrize("tamper", [False, True])
async def test_public_copy_gateway_requires_semantic_font_attestation(
    monkeypatch: pytest.MonkeyPatch, *, tamper: bool
):
    source, actual = document(), document(formatted=True)
    if tamper:
        parts(actual)[0]["textRun"]["textStyle"]["weightedFontFamily"]["weight"] = 400
    gateway = object.__new__(GoogleDocumentGateway)
    monkeypatch.setattr(gateway, "_get_document_sync", lambda _: deepcopy(actual))
    result = await gateway.verify_output(
        draft=transform._pending_output_draft(
            pending_mutation_sha256="hash", mutation_base_revision_sha256="hash"
        ),
        source=provenance._snapshot(source),
        expected=planned(source),
        verify_brand=True,
        header_title="Decision",
    )
    assert result.content_preserved
    assert result.brand_applied
    assert result.structure_preserved is not tamper


def test_unknown_family_keeps_entire_rich_paragraph_exact():
    source = document()
    parts(source)[0]["textRun"]["textStyle"]["weightedFontFamily"]["fontFamily"] = "Courier New"
    expected = planned(source)
    assert expected.blocks[0].preserved_font_family
    assert [set(item) for item in mutations(source)] == [{"updateDocumentStyle"}]
    assert verify_copy_fonts(source, source, expected, list_identity_equivalent=False)
    actual = deepcopy(source)
    parts(actual)[1]["textRun"]["textStyle"]["weightedFontFamily"]["fontFamily"] = "Nunito"
    assert not verify_copy_fonts(source, actual, expected, list_identity_equivalent=False)


def test_mixed_visual_title_and_rich_body_have_independent_exact_proofs():
    source = document()
    body = source["tabs"][0]["documentTab"]["body"]["content"]
    for item in [body[0], *parts(source)]:
        item["startIndex"] += 6
        item["endIndex"] += 6
    body.insert(
        0,
        {
            "startIndex": 1,
            "endIndex": 7,
            "paragraph": {
                "elements": [
                    {
                        "startIndex": 1,
                        "endIndex": 7,
                        "textRun": {
                            "content": "Cover\n",
                            "textStyle": {"fontSize": {"magnitude": 30, "unit": "PT"}},
                        },
                    }
                ]
            },
        },
    )
    original = parse_google_document(source, title="Decision")
    original = replace(
        apply_visual_authority(original, visual_authority_block_ids(source, original)),
        copy_font_defended=True,
    )
    expected = apply_native_font_preservation(
        original, copy_preserved_font_ids(source, original, original)
    )
    assert expected.blocks[0].preserved_font_family
    assert not expected.blocks[1].preserved_font_family
    actual = deepcopy(source)
    for item in actual["tabs"][0]["documentTab"]["body"]["content"][1]["paragraph"]["elements"]:
        item["textRun"]["textStyle"]["weightedFontFamily"]["fontFamily"] = "Nunito"
    assert verify_copy_fonts(source, actual, expected, list_identity_equivalent=False)
    actual["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["elements"][0]["textRun"][
        "textStyle"
    ]["fontSize"]["magnitude"] = 11
    assert not verify_copy_fonts(source, actual, expected, list_identity_equivalent=False)


def test_provider_fragmentation_preserves_exact_residual_and_weight_intervals():
    source, actual = document(), document(formatted=True)
    first = parts(actual)[0]
    second = deepcopy(first)
    first["endIndex"] = 3
    first["textRun"]["content"] = "Al"
    second["startIndex"] = 3
    second["textRun"]["content"] = "pha "
    parts(actual).insert(1, second)
    assert verify_copy_fonts(source, actual, planned(source), list_identity_equivalent=False)
    second["textRun"]["textStyle"]["weightedFontFamily"]["weight"] = 400
    assert not verify_copy_fonts(source, actual, planned(source), list_identity_equivalent=False)


async def test_checkpoint_manifest_binds_source_bold_and_weight_before_writes():
    snapshot = replace(provenance._snapshot(document()), modified_time="2026-09-03T00:00:00Z")
    gateway, store, operation = (
        transform._Gateway(snapshot),
        transform._CopyCheckpointStore(),
        TaskId(uuid.uuid7()),
    )
    request = transform._in_place_request(TransformMode.FORMAT).model_copy(
        update={"output_strategy": OutputStrategy.COPY}
    )
    await transform._copy_executor(gateway=gateway, store=store, task_id=operation).execute(
        operation_id=operation, request=request
    )
    before = gateway.applies, gateway.brand_prepares
    assert gateway.snapshot is not None
    assert gateway.snapshot.google_document is not None
    parts(gateway.snapshot.google_document)[0]["textRun"]["textStyle"]["weightedFontFamily"][
        "weight"
    ] = 400
    with pytest.raises(OutputConflictError):
        await transform._copy_executor(
            gateway=gateway, store=store, task_id=operation, checkpoint=store.checkpoints[-1]
        ).execute(operation_id=operation, request=request)
    assert (gateway.applies, gateway.brand_prepares) == before


def test_preceding_utf16_edit_preserves_rich_family_and_binds_shifted_positions():
    source = document()
    body = source["tabs"][0]["documentTab"]["body"]["content"]
    for item in [body[0], *parts(source)]:
        item["startIndex"] += 5
        item["endIndex"] += 5
    body.insert(
        0,
        {
            "startIndex": 1,
            "endIndex": 6,
            "paragraph": {
                "elements": [{"startIndex": 1, "endIndex": 6, "textRun": {"content": "😀ab\n"}}]
            },
        },
    )
    original = replace(parse_google_document(source, title="Decision"), copy_font_defended=True)
    changed = original.replace_blocks({original.blocks[0].id: "a"})
    expected = apply_native_font_preservation(
        changed, copy_preserved_font_ids(source, original, changed)
    )
    assert expected.blocks[1].preserved_font_family
    actual = deepcopy(source)
    actual_body = actual["tabs"][0]["documentTab"]["body"]["content"]
    actual_body[0]["endIndex"] = 3
    actual_body[0]["paragraph"]["elements"][0].update(
        {"endIndex": 3, "textRun": {"content": "a\n"}}
    )
    for item in [actual_body[1], *actual_body[1]["paragraph"]["elements"]]:
        item["startIndex"] -= 3
        item["endIndex"] -= 3
    assert verify_copy_fonts(source, actual, expected, list_identity_equivalent=False)
    actual_body[1]["paragraph"]["elements"][0]["textRun"]["textStyle"]["bold"] = False
    assert not verify_copy_fonts(source, actual, expected, list_identity_equivalent=False)


def test_verified_list_renaming_does_not_change_unrelated_rich_font_selection():
    source = document()
    tab = source["tabs"][0]["documentTab"]
    second = deepcopy(tab["body"]["content"][0])
    for item in [second, *second["paragraph"]["elements"]]:
        item["startIndex"] += 11
        item["endIndex"] += 11
    second["paragraph"]["bullet"] = {"listId": "original-list", "nestingLevel": 0}
    tab["body"]["content"].append(second)
    tab["lists"] = {"original-list": {"listId": "original-list"}}
    expected = planned(source)
    assert not expected.blocks[0].preserved_font_family
    assert expected.blocks[1].preserved_font_family
    actual = deepcopy(source)
    for item in parts(actual):
        item["textRun"]["textStyle"]["weightedFontFamily"]["fontFamily"] = "Nunito"
    actual_tab = actual["tabs"][0]["documentTab"]
    actual_tab["body"]["content"][1]["paragraph"]["bullet"]["listId"] = "copy-list"
    actual_tab["lists"] = {"copy-list": {"listId": "copy-list"}}
    assert verify_copy_fonts(source, actual, expected, list_identity_equivalent=True)
    assert not verify_copy_fonts(source, actual, expected, list_identity_equivalent=False)


def sectioned_rich_document() -> dict[str, Any]:
    source = structure._document()
    content = source["tabs"][0]["documentTab"]["body"]["content"]
    for element in content:
        if "paragraph" not in element:
            continue
        paragraph = element["paragraph"]
        run = paragraph["elements"][0]
        run["textRun"]["textStyle"]["weightedFontFamily"] = {
            "fontFamily": "Arial",
            "weight": 400,
        }
        if paragraph["paragraphStyle"]["namedStyleType"] == "NORMAL_TEXT":
            second = deepcopy(run)
            run["endIndex"] = run["startIndex"] + 1
            run["textRun"]["content"] = run["textRun"]["content"][:1]
            second["startIndex"] += 1
            second["textRun"]["content"] = second["textRun"]["content"][1:]
            second["textRun"]["textStyle"].update(
                {"bold": True, "weightedFontFamily": {"fontFamily": "Arial", "weight": 700}}
            )
            paragraph["elements"].append(second)
    for text, bold in reversed((("Owner:\n", True), ("A brief explanation.\n", False))):
        content.insert(
            2,
            {
                "paragraph": {
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    "elements": [{"textRun": {"content": text, "textStyle": {"bold": bold}}}],
                }
            },
        )
    index = 1
    for element in content[1:]:
        element["startIndex"] = index
        for run in element["paragraph"]["elements"]:
            run["startIndex"] = index
            index += utf16_length(run["textRun"]["content"])
            run["endIndex"] = index
        element["endIndex"] = index
    return source


def verified_sections(source: dict[str, Any]) -> StructureCheckpoint:
    plan = plan_tab_structure(source, comments_verified_absent=True)
    assert plan is not None
    intent, _ = prepare_tab_creation(StructureCheckpoint(plan))
    created = bind_created_tabs(
        intent,
        tab_ids=(DocsTabId("delivery"), DocsTabId("risks")),
        revision_id=DocsRevisionId("r2"),
    )
    moving, _ = prepare_content_move(created)
    return replace(moving, stage=StructureStage.VERIFIED)


async def test_completed_copy_tab_projection_retains_bound_font_preservation(
    monkeypatch: pytest.MonkeyPatch,
):
    source = sectioned_rich_document()
    snapshot = replace(provenance._snapshot(source), modified_time="2026-09-03T00:00:00Z")
    gateway, store, operation = (
        transform._Gateway(snapshot),
        transform._CopyCheckpointStore(),
        TaskId(uuid.uuid7()),
    )
    request = transform._in_place_request(TransformMode.FORMAT).model_copy(
        update={"output_strategy": OutputStrategy.COPY}
    )
    await transform._copy_executor(gateway=gateway, store=store, task_id=operation).execute(
        operation_id=operation, request=request
    )
    saved = store.checkpoints[-1]
    assert saved.pagination
    assert sum(f.code == "native_font_family_preserved" for f in saved.findings) == 3
    verified = verified_sections(source)
    checkpoint = saved.model_copy(update={"tab_structure": verified})
    actual = planned_structure_document(verified, source)
    public_gateway = object.__new__(GoogleDocumentGateway)
    monkeypatch.setattr(public_gateway, "_get_document_sync", lambda _: deepcopy(actual))
    original_verify = gateway.verify_output
    checks: list[bool] = []

    async def verify(
        *, source: SourceSnapshot, expected: DocumentIR, **kwargs: Any
    ) -> OutputVerification:
        assert any(block.pagination_keep_with_next is not None for block in expected.blocks)
        assert sum(block.preserved_font_family for block in expected.blocks) == 3
        result = await public_gateway.verify_output(source=source, expected=expected, **kwargs)
        checks.append(result.structure_preserved)
        return replace(
            await original_verify(source=source, expected=expected, **kwargs),
            structure_preserved=result.structure_preserved,
        )

    monkeypatch.setattr(gateway, "verify_output", verify)
    writes = gateway.applies, gateway.brand_prepares
    await transform._copy_executor(
        gateway=gateway, store=store, task_id=operation, checkpoint=checkpoint
    ).execute(operation_id=operation, request=request)
    assert checks
    assert all(checks)
    assert (gateway.applies, gateway.brand_prepares) == writes
    actual["tabs"][1]["documentTab"]["body"]["content"][2]["paragraph"]["elements"][1]["textRun"][
        "textStyle"
    ]["weightedFontFamily"]["weight"] = 400
    with pytest.raises(OutputVerificationError):
        await transform._copy_executor(
            gateway=gateway, store=store, task_id=operation, checkpoint=checkpoint
        ).execute(operation_id=operation, request=request)
    assert checks[-1] is False
    assert (gateway.applies, gateway.brand_prepares) == writes
