from copy import deepcopy
from typing import Any, override
import uuid

from pydantic import ValidationError
import pytest

from app.document_transform.errors import DocumentGatewayError, InPlaceNeedsReviewError
from app.document_transform.gateway import InPlaceStageResult, OutputDraft, SourceSnapshot
from app.document_transform.google_gateway import GoogleDocumentGateway, stage_request_kinds
from app.document_transform.native_date_flow import (
    NativeDateCheckpoint,
    plan_copy_dates,
    verified_date_projection,
)
from app.document_transform.schemas import DocumentTransformRequest, DriveFileSource, TransformMode
from app.drive.retry import ProviderDeadlineError
from app.identifiers import DocsRevisionId, FileId, TaskId
from tests.test_document_transform import _copy_executor, _CopyCheckpointStore, _Gateway


class _DateProvider:
    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document
        self.calls = 0

    async def apply(self, **_kwargs: Any) -> InPlaceStageResult:
        self.calls += 1
        return InPlaceStageResult(DocsRevisionId("r2"), self.document)


def _paragraph(start: int, text: str) -> dict[str, Any]:
    return {
        "startIndex": start,
        "endIndex": start + len(text),
        "paragraph": {
            "elements": [
                {"startIndex": start, "endIndex": start + len(text), "textRun": {"content": text}}
            ]
        },
    }


def _document() -> dict[str, Any]:
    return {
        "revisionId": "r1",
        "tabs": [
            {
                "tabProperties": {"tabId": "t1"},
                "documentTab": {
                    "body": {
                        "content": [
                            _paragraph(1, "Title\n"),
                            _paragraph(7, "2026-09-04\n"),
                            _paragraph(18, "Tail\n"),
                        ]
                    }
                },
            }
        ],
    }


def _converted() -> dict[str, Any]:
    document = _document()
    document["revisionId"] = "r2"
    body = document["tabs"][0]["documentTab"]["body"]["content"]
    body[1] = {
        "startIndex": 7,
        "endIndex": 9,
        "paragraph": {
            "elements": [
                {
                    "startIndex": 7,
                    "endIndex": 8,
                    "dateElement": {
                        "dateId": "d1",
                        "dateElementProperties": {
                            "displayText": "2026-09-04",
                            "timestamp": "2026-09-04T00:00:00Z",
                            "timeZoneId": "Etc/UTC",
                            "locale": "en",
                            "dateFormat": "DATE_FORMAT_ISO8601",
                            "timeFormat": "TIME_FORMAT_DISABLED",
                        },
                    },
                },
                {"startIndex": 8, "endIndex": 9, "textRun": {"content": "\n"}},
            ]
        },
    }
    body[2] = _paragraph(9, "Tail\n")
    return document


def test_date_stage_exact_projection_and_repeat_noop() -> None:
    checkpoint = plan_copy_dates(_document(), operation_id=uuid.uuid7())
    assert checkpoint is not None
    assert len(checkpoint.requests) == 2
    actual = _converted()
    projected = verified_date_projection(actual, checkpoint)
    projected["revisionId"] = "r1"
    assert projected == _document()
    assert plan_copy_dates(actual, operation_id=uuid.uuid7()) is None


@pytest.mark.parametrize(
    "mutation", ["identity", "tail", "newline", "paragraph", "topology", "footer", "unknown"]
)
def test_date_stage_rejects_unplanned_changes(mutation: str) -> None:
    checkpoint = plan_copy_dates(_document(), operation_id=uuid.uuid7())
    assert checkpoint is not None
    actual = _converted()
    tab = actual["tabs"][0]
    body = tab["documentTab"]["body"]["content"]
    match mutation:
        case "identity":
            body[1]["paragraph"]["elements"][0]["dateElement"]["dateElementProperties"][
                "timestamp"
            ] = "2026-09-05T00:00:00Z"
        case "tail":
            body[2]["paragraph"]["elements"][0]["textRun"]["content"] = "Evil\n"
        case "newline":
            body[1]["paragraph"]["elements"][1]["textRun"]["textStyle"] = {"bold": True}
        case "paragraph":
            body[1]["paragraph"]["paragraphStyle"] = {"alignment": "CENTER"}
        case "topology":
            tab["tabProperties"]["title"] = "Changed"
        case "footer":
            tab["documentTab"]["footers"] = {"alien": {}}
        case _:
            body[1]["suggestedFuture"] = True
    with pytest.raises(InPlaceNeedsReviewError):
        verified_date_projection(actual, checkpoint)


def test_date_stage_restores_existing_named_range_positions() -> None:
    original = _document()
    original["tabs"][0]["documentTab"]["namedRanges"] = {
        "custom": {"namedRanges": [{"ranges": [{"startIndex": 18, "endIndex": 22}]}]}
    }
    checkpoint = plan_copy_dates(original, operation_id=uuid.uuid7())
    assert checkpoint is not None
    actual = _converted()
    actual["tabs"][0]["documentTab"]["namedRanges"] = deepcopy(
        original["tabs"][0]["documentTab"]["namedRanges"]
    )
    actual["tabs"][0]["documentTab"]["namedRanges"]["custom"]["namedRanges"][0]["ranges"] = [
        {"startIndex": 9, "endIndex": 13}
    ]
    projected = verified_date_projection(actual, checkpoint)
    projected["revisionId"] = "r1"
    assert projected == original


@pytest.mark.parametrize("segment_id", ["header-1", "footer-1"])
def test_date_stage_preserves_segment_named_range_positions(segment_id: str) -> None:
    original = _document()
    markers = {
        "segment": {
            "namedRanges": [
                {"ranges": [{"segmentId": segment_id, "startIndex": 18, "endIndex": 22}]}
            ]
        }
    }
    original["tabs"][0]["documentTab"]["namedRanges"] = deepcopy(markers)
    checkpoint = plan_copy_dates(original, operation_id=uuid.uuid7())
    assert checkpoint is not None
    actual = _converted()
    actual["tabs"][0]["documentTab"]["namedRanges"] = deepcopy(markers)
    projected = verified_date_projection(actual, checkpoint)
    projected["revisionId"] = "r1"
    assert projected == original
    actual["tabs"][0]["documentTab"]["namedRanges"]["segment"]["namedRanges"][0]["ranges"][0][
        "endIndex"
    ] = 23
    with pytest.raises(InPlaceNeedsReviewError):
        verified_date_projection(actual, checkpoint)


@pytest.mark.asyncio
async def test_gateway_recovers_lost_reply_and_completed_stage_without_second_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _document()
    checkpoint = plan_copy_dates(original, operation_id=uuid.uuid7())
    assert checkpoint is not None
    actual = _converted()
    actual["tabs"][0]["documentTab"]["namedRanges"] = {
        checkpoint.marker_name: {
            "namedRanges": [{"name": checkpoint.marker_name, "ranges": [checkpoint.marker_range]}]
        }
    }
    gateway = object.__new__(GoogleDocumentGateway)
    readback = deepcopy(original)
    monkeypatch.setattr(gateway, "_get_document_sync", lambda _file: deepcopy(readback))
    provider = _DateProvider(actual)
    monkeypatch.setattr(gateway, "apply_in_place_stage", provider.apply)
    completed = await gateway.apply_copy_dates(document_id=FileId("doc"), checkpoint=checkpoint)
    assert completed.complete
    assert provider.calls == 1
    readback = deepcopy(actual)
    assert (
        await gateway.apply_copy_dates(document_id=FileId("doc"), checkpoint=checkpoint)
        == completed
    )
    assert (
        await gateway.apply_copy_dates(document_id=FileId("doc"), checkpoint=completed) == completed
    )
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_gateway_refuses_unmarked_changed_revision_without_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint = plan_copy_dates(_document(), operation_id=uuid.uuid7())
    assert checkpoint is not None
    gateway = object.__new__(GoogleDocumentGateway)
    monkeypatch.setattr(gateway, "_get_document_sync", lambda _file: _converted())
    provider = _DateProvider(_converted())
    monkeypatch.setattr(gateway, "apply_in_place_stage", provider.apply)
    with pytest.raises(InPlaceNeedsReviewError):
        await gateway.apply_copy_dates(document_id=FileId("doc"), checkpoint=checkpoint)
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_rejected_date_is_durable_and_never_replayed(monkeypatch: pytest.MonkeyPatch) -> None:
    checkpoint = plan_copy_dates(_document(), operation_id=uuid.uuid7())
    assert checkpoint is not None
    gateway = object.__new__(GoogleDocumentGateway)
    monkeypatch.setattr(gateway, "_get_document_sync", lambda _file: _document())
    calls = 0

    async def reject(**_kwargs: Any) -> InPlaceStageResult:
        nonlocal calls
        calls += 1
        raise DocumentGatewayError(http_status=400, retryable=False)

    monkeypatch.setattr(gateway, "apply_in_place_stage", reject)
    declined = await gateway.apply_copy_dates(document_id=FileId("doc"), checkpoint=checkpoint)
    assert declined.declined_reason == "provider_rejected"
    assert not declined.complete
    persisted = type(declined).model_validate_json(declined.model_dump_json())
    assert (
        await gateway.apply_copy_dates(document_id=FileId("doc"), checkpoint=persisted) == declined
    )
    assert calls == 1
    assert verified_date_projection(_document(), declined) == _document()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [403, 429, 500, 503], ids=["forbidden", "limited", "error", "unavailable"]
)
async def test_date_other_failures_not_declined(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    checkpoint = plan_copy_dates(_document(), operation_id=uuid.uuid7())
    assert checkpoint is not None
    gateway = object.__new__(GoogleDocumentGateway)
    monkeypatch.setattr(gateway, "_get_document_sync", lambda _file: _document())

    async def reject(**_kwargs: Any) -> InPlaceStageResult:
        raise DocumentGatewayError(http_status=status)

    monkeypatch.setattr(gateway, "apply_in_place_stage", reject)
    with pytest.raises(DocumentGatewayError):
        await gateway.apply_copy_dates(document_id=FileId("doc"), checkpoint=checkpoint)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["revision", "content", "marker"], ids=["revision", "content", "marker"]
)
async def test_date_rejection_requires_exact_unchanged_readback(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    checkpoint = plan_copy_dates(_document(), operation_id=uuid.uuid7())
    assert checkpoint is not None
    gateway = object.__new__(GoogleDocumentGateway)
    document = _document()
    monkeypatch.setattr(gateway, "_get_document_sync", lambda _file: deepcopy(document))

    async def reject(**_kwargs: Any) -> InPlaceStageResult:
        match change:
            case "revision":
                document["revisionId"] = "r2"
            case "content":
                document["tabs"][0]["documentTab"]["body"]["content"][2] = _paragraph(18, "Evil\n")
            case _:
                document["namedRanges"] = {checkpoint.marker_name: {}}
        raise DocumentGatewayError(http_status=400)

    monkeypatch.setattr(gateway, "apply_in_place_stage", reject)
    with pytest.raises(InPlaceNeedsReviewError):
        await gateway.apply_copy_dates(document_id=FileId("doc"), checkpoint=checkpoint)


def test_stage_diagnostics_only_emit_allowlisted_kinds() -> None:
    assert stage_request_kinds([{"insertDate": {"secret": "hidden"}}, {"private-id": {}}]) == [
        "insertDate",
        "other",
    ]


def test_declined_checkpoint_rejects_later_edits_and_conflicting_outcome() -> None:
    checkpoint = plan_copy_dates(_document(), operation_id=uuid.uuid7())
    assert checkpoint is not None
    declined = checkpoint.model_copy(update={"declined_reason": "provider_rejected"})
    changed = _document()
    changed["revisionId"] = "r2"
    with pytest.raises(InPlaceNeedsReviewError):
        verified_date_projection(changed, declined)
    with pytest.raises(ValidationError):
        NativeDateCheckpoint.model_validate({**declined.model_dump(), "complete": True})


@pytest.mark.asyncio
async def test_date_timeout_is_not_declined(monkeypatch: pytest.MonkeyPatch) -> None:
    checkpoint = plan_copy_dates(_document(), operation_id=uuid.uuid7())
    assert checkpoint is not None
    gateway = object.__new__(GoogleDocumentGateway)
    monkeypatch.setattr(gateway, "_get_document_sync", lambda _file: _document())

    async def timeout(**_kwargs: Any) -> InPlaceStageResult:
        raise ProviderDeadlineError

    monkeypatch.setattr(gateway, "apply_in_place_stage", timeout)
    with pytest.raises(ProviderDeadlineError):
        await gateway.apply_copy_dates(document_id=FileId("doc"), checkpoint=checkpoint)


async def test_executor_persists_decline_and_reports_finding_on_recovery() -> None:
    store = _CopyCheckpointStore()

    class Gateway(_Gateway):
        supports_native_dates = True
        date_writes = 0

        @override
        async def plan_copy_dates(
            self, *, draft: OutputDraft, operation_id: uuid.UUID
        ) -> NativeDateCheckpoint:
            return NativeDateCheckpoint(
                revision_id=draft.revision_id,
                marker_name=f"date-{operation_id}",
                marker_range={},
                invariant_sha256="hash",
                paragraphs=[],
                requests=[],
            )

        @override
        async def apply_copy_dates(
            self, *, document_id: FileId, checkpoint: NativeDateCheckpoint
        ) -> NativeDateCheckpoint:
            assert store.checkpoints[-1].native_dates == checkpoint
            if checkpoint.declined_reason is None:
                self.date_writes += 1
            return checkpoint.model_copy(update={"declined_reason": "provider_rejected"})

    gateway = Gateway(
        SourceSnapshot(
            title="Dated notes",
            parent_folder_id=FileId("folder"),
            modified_time="before",
            revision_id=DocsRevisionId("r1"),
            media_type="application/vnd.google-apps.document",
            google_document=_document(),
        )
    )
    task_id = TaskId(uuid.uuid7())
    request = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file", file_id=FileId("source"), expected_modified_time="before"
        ),
        mode=TransformMode.FORMAT,
    )
    result = await _copy_executor(gateway=gateway, store=store, task_id=task_id).execute(
        operation_id=task_id, request=request
    )
    assert not result.needs_review
    assert [finding.code for finding in result.findings].count("native_date_provider_rejected") == 1
    assert all(finding.severity.value == "informational" for finding in result.findings)
    # Resume the durable pre-final-verification checkpoint, not the cached result.
    declined = store.checkpoints[-2]
    assert declined.native_dates is not None
    assert declined.native_dates.declined_reason == "provider_rejected"
    repeated = await _copy_executor(
        gateway=gateway, store=store, task_id=task_id, checkpoint=declined
    ).execute(operation_id=task_id, request=request)
    assert repeated.findings == result.findings
    assert gateway.date_writes == 1
    assert gateway.applies == 1
