from copy import deepcopy
from dataclasses import replace
from typing import Any
import uuid

import pytest

from app.document_transform.errors import (
    DocumentGatewayError,
    OutputConflictError,
)
from app.document_transform.executor import DocumentTransformExecutor
from app.document_transform.gateway import InPlaceStageResult, SourceSnapshot
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.ir import parse_google_document
from app.document_transform.schemas import (
    DocumentTransformRequest,
    DriveFileSource,
    OutputStrategy,
    TransformMode,
)
from app.identifiers import DocsRevisionId, FileId, TaskId
from tests.test_document_transform import (
    _branded_google_document,
    _CheckpointStore,
    _Gateway,
    _Generator,
)
from tests.test_document_transform_repeat import _inspection_snapshot
from tests.test_native_table_font import _document, _paragraph


@pytest.mark.parametrize("change", ["text", "range", "tab"])
async def test_table_scope_declines_before_write_when_preceding_body_changes(change: str) -> None:
    document = _document()
    snapshot = _inspection_snapshot(document)
    native = object.__new__(GoogleDocumentGateway)
    native._logo_uri = "https://example.test/logo.png"
    original = parse_google_document(document, title=snapshot.title)
    first = original.blocks[0]
    assert first.source_range is not None
    transformed = replace(
        original,
        blocks=(
            replace(
                first,
                text=first.text + " Extra." if change == "text" else first.text,
                source_range=(
                    replace(first.source_range, end_index=first.source_range.end_index + 1)
                    if change == "range"
                    else first.source_range
                ),
            ),
            *original.blocks[1:],
        ),
        tab_ids=() if change == "tab" else original.tab_ids,
    )
    scope = await DocumentTransformExecutor(
        gateway=_Gateway(snapshot),
        generator=_Generator(),
        allow_format_text_edits=True,
        checkpoint_store=_CheckpointStore([]),
        task_id=TaskId(uuid.uuid7()),
        lease_token=uuid.uuid7(),
    )._table_font_scope(
        request=DocumentTransformRequest(
            source=DriveFileSource(
                kind="drive_file", file_id=FileId("source"), expected_modified_time="now"
            ),
            mode=TransformMode.FORMAT,
            output_strategy=OutputStrategy.IN_PLACE,
        ),
        snapshot=snapshot,
        original=original,
        transformed=transformed,
        inspection=await native.inspect_in_place(snapshot=snapshot, title=snapshot.title),
    )
    assert scope.plan is None
    assert scope.requests == []
    assert [finding.code for finding in scope.findings] == ["table_font_preserved"]
    assert "preserved" in scope.findings[0].detail
    assert document == _document()


@pytest.mark.parametrize("preserve_alignment", [False, True])
@pytest.mark.parametrize("tamper", ["bold", "family", "terminator"])
async def test_table_format_preserves_exact_styles_and_repeat_noop(
    monkeypatch: pytest.MonkeyPatch, *, preserve_alignment: bool, tamper: str
) -> None:
    document = _document()
    if preserve_alignment:
        document["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["paragraphStyle"][
            "alignment"
        ] = "CENTER"
    native = object.__new__(GoogleDocumentGateway)
    native._logo_uri = "https://example.test/logo.png"
    gateway = _Gateway(_inspection_snapshot(document))
    store = _CheckpointStore([])
    events: list[str] = []
    operation = uuid.uuid7()
    task_id = TaskId(uuid.uuid7())

    async def load(_source: DriveFileSource) -> SourceSnapshot:
        return replace(
            _inspection_snapshot(document), revision_id=DocsRevisionId(document["revisionId"])
        )

    async def apply(**_kwargs: Any) -> InPlaceStageResult:
        events.append("unexpected_write")
        pytest.fail("Preserved table-only formatting must not dispatch provider requests")

    monkeypatch.setattr(gateway, "load_drive_source", load)
    monkeypatch.setattr(gateway, "inspect_in_place", native.inspect_in_place)
    monkeypatch.setattr(gateway, "verify_in_place", native.verify_in_place)
    monkeypatch.setattr(gateway, "apply_in_place_stage", apply)
    request = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file", file_id=FileId("source"), expected_modified_time="now"
        ),
        mode=TransformMode.FORMAT,
        output_strategy=OutputStrategy.IN_PLACE,
    )

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

    before = deepcopy(document)
    result = await executor().execute(operation_id=operation, request=request)
    assert not result.changed
    assert not result.needs_review
    assert [finding.code for finding in result.findings] == (
        ["native_paragraph_layout_preserved", "table_font_preserved"]
        if preserve_alignment
        else ["table_font_preserved"]
    )
    assert all(finding.severity.value == "informational" for finding in result.findings)
    assert events == []
    assert document == before
    assert all(checkpoint.table_font_plan is None for checkpoint in store.checkpoints)
    assert (
        await executor(store.checkpoints[-1]).execute(operation_id=operation, request=request)
        == result
    )
    again = await executor().execute(operation_id=uuid.uuid7(), request=request)
    assert not again.changed
    assert not again.needs_review
    assert events == []
    previous = store.checkpoints[-1].model_copy(
        update={"request_builder_version": "google-docs-requests-v24"}
    )
    with pytest.raises(OutputConflictError):
        await executor(previous).execute(operation_id=operation, request=request)
    assert events == []
    parts = _paragraph(document)["paragraph"]["elements"]
    if tamper == "bold":
        parts[0]["textRun"]["textStyle"]["bold"] = False
    else:
        part = parts[-1] if tamper == "terminator" else parts[0]
        part["textRun"]["textStyle"]["weightedFontFamily"]["fontFamily"] = "Changed Family"
    verification = await native.verify_in_place(
        document=document,
        title=_inspection_snapshot(before).title,
        expected=parse_google_document(before, title=_inspection_snapshot(before).title),
        structural_sha256=store.checkpoints[-1].structural_sha256,
        protected_structure_sha256=store.checkpoints[-1].protected_structure_sha256,
        verify_brand=True,
    )
    assert not verification.structure_preserved


async def test_table_font_budget_preserves_table_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _document()
    native = object.__new__(GoogleDocumentGateway)
    native._logo_uri = "https://example.test/logo.png"
    gateway = _Gateway(_inspection_snapshot(document))
    monkeypatch.setattr(gateway, "inspect_in_place", native.inspect_in_place)
    monkeypatch.setattr(
        "app.document_transform.executor.table_font_requests",
        lambda _plan: [{"updateTextStyle": {}}] * 2501,
    )
    result = await DocumentTransformExecutor(
        gateway=gateway,
        generator=_Generator(),
        checkpoint_store=_CheckpointStore([]),
        task_id=TaskId(uuid.uuid7()),
        lease_token=uuid.uuid7(),
    ).execute(
        operation_id=uuid.uuid7(),
        request=DocumentTransformRequest(
            source=DriveFileSource(
                kind="drive_file", file_id=FileId("source"), expected_modified_time="now"
            ),
            mode=TransformMode.FORMAT,
            output_strategy=OutputStrategy.IN_PLACE,
        ),
    )
    assert not result.changed
    assert [finding.code for finding in result.findings] == ["table_font_preserved"]
    assert "preserved" in result.findings[0].detail
    assert document == _document()


@pytest.mark.parametrize("outage", [False, True])
async def test_table_plan_survives_header_stage_without_replanning(
    monkeypatch: pytest.MonkeyPatch, *, outage: bool
) -> None:
    document = _document()
    tab = document["tabs"][0]["documentTab"]
    tab["headers"] = {}
    tab["documentStyle"].pop("defaultHeaderId")
    native = object.__new__(GoogleDocumentGateway)
    native._logo_uri = "https://example.test/logo.png"
    gateway = _Gateway(_inspection_snapshot(document))
    store = _CheckpointStore([])
    events: list[str] = []
    operation = uuid.uuid7()
    task_id = TaskId(uuid.uuid7())

    async def load(_source: DriveFileSource) -> SourceSnapshot:
        return replace(
            _inspection_snapshot(document), revision_id=DocsRevisionId(document["revisionId"])
        )

    async def apply(**kwargs: Any) -> InPlaceStageResult:
        assert store.checkpoints[-1].table_font_plan is None
        if "createHeader" in kwargs["requests"][0]:
            if not events:
                tab["headers"] = {"header-a": {"content": []}}
                tab["documentStyle"]["defaultHeaderId"] = "header-a"
                document["revisionId"] = "headers"
                events.append("headers")
                if outage:
                    raise DocumentGatewayError
        else:
            assert all(
                request.get("updateTextStyle", {}).get("range", {}).get("startIndex", 0) < 103
                for request in kwargs["requests"]
            )
            formatted = deepcopy(document)
            formatted["tabs"][0]["documentTab"]["headers"] = deepcopy(
                _branded_google_document()["tabs"][0]["documentTab"]["headers"]
            )
            document.clear()
            document.update(formatted)
            document["revisionId"] = "formatted"
            events.append("content")
        return InPlaceStageResult(DocsRevisionId(document["revisionId"]), deepcopy(document))

    monkeypatch.setattr(gateway, "load_drive_source", load)
    monkeypatch.setattr(gateway, "inspect_in_place", native.inspect_in_place)
    monkeypatch.setattr(gateway, "verify_in_place", native.verify_in_place)
    monkeypatch.setattr(gateway, "apply_in_place_stage", apply)
    request = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file", file_id=FileId("source"), expected_modified_time="now"
        ),
        mode=TransformMode.FORMAT,
        output_strategy=OutputStrategy.IN_PLACE,
    )

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

    if outage:
        with pytest.raises(DocumentGatewayError):
            await executor().execute(operation_id=operation, request=request)
    result = await executor(store.checkpoints[-1] if outage else None).execute(
        operation_id=operation, request=request
    )
    assert result.changed
    assert result.validation.structure_preserved
    assert events == ["headers", "content"]
    assert all(
        checkpoint.table_font_plan == store.checkpoints[0].table_font_plan
        for checkpoint in store.checkpoints
    )


async def test_table_over_budget_scope_is_declined_before_header_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _document()
    tab = document["tabs"][0]["documentTab"]
    tab["headers"] = {}
    tab["documentStyle"].pop("defaultHeaderId")
    native = object.__new__(GoogleDocumentGateway)
    native._logo_uri = "https://example.test/logo.png"
    gateway = _Gateway(_inspection_snapshot(document))
    store = _CheckpointStore([])
    monkeypatch.setattr(gateway, "inspect_in_place", native.inspect_in_place)
    monkeypatch.setattr(
        "app.document_transform.executor.table_font_requests",
        lambda _plan: [{"updateTextStyle": {}}] * 2501,
    )

    async def before_write(**kwargs: Any) -> InPlaceStageResult:
        assert store.checkpoints[-1].table_font_plan is None
        assert [finding.code for finding in store.checkpoints[-1].findings] == [
            "table_font_preserved"
        ]
        assert all("createHeader" in request for request in kwargs["requests"])
        raise DocumentGatewayError

    monkeypatch.setattr(gateway, "apply_in_place_stage", before_write)
    with pytest.raises(DocumentGatewayError):
        await DocumentTransformExecutor(
            gateway=gateway,
            generator=_Generator(),
            checkpoint_store=store,
            task_id=TaskId(uuid.uuid7()),
            lease_token=uuid.uuid7(),
        ).execute(
            operation_id=uuid.uuid7(),
            request=DocumentTransformRequest(
                source=DriveFileSource(
                    kind="drive_file", file_id=FileId("source"), expected_modified_time="now"
                ),
                mode=TransformMode.FORMAT,
                output_strategy=OutputStrategy.IN_PLACE,
            ),
        )
    assert not tab["headers"]
