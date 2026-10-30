from copy import deepcopy
from typing import Any, NamedTuple
import uuid

import pytest

from app.document_transform.errors import InPlaceNeedsReviewError, UnsupportedTabMetadataError
from app.document_transform.gateway import InPlaceStageResult
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.schemas import TabNamingCheckpoint
from app.document_transform.tab_metadata import native_tab_properties, tab_naming_invariant_sha256
from app.identifiers import DocsRevisionId, FileId


class _Stage:
    def __init__(self, result: InPlaceStageResult | None = None) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    async def apply(self, **kwargs: object) -> InPlaceStageResult:
        self.calls.append(kwargs)
        assert self.result is not None
        return self.result


def _document() -> dict[str, Any]:
    return {
        "revisionId": "r1",
        "tabs": [
            {
                "tabProperties": {"tabId": "t1", "title": "Tab 1"},
                "documentTab": {
                    "body": {
                        "content": [
                            {
                                "startIndex": 1,
                                "endIndex": 9,
                                "paragraph": {
                                    "paragraphStyle": {"namedStyleType": "HEADING_1"},
                                    "elements": [
                                        {
                                            "startIndex": 1,
                                            "endIndex": 9,
                                            "textRun": {"content": "Roadmap\n"},
                                        }
                                    ],
                                },
                            }
                        ]
                    },
                    "headers": {"h1": {"content": [{"opaque": "preserve"}]}},
                    "footers": {"f1": {"content": [{"opaque": "preserve"}]}},
                },
            }
        ],
    }


class _Planned(NamedTuple):
    gateway: GoogleDocumentGateway
    checkpoint: TabNamingCheckpoint


async def _plan(monkeypatch: pytest.MonkeyPatch, document: dict[str, Any]) -> _Planned:
    gateway = object.__new__(GoogleDocumentGateway)
    monkeypatch.setattr(gateway, "_get_document_sync", lambda _file_id: document)
    checkpoint = await gateway.plan_tab_naming(
        document_id=FileId("doc"),
        title="Plan",
        operation_id=uuid.uuid7(),
        required_revision_id=DocsRevisionId("r1"),
    )
    assert checkpoint is not None
    return _Planned(gateway, TabNamingCheckpoint.model_validate_json(checkpoint.model_dump_json()))


def _apply(document: dict[str, Any], checkpoint: TabNamingCheckpoint) -> None:
    document["tabs"][0]["tabProperties"]["title"] = "Roadmap"
    document["tabs"][0]["tabProperties"]["iconEmoji"] = "📄"
    document["tabs"][0]["documentTab"]["namedRanges"] = {
        checkpoint.marker_name: {
            "namedRanges": [
                {
                    "name": checkpoint.marker_name,
                    "ranges": [checkpoint.marker_range],
                }
            ]
        },
    }
    document["revisionId"] = "r2"


@pytest.mark.asyncio
async def test_native_tab_stage_commits_once_and_recovers_lost_response(
    monkeypatch: pytest.MonkeyPatch,
):
    document = _document()
    gateway, checkpoint = await _plan(monkeypatch, document)
    after = deepcopy(document)
    _apply(after, checkpoint)
    stage = _Stage(InPlaceStageResult(DocsRevisionId("r2"), after))
    monkeypatch.setattr(gateway, "apply_in_place_stage", stage.apply)
    assert await gateway.apply_tab_naming(document_id=FileId("doc"), checkpoint=checkpoint) == "r2"
    assert stage.calls[0]["required_revision_id"] == "r1"
    assert stage.calls[0]["requests"] == [
        {
            "updateDocumentTabProperties": {
                "tabProperties": {"tabId": "t1", "title": "Roadmap", "iconEmoji": "📄"},
                "fields": "title,iconEmoji",
            },
        }
    ]
    _apply(document, checkpoint)
    assert await gateway.apply_tab_naming(document_id=FileId("doc"), checkpoint=checkpoint) == "r2"
    assert len(stage.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", ["revision", "title", "footer", "body", "header"])
async def test_tab_naming_rejects_concurrent_edits(monkeypatch: pytest.MonkeyPatch, conflict: str):
    document = _document()
    gateway, checkpoint = await _plan(monkeypatch, document)
    tab = document["tabs"][0]
    match conflict:
        case "revision":
            document["revisionId"] = "changed"
        case "title":
            tab["tabProperties"]["title"] = "User authored"
        case _:
            tab["documentTab"][
                {"footer": "footers", "header": "headers", "body": "body"}[conflict]
            ] = {"changed": True}
    stage = _Stage()
    monkeypatch.setattr(gateway, "apply_in_place_stage", stage.apply)
    with pytest.raises(InPlaceNeedsReviewError):
        await gateway.apply_tab_naming(document_id=FileId("doc"), checkpoint=checkpoint)
    assert not stage.calls


@pytest.mark.asyncio
async def test_tab_naming_rejects_wrong_post_write_title(monkeypatch: pytest.MonkeyPatch):
    document = _document()
    gateway, checkpoint = await _plan(monkeypatch, document)
    stage = _Stage(InPlaceStageResult(DocsRevisionId("r2"), document))
    monkeypatch.setattr(gateway, "apply_in_place_stage", stage.apply)
    with pytest.raises(InPlaceNeedsReviewError):
        await gateway.apply_tab_naming(document_id=FileId("doc"), checkpoint=checkpoint)


def test_tab_metadata_nested_topology_and_full_content_invariant():
    document = _document()
    original_hash = tab_naming_invariant_sha256(document)
    document["tabs"][0]["tabProperties"]["title"] = "Roadmap"
    assert tab_naming_invariant_sha256(document) == original_hash
    child = deepcopy(document["tabs"][0])
    child["tabProperties"] = {"tabId": "child", "title": "Details"}
    document["tabs"][0]["childTabs"] = [child]
    assert native_tab_properties(document)[1].parent_tab_id == "t1"
    assert tab_naming_invariant_sha256(document) != original_hash


def test_unknown_tab_metadata_is_a_typed_optional_enhancement_decline() -> None:
    document = _document()
    document["tabs"][0]["tabProperties"]["futureProviderField"] = "preserve"

    with pytest.raises(UnsupportedTabMetadataError):
        native_tab_properties(document)
