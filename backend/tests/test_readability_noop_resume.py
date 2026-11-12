from copy import deepcopy
from typing import override
import uuid

from pydantic import JsonValue
import pytest

from app.document_transform.errors import ModelUnavailableError, OutputConflictError
from app.document_transform.executor import DocumentTransformExecutor
from app.document_transform.ir import DocumentIR
from app.document_transform.readability_provider import ReadabilityProvider
from app.document_transform.readability_types import ReadabilityCheckpoint, ReadabilityProposal
from app.document_transform.schemas import DocumentTransformRequest, MarkdownSource, TransformMode
from app.identifiers import FileId, TaskId
from tests.test_document_readability import _fixture, _LayoutEditor
from tests.test_document_transform import (
    _CheckpointStore,
    _CopyCheckpointStore,
    _Gateway,
    _Generator,
    _in_place_request,
    _InPlaceGateway,
)
from tests.test_readability_execution import _resume_executor


class _NoChangeEditor(_LayoutEditor):
    @override
    async def plan_readability(
        self, document: DocumentIR, *, previous: list[ReadabilityProposal], feedback: str = ""
    ) -> list[ReadabilityProposal]:
        self.calls += 1
        if self.unavailable:
            raise ModelUnavailableError
        return []


@pytest.mark.parametrize("outage", [False, True])
@pytest.mark.parametrize("in_place", [False, True])
async def test_no_change_layout_review_is_checkpointed_and_never_regenerated_on_retry(
    *, outage: bool, in_place: bool
) -> None:
    gateway: _InPlaceGateway | _Gateway
    store: _CheckpointStore | _CopyCheckpointStore
    if in_place:
        gateway = _InPlaceGateway("The team retains the source.")
        store = _CheckpointStore(gateway.events)
    else:
        gateway = _Gateway()
        store = _CopyCheckpointStore()
    task_id = TaskId(uuid.uuid7())
    request = (
        _in_place_request(TransformMode.FORMAT)
        if in_place
        else DocumentTransformRequest(
            source=MarkdownSource(
                kind="markdown",
                content="An exact source paragraph.",
                name="Readability fixture",
                destination_folder_id=FileId("folder"),
            ),
            mode=TransformMode.FORMAT,
        )
    )
    base = await DocumentTransformExecutor(
        gateway=gateway,
        generator=_Generator(),
        checkpoint_store=store,
        task_id=task_id,
        lease_token=uuid.uuid7(),
    ).execute(operation_id=task_id, request=request)
    document = _fixture("after")
    document.update({"documentId": base.output.file_id, "revisionId": base.output_revision_id})

    async def read(document_id: FileId) -> dict[str, JsonValue]:
        assert document_id == base.output.file_id
        return deepcopy(document)

    async def write(_document_id: FileId, _checkpoint: ReadabilityCheckpoint) -> None:
        pytest.fail("A no-change review must not write document content")

    gateway.readability_provider = ReadabilityProvider(read, write)
    editor = _NoChangeEditor(unavailable=outage)
    executor = _resume_executor(gateway, store, task_id, editor)
    result = await executor._finish_readability(operation_id=task_id, request=request, result=base)

    saved = store.checkpoints[-1].readability
    assert saved is not None
    assert saved.complete
    assert saved.passes == editor.calls == 1
    assert saved.accepted_improvements == 0
    assert saved.stop_reason == ("provider_unavailable" if outage else "converged")
    assert not saved.requests
    assert result.llm_invoked
    assert result.applied_patch_count == base.applied_patch_count
    assert result.output_revision_id == base.output_revision_id
    assert not any(finding.code == "document_readability_improved" for finding in result.findings)
    assert (
        await _resume_executor(gateway, store, task_id, editor).execute(
            operation_id=task_id, request=request
        )
        == result
    )
    assert editor.calls == 1

    document["revisionId"] = "concurrent-edit"
    with pytest.raises(OutputConflictError):
        await _resume_executor(gateway, store, task_id, editor).execute(
            operation_id=task_id, request=request
        )
    assert editor.calls == 1
