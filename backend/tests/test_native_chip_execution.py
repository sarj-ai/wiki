from copy import deepcopy
import json
import uuid

from pydantic import JsonValue
import pytest

from app.document_transform.executor import DocumentTransformExecutor
from app.document_transform.gateway import DocumentGateway
from app.document_transform.native_chip_flow import (
    NativeChipCheckpoint,
    NativeChipProvider,
    PendingNativeChipCheckpoint,
    plan_native_chips,
)
from app.document_transform.schemas import (
    DocumentTransformRequest,
    MarkdownSource,
    OutputStrategy,
    TransformMode,
)
from app.identifiers import FileId, TaskId
from tests.test_document_transform import (
    _CheckpointStore,
    _CopyCheckpointStore,
    _Gateway,
    _Generator,
    _in_place_request,
    _InPlaceGateway,
)
from tests.test_native_chip_flow import _fixture


def _executor(
    gateway: DocumentGateway,
    store: _CopyCheckpointStore | _CheckpointStore,
    task_id: TaskId,
    *,
    resume: bool = False,
) -> DocumentTransformExecutor:
    return DocumentTransformExecutor(
        gateway=gateway,
        generator=_Generator(),
        checkpoint_store=store,
        task_id=task_id,
        lease_token=uuid.uuid7(),
        checkpoint=store.checkpoints[-1] if resume else None,
        checkpoint_generation=store.generation,
    )


def _setup(strategy: OutputStrategy, mode: TransformMode):
    if strategy is OutputStrategy.COPY:
        gateway: DocumentGateway = _Gateway()
        store: _CopyCheckpointStore | _CheckpointStore = _CopyCheckpointStore()
        request = DocumentTransformRequest(
            source=MarkdownSource(
                kind="markdown",
                content="The gallery records every visit.",
                name="Native chip stage fixture",
                destination_folder_id=FileId("folder"),
            ),
            mode=mode,
        )
    else:
        gateway = _InPlaceGateway("The gallery records every visit.")
        store = _CheckpointStore(gateway.events)
        request = _in_place_request(mode)
    return gateway, store, request


@pytest.mark.parametrize("strategy", [OutputStrategy.COPY, OutputStrategy.IN_PLACE])
@pytest.mark.parametrize("mode", [TransformMode.FORMAT, TransformMode.BOTH])
@pytest.mark.parametrize("interrupt", ["before-write", "after-write"])
async def test_chip_stage_recovers_copy_and_in_place_without_duplicating_native_chips(
    strategy: OutputStrategy, mode: TransformMode, interrupt: str
) -> None:
    gateway, store, request = _setup(strategy, mode)
    task_id = TaskId(uuid.uuid7())
    executor = _executor(gateway, store, task_id)
    base = await executor.execute(operation_id=task_id, request=request)
    document = _fixture("before")
    document.update({"documentId": base.output.file_id, "revisionId": base.output_revision_id})
    writes = 0
    mutations = 0

    async def read(document_id: FileId) -> dict[str, JsonValue]:
        assert document_id == base.output.file_id
        return deepcopy(document)

    async def write(document_id: FileId, checkpoint: NativeChipCheckpoint) -> None:
        nonlocal writes, mutations
        assert store.checkpoints[-1].native_chips == checkpoint
        assert not checkpoint.complete
        assert document_id == base.output.file_id
        writes += 1
        if writes == 1 and interrupt == "before-write":
            raise TimeoutError
        mutations += 1
        document.clear()
        document.update(
            json.loads(
                json.dumps(_fixture("after")).replace(
                    "sarj_native_date_range_canary_20261112",
                    checkpoint.marker_name,
                )
            )
        )
        document["documentId"] = document_id
        if writes == 1 and interrupt == "after-write":
            raise TimeoutError

    gateway.native_chip_provider = NativeChipProvider(read, write)
    with pytest.raises(TimeoutError):
        await executor._finish_native_chips(operation_id=task_id, request=request, result=base)
    assert store.checkpoints[-1].native_chips is not None
    assert not store.checkpoints[-1].native_chips.complete
    result = await _executor(gateway, store, task_id, resume=True).execute(
        operation_id=task_id,
        request=request,
    )
    assert result.output_revision_id == "r2"
    assert result.deterministic_patch_count == base.deterministic_patch_count + 2
    assert result.validation == base.validation
    assert result.needs_review
    assert any(
        finding.code == "native_place_skipped_native_place_insertion_unsupported_by_verified_api"
        for finding in result.findings
    )
    assert mutations == 1
    assert sum(finding.code == "native_dates_converted" for finding in result.findings) == 1
    assert store.checkpoints[-1].native_chip_result == result
    assert (
        await _executor(gateway, store, task_id, resume=True).execute(
            operation_id=task_id,
            request=request,
        )
        == result
    )
    assert mutations == 1


async def test_unslop_does_not_plan_or_write_native_conversions() -> None:
    gateway, store, request = _setup(OutputStrategy.COPY, TransformMode.UNSLOP)
    task_id = TaskId(uuid.uuid7())

    async def forbidden_read(_: FileId) -> dict[str, JsonValue]:
        pytest.fail("Unslop cannot plan new native chips")

    async def forbidden_write(_: FileId, __: NativeChipCheckpoint) -> None:
        pytest.fail("Unslop cannot convert native chips")

    gateway.native_chip_provider = NativeChipProvider(forbidden_read, forbidden_write)
    await _executor(gateway, store, task_id).execute(operation_id=task_id, request=request)
    assert all(checkpoint.native_chips is None for checkpoint in store.checkpoints)


async def test_already_written_legacy_quoted_conversions_recover_with_review_and_exactly_once_counts() -> (
    None
):
    gateway, store, request = _setup(OutputStrategy.COPY, TransformMode.FORMAT)
    task_id = TaskId(uuid.uuid7())
    executor = _executor(gateway, store, task_id)
    base = await executor.execute(operation_id=task_id, request=request)
    before = _fixture("table_style_before")
    before.update({"documentId": base.output.file_id, "revisionId": base.output_revision_id})
    legacy = plan_native_chips(before, operation_id=task_id, planner_version=1)
    assert legacy is not None
    payload = legacy.model_dump()
    payload.pop("planner_version")
    legacy = PendingNativeChipCheckpoint.model_validate(payload)
    assert legacy.planner_version == 1
    after = json.loads(
        json.dumps(_fixture("table_style_after")).replace(
            "sarj_native_chips_00000000000000000000000000000002", legacy.marker_name
        )
    )
    after["documentId"] = base.output.file_id

    async def read(_: FileId) -> dict[str, JsonValue]:
        return deepcopy(after)

    async def forbidden_write(_: FileId, __: NativeChipCheckpoint) -> None:
        pytest.fail("Already-written legacy chip conversion cannot be repeated")

    gateway.native_chip_provider = NativeChipProvider(read, forbidden_write)
    await executor._save_readability_checkpoint(
        store.checkpoints[-1].model_copy(
            update={
                "native_chips": legacy,
                "native_chip_result": base,
            }
        )
    )
    result = await executor._finish_native_chips(operation_id=task_id, request=request, result=base)
    assert result.needs_review
    assert result.applied_patch_count == base.applied_patch_count + 4
    assert (
        sum(
            finding.code == "native_legacy_protected_conversion_needs_review"
            for finding in result.findings
        )
        == 1
    )
    assert (
        await _executor(gateway, store, task_id, resume=True).execute(
            operation_id=task_id,
            request=request,
        )
        == result
    )
