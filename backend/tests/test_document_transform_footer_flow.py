from __future__ import annotations

from copy import deepcopy
from typing import Any, NamedTuple
import uuid

from pydantic import ValidationError
import pytest

from app.document_transform.errors import InPlaceNeedsReviewError
from app.document_transform.gateway import InPlaceStageResult, OutputDraft
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.native_footer_flow import (
    NativeFooterCheckpoint,
    NativeFooterStage,
    footer_coverage_complete,
    verified_footer_coverage_projection,
    verified_footer_projection,
)
from app.document_transform.schemas import DocumentOutput
from app.identifiers import DocsRevisionId, DocsTabId, FileId


def _document() -> dict[str, Any]:
    return {
        "revisionId": "r1",
        "tabs": [
            {
                "tabProperties": {"tabId": "t1", "title": "Roadmap"},
                "documentTab": {
                    "documentStyle": {"documentFormat": {"documentMode": "PAGES"}},
                    "body": {
                        "content": [
                            {
                                "startIndex": 1,
                                "endIndex": 9,
                                "paragraph": {"elements": [{"textRun": {"content": "Roadmap\n"}}]},
                            }
                        ]
                    },
                    "headers": {"h1": {"content": [{"opaque": "preserve"}]}},
                },
            }
        ],
    }


def _draft() -> OutputDraft:
    return OutputDraft(
        output=DocumentOutput(
            file_id=FileId("doc"),
            name="Roadmap",
            web_url="https://docs.google.com/document/d/doc/edit",
            parent_folder_id=FileId("folder"),
        ),
        document_id=FileId("doc"),
        tab_id=DocsTabId("t1"),
        revision_id=DocsRevisionId("r1"),
        source_sha256="source",
    )


class _Provider:
    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document
        self.calls: list[dict[str, Any]] = []

    async def apply(self, **kwargs: Any) -> InPlaceStageResult:
        self.calls.append(kwargs)
        assert kwargs["required_revision_id"] == self.document["revisionId"]
        request = kwargs["requests"][0]
        target_id = (
            request["createFooter"]["sectionBreakLocation"]["tabId"]
            if "createFooter" in request
            else request["insertText"]["location"]["tabId"]
        )
        target = next(
            tab for tab in self.document["tabs"] if tab["tabProperties"]["tabId"] == target_id
        )
        tab = target["documentTab"]
        footer_id = f"f{self.document['tabs'].index(target) + 1}"
        marker_tab = next(
            tab["documentTab"]
            for tab in self.document["tabs"]
            if tab["tabProperties"]["tabId"] == kwargs["marker_range"]["tabId"]
        )
        marker = kwargs["marker_name"]
        marker_tab.setdefault("namedRanges", {})[marker] = {
            "namedRanges": [
                {
                    "name": marker,
                    "ranges": [kwargs["marker_range"]],
                }
            ]
        }
        for request in kwargs["requests"]:
            if "createFooter" in request:
                tab["documentStyle"]["defaultFooterId"] = footer_id
                tab["footers"] = {
                    footer_id: {
                        "footerId": footer_id,
                        "content": [
                            {
                                "paragraph": {
                                    "elements": [{"textRun": {"content": "\n"}}],
                                }
                            }
                        ],
                    }
                }
            elif "insertText" in request:
                paragraph = tab["footers"][footer_id]["content"][0]["paragraph"]
                paragraph["elements"][0]["textRun"]["content"] = (
                    request["insertText"]["text"] + "\n"
                )
            elif "updateParagraphStyle" in request:
                paragraph = tab["footers"][footer_id]["content"][0]["paragraph"]
                paragraph["paragraphStyle"] = request["updateParagraphStyle"]["paragraphStyle"]
            elif "updateTextStyle" in request:
                paragraph = tab["footers"][footer_id]["content"][0]["paragraph"]
                paragraph["elements"][0]["textRun"]["textStyle"] = request["updateTextStyle"][
                    "textStyle"
                ]
        self.document["revisionId"] = f"r{len(self.calls) + 1}"
        return InPlaceStageResult(
            DocsRevisionId(self.document["revisionId"]), deepcopy(self.document)
        )


class _Planned(NamedTuple):
    gateway: GoogleDocumentGateway
    checkpoint: NativeFooterCheckpoint
    provider: _Provider


async def _plan(monkeypatch: pytest.MonkeyPatch, document: dict[str, Any]) -> _Planned:
    gateway = object.__new__(GoogleDocumentGateway)
    provider = _Provider(document)
    monkeypatch.setattr(gateway, "_get_document_sync", lambda _file_id: deepcopy(document))
    monkeypatch.setattr(gateway, "apply_in_place_stage", provider.apply)
    checkpoint = await gateway.plan_copy_footer(draft=_draft(), operation_id=uuid.uuid7())
    assert checkpoint is not None
    return _Planned(
        gateway,
        NativeFooterCheckpoint.model_validate_json(checkpoint.model_dump_json()),
        provider,
    )


@pytest.mark.asyncio
async def test_create_and_populate_recover_without_duplicate_writes(
    monkeypatch: pytest.MonkeyPatch,
):
    document = _document()
    before = deepcopy(document)
    gateway, initial, provider = await _plan(monkeypatch, document)
    created = await gateway.apply_copy_footer_stage(document_id=FileId("doc"), checkpoint=initial)
    assert created.stage is NativeFooterStage.POPULATE
    assert created.footer_id == "f1"
    assert created.revision_id == "r2"
    assert (
        await gateway.apply_copy_footer_stage(document_id=FileId("doc"), checkpoint=initial)
        == created
    )
    complete = await gateway.apply_copy_footer_stage(document_id=FileId("doc"), checkpoint=created)
    assert complete.stage is NativeFooterStage.COMPLETE
    assert (
        await gateway.apply_copy_footer_stage(document_id=FileId("doc"), checkpoint=created)
        == complete
    )
    assert len(provider.calls) == 2
    projected, footer_id = verified_footer_projection(document, complete, populated=True)
    before["revisionId"] = "r3"
    assert projected == before
    assert footer_id == "f1"
    assert complete.url == "https://wiki.sarj.ai/d/doc"
    assert complete.display_text == "Open in Wiki"
    run = document["tabs"][0]["documentTab"]["footers"]["f1"]["content"][0]["paragraph"][
        "elements"
    ][0]["textRun"]
    assert run["content"] == "Open in Wiki\n"
    assert run["textStyle"]["link"] == {"url": complete.url}


@pytest.mark.asyncio
async def test_legacy_footer_checkpoint_keeps_raw_url_and_replay_payload(
    monkeypatch: pytest.MonkeyPatch,
):
    document = _document()
    extra = deepcopy(document["tabs"][0])
    extra["tabProperties"]["tabId"] = "t2"
    document["tabs"].append(extra)
    gateway, initial, provider = await _plan(monkeypatch, document)
    legacy_payload = initial.model_dump(mode="json")
    legacy_payload.pop("display_text")
    legacy_payload.pop("coverage")
    legacy = NativeFooterCheckpoint.model_validate(legacy_payload)
    assert legacy.display_text is None
    created = await gateway.apply_copy_footer_stage(document_id=FileId("doc"), checkpoint=legacy)
    complete = await gateway.apply_copy_footer_stage(document_id=FileId("doc"), checkpoint=created)
    assert (
        await gateway.apply_copy_footer_stage(document_id=FileId("doc"), checkpoint=created)
        == complete
    )
    run = document["tabs"][0]["documentTab"]["footers"]["f1"]["content"][0]["paragraph"][
        "elements"
    ][0]["textRun"]
    assert run["content"] == legacy.url + "\n"
    assert run["textStyle"]["link"] == {"url": legacy.url}
    assert len(provider.calls) == 2
    assert complete.coverage is None
    assert "footers" not in document["tabs"][1]["documentTab"]


@pytest.mark.asyncio
async def test_caption_cannot_be_replaced_even_when_destination_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
):
    document = _document()
    gateway, initial, provider = await _plan(monkeypatch, document)
    created = await gateway.apply_copy_footer_stage(document_id=FileId("doc"), checkpoint=initial)
    complete = await gateway.apply_copy_footer_stage(document_id=FileId("doc"), checkpoint=created)
    run = document["tabs"][0]["documentTab"]["footers"]["f1"]["content"][0]["paragraph"][
        "elements"
    ][0]["textRun"]
    run["content"] = complete.url + "\n"
    with pytest.raises(InPlaceNeedsReviewError):
        verified_footer_projection(document, complete, populated=True)
    assert len(provider.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["body", "header", "custom_footer", "link", "font", "marker"])
async def test_recovery_rejects_unplanned_or_unproven_changes(
    monkeypatch: pytest.MonkeyPatch, change: str
):
    document = _document()
    gateway, initial, provider = await _plan(monkeypatch, document)
    created = await gateway.apply_copy_footer_stage(document_id=FileId("doc"), checkpoint=initial)
    await gateway.apply_copy_footer_stage(document_id=FileId("doc"), checkpoint=created)
    tab = document["tabs"][0]["documentTab"]
    match change:
        case "body":
            tab["body"] = {"content": []}
        case "header":
            tab["headers"] = {}
        case "custom_footer":
            tab["footers"]["other"] = {"content": []}
        case "marker":
            tab["namedRanges"].pop(created.populate_marker)
        case _:
            style = tab["footers"]["f1"]["content"][0]["paragraph"]["elements"][0]["textRun"][
                "textStyle"
            ]
            style["link" if change == "link" else "weightedFontFamily"] = {"tampered": True}
    with pytest.raises(InPlaceNeedsReviewError):
        await gateway.apply_copy_footer_stage(document_id=FileId("doc"), checkpoint=created)
    assert len(provider.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", ["custom", "empty", "pageless"])
async def test_native_footer_planning_preserves_existing_segments(
    monkeypatch: pytest.MonkeyPatch, existing: str
):
    document = _document()
    tab = document["tabs"][0]["documentTab"]
    if existing == "pageless":
        tab["documentStyle"]["documentFormat"]["documentMode"] = "PAGELESS"
    else:
        tab["documentStyle"]["defaultFooterId"] = "existing"
        tab["footers"] = {"existing": {"content": [] if existing == "empty" else ["custom"]}}
    gateway = object.__new__(GoogleDocumentGateway)
    monkeypatch.setattr(gateway, "_get_document_sync", lambda _file_id: deepcopy(document))
    result = await gateway.plan_copy_footer(draft=_draft(), operation_id=uuid.uuid7())
    assert result is not None
    assert result.coverage is not None
    assert result.coverage.active is False
    assert result.coverage.skipped[0].reason == (
        "pageless" if existing == "pageless" else "custom_footer"
    )
    assert verified_footer_coverage_projection(document, result) == document


@pytest.mark.asyncio
@pytest.mark.parametrize("custom_middle", [False, True])
async def test_all_tabs_footer_stages_recover_and_reverse_project(
    monkeypatch: pytest.MonkeyPatch, custom_middle: bool
) -> None:
    document = _document()
    for number in (2, 3):
        tab = deepcopy(document["tabs"][0])
        tab["tabProperties"]["tabId"] = f"t{number}"
        document["tabs"].append(tab)
    if custom_middle:
        tab = document["tabs"][1]["documentTab"]
        tab["documentStyle"]["defaultFooterId"] = "custom"
        tab["footers"] = {"custom": {"content": ["Do not change"]}}
    before = deepcopy(document)
    gateway, checkpoint, provider = await _plan(monkeypatch, document)
    for _ in range(9):
        if footer_coverage_complete(checkpoint):
            break
        advanced = await gateway.apply_copy_footer_stage(
            document_id=FileId("doc"), checkpoint=checkpoint
        )
        assert (
            await gateway.apply_copy_footer_stage(document_id=FileId("doc"), checkpoint=checkpoint)
            == advanced
        )
        checkpoint = NativeFooterCheckpoint.model_validate_json(advanced.model_dump_json())
    assert footer_coverage_complete(checkpoint)
    assert len(provider.calls) == (4 if custom_middle else 6)
    before["revisionId"] = document["revisionId"]
    assert verified_footer_coverage_projection(document, checkpoint) == before
    assert len({call["marker_name"] for call in provider.calls}) == len(provider.calls)
    document["tabs"][0]["documentTab"]["footers"]["f1"]["content"][0]["paragraph"]["elements"][0][
        "textRun"
    ]["content"] = "Tampered\n"
    with pytest.raises(InPlaceNeedsReviewError):
        verified_footer_coverage_projection(document, checkpoint)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["duplicate", "overlap", "wrong_tab", "nested", "oversize"])
async def test_footer_coverage_rejects_invalid_durable_topology(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    _, checkpoint, _ = await _plan(monkeypatch, _document())
    payload = checkpoint.model_dump(mode="json")
    coverage = payload["coverage"]
    match change:
        case "duplicate":
            coverage["topology"] = ["t1", "t1"]
        case "overlap":
            coverage["pending"] = ["t1"]
        case "wrong_tab":
            payload["tab_id"] = "unplanned"
        case "nested":
            coverage["completed"] = [checkpoint.model_dump(mode="json")]
        case _:
            coverage["topology"] = [f"t{index}" for index in range(51)]
    with pytest.raises(ValidationError):
        NativeFooterCheckpoint.model_validate(payload)


@pytest.mark.asyncio
async def test_footer_coverage_rejects_changed_provider_topology_before_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _document()
    gateway, checkpoint, provider = await _plan(monkeypatch, document)
    document["tabs"][0]["tabProperties"]["tabId"] = "wrong"
    with pytest.raises(InPlaceNeedsReviewError):
        await gateway.apply_copy_footer_stage(document_id=FileId("doc"), checkpoint=checkpoint)
    assert provider.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "layout",
    [None, "malformed", {"documentMode": "UNKNOWN"}],
    ids=["null", "string", "unknown-mode"],
)
async def test_unknown_footer_layout_is_durably_declined(
    monkeypatch: pytest.MonkeyPatch, layout: object
) -> None:
    document = _document()
    document["tabs"][0]["documentTab"]["documentStyle"]["documentFormat"] = layout
    _, checkpoint, provider = await _plan(monkeypatch, document)
    assert checkpoint.coverage is not None
    assert checkpoint.coverage.skipped[0].reason == "unsupported_layout"
    assert footer_coverage_complete(checkpoint)
    assert provider.calls == []
    assert verified_footer_coverage_projection(document, checkpoint) == document


@pytest.mark.asyncio
async def test_unmarked_ambiguous_creation_fails_closed(monkeypatch: pytest.MonkeyPatch):
    document = _document()
    gateway, initial, provider = await _plan(monkeypatch, document)
    await gateway.apply_copy_footer_stage(document_id=FileId("doc"), checkpoint=initial)
    document["tabs"][0]["documentTab"]["namedRanges"].clear()
    with pytest.raises(InPlaceNeedsReviewError):
        await gateway.apply_copy_footer_stage(document_id=FileId("doc"), checkpoint=initial)
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_provider_empty_map_omission_does_not_break_ownership_recovery(
    monkeypatch: pytest.MonkeyPatch,
):
    document = _document()
    tab = document["tabs"][0]["documentTab"]
    tab["footers"] = {}
    tab["namedRanges"] = {}
    gateway, initial, provider = await _plan(monkeypatch, document)
    created = await gateway.apply_copy_footer_stage(document_id=FileId("doc"), checkpoint=initial)
    complete = await gateway.apply_copy_footer_stage(document_id=FileId("doc"), checkpoint=created)
    assert complete.stage is NativeFooterStage.COMPLETE
    assert len(provider.calls) == 2
