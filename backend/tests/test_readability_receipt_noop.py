from dataclasses import replace
from typing import Any, Literal, assert_never, override
import uuid

import pytest

from app.document_transform.copy_draft_state import CompletedDraft, IncompleteDraft
from app.document_transform.executor import DocumentTransformExecutor
from app.document_transform.gateway import OutputDraft
from app.document_transform.schemas import DocumentTransformRequest, MarkdownSource, TransformMode
from app.identifiers import FileId, TaskId
from tests.test_document_transform import _CopyCheckpointStore, _Gateway, _Generator


class _ReceiptGateway(_Gateway):
    def __init__(self) -> None:
        super().__init__()
        self.completions = 0

    @override
    async def complete(
        self, *, operation_id: uuid.UUID, draft: OutputDraft, result: dict[str, Any]
    ) -> None:
        self.completions += 1
        await super().complete(operation_id=operation_id, draft=draft, result=result)


@pytest.mark.parametrize("drift", [None, "completed", "result_sha256", "verified_revision_sha256"])
async def test_readable_copy_finalization_writes_only_a_missing_or_changed_receipt(
    drift: Literal["completed", "result_sha256", "verified_revision_sha256"] | None,
) -> None:
    gateway = _ReceiptGateway()
    task_id = TaskId(uuid.uuid7())
    request = DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown",
            content="The team retains the reference measurements.",
            name="Receipt verification",
            destination_folder_id=FileId("folder"),
        ),
        mode=TransformMode.FORMAT,
    )
    executor = DocumentTransformExecutor(
        gateway=gateway,
        generator=_Generator(),
        checkpoint_store=_CopyCheckpointStore(),
        task_id=task_id,
        lease_token=uuid.uuid7(),
    )
    result = await executor.execute(operation_id=task_id, request=request)
    assert gateway.draft is not None
    assert gateway.completions == 1
    receipt = gateway.draft.completion
    assert isinstance(receipt, CompletedDraft)
    match drift:
        case "completed":
            gateway.draft = replace(gateway.draft, completion=IncompleteDraft())
        case "result_sha256":
            gateway.draft = replace(
                gateway.draft,
                completion=CompletedDraft(
                    result_sha256="0" * 64,
                    verified_revision_sha256=receipt.verified_revision_sha256,
                ),
            )
        case "verified_revision_sha256":
            gateway.draft = replace(
                gateway.draft,
                completion=CompletedDraft(
                    result_sha256=receipt.result_sha256,
                    verified_revision_sha256="0" * 64,
                ),
            )
        case None:
            pass
        case _:
            assert_never(drift)

    await executor._finalize_readable_copy(operation_id=task_id, result=result)
    assert gateway.completions == (1 if drift is None else 2)
    await executor._finalize_readable_copy(operation_id=task_id, result=result)
    assert gateway.completions == (1 if drift is None else 2)
