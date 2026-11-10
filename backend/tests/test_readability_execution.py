from copy import deepcopy
import uuid

from pydantic import JsonValue

from app.document_transform.executor import DocumentTransformExecutor
from app.document_transform.gateway import DocumentGateway
from app.document_transform.readability_provider import ReadabilityProvider
from app.document_transform.readability_types import ReadabilityCheckpoint
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


async def test_copy_readability_finalizes_the_new_receipt_and_resumes_without_new_edits() -> None:
    gateway = _Gateway()
    store = _CopyCheckpointStore()
    task_id = TaskId(uuid.uuid7())
    request = DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown",
            content="An exact source paragraph.",
            name="Readability fixture",
            destination_folder_id=FileId("folder"),
        ),
        mode=TransformMode.FORMAT,
    )
    base = await DocumentTransformExecutor(
        gateway=gateway,
        generator=_Generator(),
        checkpoint_store=store,
        task_id=task_id,
        lease_token=uuid.uuid7(),
    ).execute(operation_id=task_id, request=request)
    document = _fixture("before")
    document.update({"documentId": "output", "revisionId": base.output_revision_id})
    writes: list[FileId] = []

    async def read(document_id: FileId) -> dict[str, JsonValue]:
        assert document_id == "output"
        return deepcopy(document)

    async def write(document_id: FileId, checkpoint: ReadabilityCheckpoint) -> None:
        assert checkpoint.requests
        assert store.checkpoints[-1].readability == checkpoint
        writes.append(document_id)
        document.clear()
        document.update(_fixture("after"))
        document["documentId"] = "output"

    gateway.readability_provider = ReadabilityProvider(read, write)
    editor = _LayoutEditor()
    executor = _resume_executor(gateway, store, task_id, editor)
    result = await executor._finish_readability(operation_id=task_id, request=request, result=base)
    assert result.output_revision_id == "r2"
    assert result.llm_invoked
    assert result.model_patch_count == 3
    assert result.deterministic_patch_count == base.deterministic_patch_count + 1
    assert store.checkpoints[-1].result == result
    assert store.checkpoints[-1].readability_result == result
    assert gateway.completed_result == result.model_dump(mode="json")
    assert await executor.execute(operation_id=task_id, request=request) == result
    assert writes == ["output"]
    assert editor.calls == 2


async def test_in_place_readability_is_durable_and_a_retry_keeps_the_completed_document() -> None:
    gateway = _InPlaceGateway("The team records measurements before selecting storage.")
    store = _CheckpointStore(gateway.events)
    task_id = TaskId(uuid.uuid7())
    request = _in_place_request(TransformMode.FORMAT)
    base = await DocumentTransformExecutor(
        gateway=gateway,
        generator=_Generator(),
        checkpoint_store=store,
        task_id=task_id,
        lease_token=uuid.uuid7(),
    ).execute(operation_id=task_id, request=request)
    document = _fixture("before")
    document.update({"documentId": base.output.file_id, "revisionId": base.output_revision_id})
    writes: list[FileId] = []

    async def read(document_id: FileId) -> dict[str, JsonValue]:
        assert document_id == base.output.file_id
        return deepcopy(document)

    async def write(document_id: FileId, checkpoint: ReadabilityCheckpoint) -> None:
        assert store.checkpoints[-1].readability == checkpoint
        writes.append(document_id)
        document.clear()
        document.update(_fixture("after"))
        document["documentId"] = document_id

    gateway.readability_provider = ReadabilityProvider(read, write)
    editor = _LayoutEditor()
    executor = _resume_executor(gateway, store, task_id, editor)
    result = await executor._finish_readability(operation_id=task_id, request=request, result=base)
    assert result.promoted_heading_count == 1
    assert result.output_revision_id == store.checkpoints[-1].current_revision_id == "r2"
    assert await executor.execute(operation_id=task_id, request=request) == result
    assert writes == [base.output.file_id]
    assert editor.calls == 2


def _resume_executor(
    gateway: DocumentGateway,
    store: _CopyCheckpointStore | _CheckpointStore,
    task_id: TaskId,
    editor: _LayoutEditor,
) -> DocumentTransformExecutor:
    return DocumentTransformExecutor(
        gateway=gateway,
        generator=editor,
        checkpoint_store=store,
        task_id=task_id,
        lease_token=uuid.uuid7(),
        checkpoint=store.checkpoints[-1],
        checkpoint_generation=store.generation,
    )
