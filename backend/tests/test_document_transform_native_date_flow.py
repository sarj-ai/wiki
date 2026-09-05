from copy import deepcopy
from typing import Any
import uuid

import pytest

from app.document_transform.errors import InPlaceNeedsReviewError
from app.document_transform.gateway import InPlaceStageResult
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.native_date_flow import plan_copy_dates, verified_date_projection
from app.identifiers import DocsRevisionId, FileId


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
    if mutation == "identity":
        body[1]["paragraph"]["elements"][0]["dateElement"]["dateElementProperties"]["timestamp"] = (
            "2026-09-05T00:00:00Z"
        )
    elif mutation == "tail":
        body[2]["paragraph"]["elements"][0]["textRun"]["content"] = "Evil\n"
    elif mutation == "newline":
        body[1]["paragraph"]["elements"][1]["textRun"]["textStyle"] = {"bold": True}
    elif mutation == "paragraph":
        body[1]["paragraph"]["paragraphStyle"] = {"alignment": "CENTER"}
    elif mutation == "topology":
        tab["tabProperties"]["title"] = "Changed"
    elif mutation == "footer":
        tab["documentTab"]["footers"] = {"alien": {}}
    else:
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
