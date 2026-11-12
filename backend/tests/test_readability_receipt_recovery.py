from copy import deepcopy
from typing import override
import uuid

from pydantic import JsonValue
import pytest

from app.document_transform.errors import OutputConflictError
from app.document_transform.executor import DocumentTransformExecutor
from app.document_transform.ir import DocumentIR
from app.document_transform.models import PatchGenerator
from app.document_transform.readability import plan_readability
from app.document_transform.readability_json import bodies, content, obj
from app.document_transform.readability_provider import ReadabilityProvider
from app.document_transform.readability_types import ReadabilityCheckpoint, ReadabilityProposal
from app.document_transform.schemas import DocumentTransformRequest, MarkdownSource, TransformMode
from app.identifiers import FileId, TaskId
from tests.test_document_readability import _fixture, _LayoutEditor
from tests.test_document_transform import _CopyCheckpointStore, _Gateway, _Generator


class NoChangeLayout(_LayoutEditor):
    @override
    async def plan_readability(
        self,
        document: DocumentIR,
        *,
        previous: list[ReadabilityProposal],
        feedback: str = "",
    ) -> list[ReadabilityProposal]:
        self.calls += 1
        return []


def _paragraph_document() -> dict[str, JsonValue]:
    document = _fixture("before")
    for _, tab in bodies(document):
        obj(tab["body"])["content"] = [item for item in content(tab) if "table" not in obj(item)]
    return document


async def test_layout_failure_is_retained_without_any_native_mutation() -> None:
    editor = _LayoutEditor(unavailable=True)
    plan = await plan_readability(_paragraph_document(), title="Layout outage", generator=editor)
    assert plan is not None
    assert not plan.complete
    assert not plan.requests
    assert plan.model_invoked
    assert plan.planning_failure_code == "model_unavailable"
    assert plan.passes == editor.calls == 1


async def test_table_improvement_does_not_hide_an_incomplete_model_stage() -> None:
    plan = await plan_readability(
        _fixture("before"), title="Layout outage", generator=_LayoutEditor(unavailable=True)
    )
    assert plan is not None
    assert plan.tables
    assert plan.planning_failure_code == "model_unavailable"


async def test_successful_model_noop_records_the_call_without_claiming_changes() -> None:
    editor = NoChangeLayout()
    plan = await plan_readability(_fixture("after"), title="Verified no-op", generator=editor)
    assert plan is not None
    assert not plan.requests
    assert plan.model_invoked
    assert plan.planning_failure_code is None
    assert editor.calls == 1


async def test_zero_request_model_outcome_rejects_a_changed_source_revision() -> None:
    document = _paragraph_document()
    plan = await plan_readability(
        document, title="Revision fence", generator=_LayoutEditor(unavailable=True)
    )
    assert plan is not None
    assert not plan.requests
    document["revisionId"] = "concurrent-revision"

    async def read(_document_id: FileId) -> dict[str, JsonValue]:
        return deepcopy(document)

    async def write(_document_id: FileId, _checkpoint: ReadabilityCheckpoint) -> None:
        pytest.fail("A changed source must not be mutated")

    with pytest.raises(OutputConflictError):
        await ReadabilityProvider(read, write).apply(document_id=FileId("output"), checkpoint=plan)


async def test_failed_layout_receipt_requests_review_and_recovers_without_repeating_calls() -> None:
    gateway = _Gateway()
    store = _CopyCheckpointStore()
    task_id = TaskId(uuid.uuid7())
    request = DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown",
            content="An exact source paragraph.",
            name="Layout recovery",
            destination_folder_id=FileId("folder"),
        ),
        mode=TransformMode.FORMAT,
    )

    def resume(generator: PatchGenerator) -> DocumentTransformExecutor:
        return DocumentTransformExecutor(
            gateway=gateway,
            generator=generator,
            checkpoint_store=store,
            task_id=task_id,
            lease_token=uuid.uuid7(),
            checkpoint=store.checkpoints[-1] if store.checkpoints else None,
            checkpoint_generation=store.generation,
        )

    base = await resume(_Generator()).execute(operation_id=task_id, request=request)
    document = _paragraph_document()
    document.update({"documentId": "output", "revisionId": base.output_revision_id})
    before = deepcopy(document)

    async def read(document_id: FileId) -> dict[str, JsonValue]:
        assert document_id == "output"
        return deepcopy(document)

    async def write(_document_id: FileId, _checkpoint: ReadabilityCheckpoint) -> None:
        pytest.fail("A failed layout plan must never write to Google Docs")

    gateway.readability_provider = ReadabilityProvider(read, write)
    editor = _LayoutEditor(unavailable=True)

    result = await resume(editor).execute(operation_id=task_id, request=request)
    assert result.llm_invoked
    assert result.needs_review
    assert result.model_patch_count == base.model_patch_count
    assert result.output_revision_id == base.output_revision_id
    assert any(
        item.code == "document_readability_incomplete_model_unavailable" for item in result.findings
    )
    assert not any(item.code == "document_readability_improved" for item in result.findings)
    assert store.checkpoints[-1].readability_result == result
    assert await resume(editor).execute(operation_id=task_id, request=request) == result
    assert editor.calls == 1
    assert document == before
