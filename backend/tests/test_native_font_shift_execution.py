from copy import deepcopy
from dataclasses import replace
from typing import Any
import uuid

import pytest

from app.document_transform.errors import DocumentGatewayError, InPlaceNeedsReviewError
from app.document_transform.executor import DocumentTransformExecutor
from app.document_transform.gateway import InPlaceStageResult, SourceSnapshot
from app.document_transform.ir import parse_google_document, utf16_length
from app.document_transform.schemas import DriveFileSource, TransformMode
from app.identifiers import DocsRevisionId, TaskId
from tests.test_document_transform import (
    _CheckpointStore,
    _Generator,
    _in_place_request,
    _InPlaceGateway,
)
from tests.test_native_preservation_findings import _native


def _document(prefix: str) -> dict[str, Any]:
    document = _native()
    document["documentId"] = "source"
    document["revisionId"] = "revision-1"
    body = document["tabs"][0]["documentTab"]["body"]["content"]
    offset = utf16_length(prefix + "\n")
    paragraph = body[0]
    for item in [paragraph, *paragraph["paragraph"]["elements"]]:
        item["startIndex"] += offset
        item["endIndex"] += offset
    body.insert(
        0,
        {
            "startIndex": 1,
            "endIndex": 1 + offset,
            "paragraph": {
                "elements": [
                    {
                        "startIndex": 1,
                        "endIndex": 1 + offset,
                        "textRun": {"content": prefix + "\n", "textStyle": {}},
                    }
                ]
            },
        },
    )
    return document


@pytest.mark.parametrize("tamper", ["none", "bold", "relocation"])
async def test_preceding_edit_preserves_shifted_rich_style_and_exact_replay(
    monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    document = _document("Moreover, we approved.")
    gateway = _InPlaceGateway("Source")
    assert gateway.snapshot is not None
    gateway.snapshot = replace(gateway.snapshot, google_document=document, web_url=None)
    gateway.document = document
    store = _CheckpointStore(gateway.events)
    attempts: list[list[dict[str, Any]]] = []
    task_id = TaskId(uuid.uuid7())
    operation = uuid.uuid7()

    async def load(_source: DriveFileSource) -> SourceSnapshot:
        assert gateway.snapshot is not None
        return replace(gateway.snapshot, google_document=document)

    async def apply(**kwargs: Any) -> InPlaceStageResult:
        attempts.append(deepcopy(kwargs["requests"]))
        if len(attempts) == 1:
            raise DocumentGatewayError
        changed = _document("We approved.")
        paragraph = changed["tabs"][0]["documentTab"]["body"]["content"][1]
        parts = paragraph["paragraph"]["elements"]
        if tamper == "bold":
            parts[0]["textRun"]["textStyle"]["bold"] = False
        elif tamper == "relocation":
            for item in [paragraph, *parts]:
                item["startIndex"] += 1
                item["endIndex"] += 1
        document.clear()
        document.update(changed)
        return InPlaceStageResult(DocsRevisionId("revision-1"), deepcopy(document))

    monkeypatch.setattr(gateway, "load_drive_source", load)
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
    assert checkpoint.preserved_font_block_ids == ("b1",)
    if tamper != "none":
        with pytest.raises(InPlaceNeedsReviewError):
            await executor(checkpoint).execute(operation_id=operation, request=request)
        return
    result = await executor(checkpoint).execute(operation_id=operation, request=request)
    assert result.changed
    assert not result.needs_review
    assert attempts[0] == attempts[1]
    assert [block.text for block in parse_google_document(document, title="Source").blocks] == [
        "We approved.",
        "Alpha Beta",
    ]
    assert any(finding.code == "native_font_family_preserved" for finding in result.findings)
    assert all(finding.severity.value == "informational" for finding in result.findings)
    replay = await executor(store.checkpoints[-1]).execute(operation_id=operation, request=request)
    assert replay == result
    assert len(attempts) == 2
