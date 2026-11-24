from copy import deepcopy
from dataclasses import replace
from itertools import product
from typing import Literal
import uuid

from pydantic import JsonValue
import pytest

from app.document_transform.errors import InPlaceNeedsReviewError, OutputVerificationError
from app.document_transform.gateway import NativeDocumentSnapshot
from app.document_transform.native_chip_flow import (
    NativeChipCheckpoint,
    NativeChipProvider,
    plan_native_chips,
)
from app.document_transform.readability import plan_readability
from app.document_transform.readability_json import document_digest
from app.document_transform.readability_provider import ReadabilityProvider
from app.document_transform.readability_types import ReadabilityCheckpoint
from app.document_transform.schemas import OutputStrategy, TransformMode
from app.identifiers import FileId, TaskId
from tests.test_document_readability import _fixture as layout_fixture, _LayoutEditor
from tests.test_document_transform import _InPlaceGateway
from tests.test_native_chip_execution import _executor, _setup
from tests.test_native_chip_flow import _fixture as chip_fixture
from tests.test_readability_execution import _resume_executor


@pytest.mark.parametrize(
    ("strategy", "mode", "stage", "pending"),
    list(
        product(
            (OutputStrategy.COPY, OutputStrategy.IN_PLACE),
            (TransformMode.FORMAT, TransformMode.BOTH),
            ("readability", "native_chips"),
            (False, True),
        )
    ),
)
async def test_nonreversible_finishing_keeps_the_last_verified_document(
    strategy: OutputStrategy,
    mode: TransformMode,
    stage: Literal["readability", "native_chips"],
    pending: bool,
) -> None:
    gateway, store, request = _setup(strategy, mode)
    task_id = TaskId(uuid.uuid7())
    base = await _executor(gateway, store, task_id).execute(operation_id=task_id, request=request)
    if strategy is OutputStrategy.IN_PLACE:
        assert isinstance(gateway, _InPlaceGateway)
        assert isinstance(gateway.snapshot, NativeDocumentSnapshot)
        gateway.snapshot = replace(
            gateway.snapshot,
            revision_id=base.output_revision_id,
            google_document=deepcopy(gateway.document),
            web_url=base.output.web_url,
        )
    document = layout_fixture("before") if stage == "readability" else chip_fixture("before")
    document.update({"documentId": base.output.file_id, "revisionId": base.output_revision_id})
    original = deepcopy(document)
    writes: list[FileId] = []
    editor = _LayoutEditor()

    async def read(identifier: FileId) -> dict[str, JsonValue]:
        assert identifier == base.output.file_id
        return deepcopy(document)

    async def write(
        document_id: FileId, _checkpoint: ReadabilityCheckpoint | NativeChipCheckpoint
    ) -> None:
        writes.append(document_id)
        document.update({"title": "Rejected output", "revisionId": "unverified"})

    executor = _resume_executor(gateway, store, task_id, editor)
    if stage == "readability":
        gateway.readability_provider = ReadabilityProvider(read, write)
        if pending:
            layout = await plan_readability(
                document, title="Saved layout", generator=_LayoutEditor()
            )
            assert layout is not None
            assert layout.requests
            await executor._save_readability_checkpoint(
                store.checkpoints[-1].model_copy(
                    update={"readability": layout, "readability_result": base}
                )
            )
        result = await executor._finish_readability(
            operation_id=task_id, request=request, result=base
        )
    else:
        gateway.native_chip_provider = NativeChipProvider(read, write)
        if pending:
            chips = plan_native_chips(document, operation_id=task_id)
            assert chips is not None
            assert chips.requests
            await executor._save_readability_checkpoint(
                store.checkpoints[-1].model_copy(
                    update={"native_chips": chips, "native_chip_result": base}
                )
            )
        result = await executor._finish_native_chips(
            operation_id=task_id, request=request, result=base
        )

    assert not writes
    assert document == original
    assert result.output_revision_id == base.output_revision_id
    assert result.needs_review == base.needs_review
    assert any(finding.code == f"document_{stage}_skipped" for finding in result.findings)
    assert editor.calls == 0
    assert store.checkpoints[-1].readability is None
    assert store.checkpoints[-1].native_chips is None
    assert (
        await _resume_executor(gateway, store, task_id, editor).execute(
            operation_id=task_id, request=request
        )
        == result
    )
    assert not writes
    assert document == original


@pytest.mark.parametrize("stage", ["readability", "native_chips"])
async def test_rejected_native_write_restores_the_verified_preimage(stage: str) -> None:
    document = layout_fixture("before") if stage == "readability" else chip_fixture("before")
    original = deepcopy(document)
    identifier = FileId(str(document["documentId"]))
    rollbacks: list[FileId] = []

    async def read(_identifier: FileId) -> dict[str, JsonValue]:
        return deepcopy(document)

    async def write(
        _identifier: FileId, _checkpoint: ReadabilityCheckpoint | NativeChipCheckpoint
    ) -> None:
        document.update({"title": "Rejected output", "revisionId": "unverified"})

    async def rollback(
        observed_id: FileId,
        before: dict[str, JsonValue],
        observed: dict[str, JsonValue],
    ) -> None:
        assert observed_id == identifier
        assert before == original
        assert observed["revisionId"] == "unverified"
        rollbacks.append(observed_id)
        document.clear()
        document.update(deepcopy(before))
        document["revisionId"] = "restored"

    if stage == "readability":
        layout = await plan_readability(
            document, title="Rollback fixture", generator=_LayoutEditor()
        )
        assert layout is not None
        with pytest.raises(OutputVerificationError):
            await ReadabilityProvider(read, write, rollback=rollback).apply(
                document_id=identifier, checkpoint=layout
            )
    else:
        chips = plan_native_chips(document, operation_id=uuid.UUID(int=2))
        assert chips is not None
        with pytest.raises(InPlaceNeedsReviewError):
            await NativeChipProvider(read, write, rollback=rollback).apply(chips)

    assert rollbacks == [identifier]
    assert document_digest(document) == document_digest(original)
    assert document["revisionId"] == "restored"


@pytest.mark.parametrize("stage", ["readability", "native_chips"])
async def test_rollback_acknowledgment_requires_matching_readback(stage: str) -> None:
    document = layout_fixture("before") if stage == "readability" else chip_fixture("before")
    original = deepcopy(document)
    identifier = FileId(str(document["documentId"]))

    async def read(_identifier: FileId) -> dict[str, JsonValue]:
        return deepcopy(document)

    async def write(
        _identifier: FileId, _checkpoint: ReadabilityCheckpoint | NativeChipCheckpoint
    ) -> None:
        document.update({"title": "Rejected output", "revisionId": "unverified"})

    async def incomplete_rollback(
        _identifier: FileId,
        _before: dict[str, JsonValue],
        _observed: dict[str, JsonValue],
    ) -> None:
        return

    if stage == "readability":
        layout = await plan_readability(
            document, title="Rollback readback", generator=_LayoutEditor()
        )
        assert layout is not None
        with pytest.raises(OutputVerificationError):
            await ReadabilityProvider(read, write, rollback=incomplete_rollback).apply(
                document_id=identifier, checkpoint=layout
            )
    else:
        chips = plan_native_chips(document, operation_id=uuid.UUID(int=2))
        assert chips is not None
        with pytest.raises(OutputVerificationError):
            await NativeChipProvider(read, write, rollback=incomplete_rollback).apply(chips)

    assert document_digest(document) != document_digest(original)
