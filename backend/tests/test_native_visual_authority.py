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
from app.document_transform.gateway import InPlaceStageResult
from app.document_transform.ir import parse_google_document, utf16_length
from app.document_transform.native_font_proof import (
    NativeFontVerificationPolicy,
    NativeVisualAuthoritySelection,
    capture_native_font_proof,
    verify_native_font_proof,
)
from app.document_transform.native_visual_authority import (
    apply_visual_authority,
    visual_authority_block_ids,
)
from app.document_transform.schemas import TransformMode
from app.identifiers import DocsRevisionId, TaskId
from tests.test_document_transform import (
    _CheckpointStore,
    _Generator,
    _in_place_request,
    _InPlaceGateway,
)
from tests.test_native_font_proof_v2 import _proofs, _source


def _document(size: float = 30) -> dict[str, Any]:
    return {
        "revisionId": "revision",
        "tabs": [
            {
                "tabProperties": {"tabId": "t.0"},
                "documentTab": {
                    "namedStyles": {
                        "styles": [
                            {
                                "namedStyleType": "NORMAL_TEXT",
                                "textStyle": {
                                    "fontSize": {"magnitude": 12, "unit": "PT"},
                                    "foregroundColor": {"color": {"rgbColor": {}}},
                                },
                            }
                        ]
                    },
                    "body": {
                        "content": [
                            {
                                "startIndex": 1,
                                "endIndex": 7,
                                "paragraph": {
                                    "paragraphStyle": {
                                        "namedStyleType": "NORMAL_TEXT",
                                        "alignment": "CENTER",
                                    },
                                    "elements": [
                                        {
                                            "startIndex": 1,
                                            "endIndex": 6,
                                            "textRun": {
                                                "content": "Title",
                                                "textStyle": {
                                                    "fontSize": {"magnitude": size, "unit": "PT"},
                                                    "foregroundColor": {
                                                        "color": {"rgbColor": {"blue": 1}}
                                                    },
                                                },
                                            },
                                        },
                                        {
                                            "startIndex": 6,
                                            "endIndex": 7,
                                            "textRun": {
                                                "content": "\n",
                                                "textStyle": {
                                                    "fontSize": {"magnitude": 12, "unit": "PT"}
                                                },
                                            },
                                        },
                                    ],
                                },
                            }
                        ]
                    },
                },
            }
        ],
    }


def _paragraph(document: dict[str, Any]) -> dict[str, Any]:
    return document["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]


@pytest.mark.parametrize("size", [20, 26.5, 30])
def test_manual_title_preserved_without_new_semantic_role(size: float):
    document = _document(size)
    original = parse_google_document(document, title="Source")
    assert original.blocks[0].editable
    ids = visual_authority_block_ids(document, original)
    assert ids == ("b0",)
    preserved = apply_visual_authority(original, ids)
    assert preserved.semantic_sha256 == original.semantic_sha256
    requests = build_copy_mutations(
        preserved,
        preserved,
        profile=SarjBrandProfile(),
        headers=(),
        logo_uri="https://example.test/logo.png",
    )
    assert not any(
        "updateTextStyle" in request or "updateParagraphStyle" in request for request in requests
    )
    assert not apply_visual_authority(original, ids, planner=True).blocks[0].editable
    proof = capture_native_font_proof(
        document,
        preserved,
        preserved_family_block_ids=ids,
        target_font_family="Nunito",
        visual_authority=NativeVisualAuthoritySelection(ids),
    )
    assert proof[0].visual_authority
    assert verify_native_font_proof(
        document,
        proof,
        font_policy=NativeFontVerificationPolicy(
            preserved_family_block_ids=ids, require_target_family=True
        ),
    )


@pytest.mark.parametrize(
    "field", ["fontSize", "foregroundColor", "alignment", "terminal", "parent"]
)
def test_visual_proof_rejects_nonfamily_and_context_drift(field: str):
    document = _document()
    original = parse_google_document(document, title="Source")
    proof = capture_native_font_proof(
        document,
        original,
        preserved_family_block_ids=("b0",),
        target_font_family="Nunito",
        visual_authority=NativeVisualAuthoritySelection(("b0",)),
    )
    changed = deepcopy(document)
    paragraph = _paragraph(changed)
    match field:
        case "alignment":
            paragraph["paragraphStyle"]["alignment"] = "START"
        case "terminal":
            paragraph["elements"][-1]["textRun"]["textStyle"]["fontSize"]["magnitude"] = 11
        case "parent":
            changed["tabs"][0]["documentTab"]["namedStyles"]["styles"][0]["textStyle"]["fontSize"][
                "magnitude"
            ] = 11
        case _:
            paragraph["elements"][0]["textRun"]["textStyle"].pop(field)
    assert not verify_native_font_proof(
        changed, proof, font_policy=NativeFontVerificationPolicy(preserved_family_block_ids=("b0",))
    )


def test_terminal_only_overrides_and_equivalent_black_are_not_authority():
    document = _document(12)
    paragraph = _paragraph(document)
    paragraph["elements"][0]["textRun"]["textStyle"]["foregroundColor"] = {
        "color": {"rgbColor": {"red": 0, "green": 0, "blue": 0}}
    }
    paragraph["elements"][-1]["textRun"]["textStyle"]["fontSize"]["magnitude"] = 30
    assert (
        visual_authority_block_ids(document, parse_google_document(document, title="Source")) == ()
    )


def test_unknown_explicit_presentation_is_preserved():
    document = _document()
    _paragraph(document)["elements"][0]["textRun"]["textStyle"]["fontSize"]["unit"] = "UNKNOWN"
    assert visual_authority_block_ids(
        document, parse_google_document(document, title="Source")
    ) == ("b0",)


def test_existing_v25_semantic_digest_is_unchanged():
    assert (
        _proofs(_source())[0].sha256
        == "4e8cf547c3575b9e71095a7761645e11e760fa9ad57b729e2528305219a69b84"
    )


def test_visual_authority_without_font_ranges_has_exact_preservation_proof():
    document = _document()
    original = parse_google_document(document, title="Source")
    original = replace(
        original, blocks=tuple(replace(block, font_style_ranges=()) for block in original.blocks)
    )
    proofs = capture_native_font_proof(
        document,
        original,
        target_font_family="Nunito",
        preserved_family_block_ids=("b0",),
        visual_authority=NativeVisualAuthoritySelection(("b0",)),
    )
    assert len(proofs) == 1
    assert verify_native_font_proof(
        document,
        proofs,
        font_policy=NativeFontVerificationPolicy(
            preserved_family_block_ids=("b0",), require_target_family=True
        ),
    )


@pytest.mark.parametrize("tamper", ["none", "old_version", "duplicate_ids", "missing_ids"])
@pytest.mark.parametrize("preceding_edit", [False, True])
async def test_visual_authority_both_checkpoint_replay_is_bound(
    monkeypatch: pytest.MonkeyPatch, tamper: str, *, preceding_edit: bool
):
    document = _document()
    if preceding_edit:
        _prepend(document, "Moreover, we approved 😀.")
    document["revisionId"] = "revision-1"
    gateway = _InPlaceGateway("Source")
    assert gateway.snapshot is not None
    gateway.snapshot = replace(gateway.snapshot, google_document=document, web_url=None)
    gateway.document = document
    store = _CheckpointStore(gateway.events)
    attempts: list[list[dict[str, Any]]] = []
    task_id, operation = TaskId(uuid.uuid7()), uuid.uuid7()

    async def apply(**kwargs: Any) -> InPlaceStageResult:
        attempts.append(deepcopy(kwargs["requests"]))
        if len(attempts) == 1:
            raise DocumentGatewayError
        if preceding_edit:
            changed = _document()
            _prepend(changed, "We approved 😀.")
            changed["revisionId"] = "revision-1"
            document.clear()
            document.update(changed)
        return InPlaceStageResult(DocsRevisionId("revision-1"), deepcopy(document))

    monkeypatch.setattr(gateway, "apply_in_place_stage", apply)

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

    request = _in_place_request(TransformMode.BOTH)
    with pytest.raises(DocumentGatewayError):
        await executor().execute(operation_id=operation, request=request)
    checkpoint = store.checkpoints[-1]
    visual_id = "b1" if preceding_edit else "b0"
    assert checkpoint.visual_authority_block_ids == (visual_id,)
    assert checkpoint.replacements == ({"b0": "We approved 😀."} if preceding_edit else {})
    assert all(
        all(
            not mutation.get(key)
            or mutation[key]["range"].get("segmentId")
            or (preceding_edit and mutation[key]["range"]["startIndex"] == 1)
            for key in ("updateTextStyle", "updateParagraphStyle")
        )
        for mutation in checkpoint.requests
    )
    saved_requests = deepcopy(checkpoint.requests)
    if tamper != "none":
        updates = (
            {"request_builder_version": "google-docs-requests-v25"}
            if tamper == "old_version"
            else {
                "visual_authority_block_ids": (visual_id, visual_id)
                if tamper == "duplicate_ids"
                else ()
            }
        )
        with pytest.raises((OutputConflictError, InPlaceNeedsReviewError)):
            await executor(checkpoint.model_copy(update=updates)).execute(
                operation_id=operation, request=request
            )
        assert len(attempts) == 1
        assert checkpoint.requests == saved_requests
        return
    result = await executor(checkpoint).execute(operation_id=operation, request=request)
    assert attempts[0] == attempts[1]
    assert any(finding.code == "native_visual_authority_preserved" for finding in result.findings)
    assert (
        await executor(store.checkpoints[-1]).execute(operation_id=operation, request=request)
        == result
    )
    assert len(attempts) == 2


def _prepend(document: dict[str, Any], text: str) -> None:
    body = document["tabs"][0]["documentTab"]["body"]["content"]
    offset = utf16_length(text + "\n")
    title = body[0]
    for item in [title, *title["paragraph"]["elements"]]:
        item["startIndex"] += offset
        item["endIndex"] += offset
    body.insert(
        0,
        {
            "startIndex": 1,
            "endIndex": offset + 1,
            "paragraph": {
                "elements": [
                    {"startIndex": 1, "endIndex": offset + 1, "textRun": {"content": text + "\n"}}
                ]
            },
        },
    )
