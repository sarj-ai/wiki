from collections.abc import Iterator
from copy import deepcopy
from dataclasses import replace
import json
from typing import Any, NamedTuple
import uuid

from pydantic import JsonValue
import pytest

from app.document_transform import native_safety_diagnostics as diagnostics
from app.document_transform.errors import InPlaceNeedsReviewError
from app.document_transform.native_footer_flow import verified_footer_projection
from app.identifiers import DocsRevisionId, DocsTabId, FileId
from tests.test_document_transform_footer_flow import _document, _draft, _plan


@pytest.fixture
def safety_logs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    def warning(_message: str, **values: Any) -> None:
        entries.append(values)

    monkeypatch.setattr(diagnostics.logger, "warning", warning)
    return entries


@pytest.mark.parametrize("populated", [False, True])
@pytest.mark.parametrize("reason", ["segment", "shape", "invariant"])
async def test_footer_projection_reports_exact_existing_failure_without_content(
    monkeypatch: pytest.MonkeyPatch, safety_logs: list[dict[str, Any]], populated: bool, reason: str
) -> None:
    document = _document()
    gateway, checkpoint, provider = await _plan(monkeypatch, document)
    checkpoint = await gateway.apply_copy_footer_stage(
        document_id=FileId("doc"), checkpoint=checkpoint
    )
    if populated:
        checkpoint = await gateway.apply_copy_footer_stage(
            document_id=FileId("doc"), checkpoint=checkpoint
        )
    tab = document["tabs"][0]["documentTab"]
    match reason:
        case "segment":
            tab["documentStyle"]["defaultFooterId"] = "SECRET"
        case "shape":
            tab["footers"]["f1"]["content"] = [{"SECRET": "private document text"}]
        case _:
            # Diagnostics must NOT normalize a transient URI or weaken the invariant.
            tab["inlineObjects"] = {
                "SECRET": {"imageProperties": {"contentUri": "https://private.example/SECRET"}}
            }
    before = deepcopy(document)
    calls = len(provider.calls)
    with pytest.raises(InPlaceNeedsReviewError):
        verified_footer_projection(document, checkpoint, populated=populated)
    assert safety_logs[-1] == {
        "safety_stage": "footer_projection_populated" if populated else "footer_projection_empty",
        "safety_reason": reason,
        **(
            {"footer_shape_check": "paragraph_fields", "footer_shape_reason": "mismatch"}
            if reason == "shape"
            else {}
        ),
    }
    assert "SECRET" not in json.dumps(safety_logs)
    assert document == before
    assert len(provider.calls) == calls


@pytest.mark.parametrize("stage", ["footer_plan", "tab_naming_plan", "date_plan"])
async def test_planning_revision_stop_is_identified_before_any_write(
    monkeypatch: pytest.MonkeyPatch, safety_logs: list[dict[str, Any]], stage: str
) -> None:
    gateway, _, provider = await _plan(monkeypatch, _document())
    draft = replace(_draft(), revision_id=DocsRevisionId("SECRET"))
    match stage:
        case "footer_plan":
            action = gateway.plan_copy_footer(draft=draft, operation_id=uuid.uuid7())
        case "date_plan":
            action = gateway.plan_copy_dates(draft=draft, operation_id=uuid.uuid7())
        case _:
            action = gateway.plan_tab_naming(
                document_id=draft.document_id,
                title="SECRET",
                operation_id=uuid.uuid7(),
                required_revision_id=draft.revision_id,
            )
    with pytest.raises(InPlaceNeedsReviewError):
        await action
    assert safety_logs[-1] == {"safety_stage": stage, "safety_reason": "revision"}
    assert provider.calls == []


@pytest.mark.parametrize("change", ["topology", "revision", "invariant"])
async def test_footer_dispatch_guard_logs_without_dispatch(
    monkeypatch: pytest.MonkeyPatch, safety_logs: list[dict[str, Any]], change: str
) -> None:
    document = _document()
    gateway, checkpoint, provider = await _plan(monkeypatch, document)
    match change:
        case "topology":
            document["tabs"][0]["tabProperties"]["tabId"] = "SECRET"
        case "revision":
            document["revisionId"] = "SECRET"
        case _:
            document["PRIVATE"] = "SECRET"
    with pytest.raises(InPlaceNeedsReviewError):
        await gateway.apply_copy_footer_stage(document_id=FileId("doc"), checkpoint=checkpoint)
    assert safety_logs[-1] == {
        "safety_stage": "footer_coverage" if change == "topology" else "footer_create",
        "safety_reason": change,
    }
    assert "SECRET" not in json.dumps(safety_logs)
    assert provider.calls == []


class _FooterSnapshotLog(NamedTuple):
    phase: str
    fields: dict[str, str | int | bool]


@pytest.fixture
def footer_snapshot_logs() -> Iterator[list[_FooterSnapshotLog]]:
    entries: list[_FooterSnapshotLog] = []
    sink = diagnostics.logger.add(
        lambda message: entries.append(
            _FooterSnapshotLog(
                message.record["extra"]["footer_snapshot_phase"],
                message.record["extra"]["diagnostics"],
            )
        ),
        filter=lambda record: record["message"].startswith("document footer shape snapshot phase="),
        catch=False,
    )
    try:
        yield entries
    finally:
        diagnostics.logger.remove(sink)


def _snapshot_document(footer: JsonValue) -> dict[str, JsonValue]:
    return {
        "documentStyle": {"defaultFooterId": "PRIVATE_ROOT_ID"},
        "footers": {"PRIVATE_ROOT_ID": {"content": []}},
        "tabs": [
            {
                "tabProperties": {"tabId": "PRIVATE_TAB_ID", "title": "SECRET"},
                "documentTab": {
                    "documentStyle": {"defaultFooterId": "PRIVATE_FOOTER_ID"},
                    "footers": {"PRIVATE_FOOTER_ID": footer},
                },
            }
        ],
    }


@pytest.mark.parametrize(
    ("footer", "expected"),
    [
        ({}, "missing"),
        ({"content": None}, "null"),
        ({"content": []}, "empty"),
        ({"content": [{"paragraph": {"elements": []}}]}, "nonempty"),
        ({"content": "SECRET"}, "malformed"),
        (None, "unavailable"),
    ],
)
def test_footer_snapshot_distinguishes_content_states_without_values(
    footer: JsonValue, expected: str
) -> None:
    document = _snapshot_document(footer)
    before = deepcopy(document)
    fields = diagnostics.footer_snapshot_fields(document, DocsTabId("PRIVATE_TAB_ID"))
    assert fields["footer_snapshot_tab_content_state"] == expected
    assert fields["footer_snapshot_tab_ref_in_map"] is True
    assert document == before
    assert "SECRET" not in json.dumps(fields)
    assert "PRIVATE" not in json.dumps(fields)


@pytest.mark.parametrize(
    ("map_value", "expected"),
    [(None, "null"), ({}, "empty"), ({"PRIVATE_FOOTER_ID": {}}, "nonempty"), ([], "malformed")],
)
def test_footer_snapshot_distinguishes_top_level_map_shapes(
    map_value: JsonValue, expected: str
) -> None:
    document = _snapshot_document({})
    document["footers"] = map_value
    fields = diagnostics.footer_snapshot_fields(document, DocsTabId("PRIVATE_TAB_ID"))
    assert fields["footer_snapshot_document_map_state"] == expected
    assert fields["footer_snapshot_tab_map_state"] == "nonempty"
    document.pop("footers")
    assert (
        diagnostics.footer_snapshot_fields(document, DocsTabId("PRIVATE_TAB_ID"))[
            "footer_snapshot_document_map_state"
        ]
        == "missing"
    )


def test_footer_snapshot_caps_content_traversal_without_reading_text() -> None:
    runs: list[JsonValue] = [{"textRun": {"content": "SECRET"}}]
    content: list[JsonValue] = [{"paragraph": {"elements": runs * 65}}]
    document = _snapshot_document({"content": content * 65})
    fields = diagnostics.footer_snapshot_fields(document, DocsTabId("PRIVATE_TAB_ID"))
    assert fields["footer_snapshot_tab_paragraph_count"] == 64
    assert fields["footer_snapshot_tab_element_count"] == 4096
    assert fields["footer_snapshot_tab_complete"] is False
    assert "SECRET" not in json.dumps(fields)


def test_footer_snapshot_bounds_nested_tab_search() -> None:
    document: dict[str, JsonValue] = {"tabs": []}
    tabs: list[JsonValue] = []
    for _ in range(10):
        tabs = [{"tabProperties": {"tabId": "SECRET"}, "childTabs": tabs}]
    document["tabs"] = tabs
    fields = diagnostics.footer_snapshot_fields(document, DocsTabId("PRIVATE_TAB_ID"))
    assert fields["footer_snapshot_tabs_complete"] is False
    assert fields["footer_snapshot_tab_matches"] == 0
    assert "SECRET" not in json.dumps(fields)


def test_returned_footer_id_is_only_reported_as_relationships(
    footer_snapshot_logs: list[_FooterSnapshotLog],
) -> None:
    document = _snapshot_document({"content": [{"paragraph": {"elements": []}}]})
    before = deepcopy(document)
    diagnostics.log_footer_created_reply(
        document,
        [{"createFooter": {"sectionBreakLocation": {"tabId": "PRIVATE_TAB_ID"}}}],
        {"replies": [{}, {"createFooter": {"footerId": "PRIVATE_FOOTER_ID"}}], "token": "SECRET"},
    )
    entry = footer_snapshot_logs[0]
    assert entry.phase == "create_reply"
    assert entry.fields["footer_snapshot_created_id_state"] == "present"
    assert entry.fields["footer_snapshot_created_reply_count"] == 1
    assert entry.fields["footer_snapshot_document_created_in_map"] is False
    assert entry.fields["footer_snapshot_document_created_matches_ref"] is False
    assert entry.fields["footer_snapshot_tab_created_in_map"] is True
    assert entry.fields["footer_snapshot_tab_created_matches_ref"] is True
    assert document == before
    assert "PRIVATE" not in json.dumps(footer_snapshot_logs)
    assert "SECRET" not in json.dumps(footer_snapshot_logs)


@pytest.mark.parametrize(
    ("footer_id", "expected"), [(None, "null"), ("", "empty"), ({"SECRET": 1}, "malformed")]
)
def test_malformed_returned_footer_id_is_never_exposed(
    footer_snapshot_logs: list[_FooterSnapshotLog], footer_id: JsonValue, expected: str
) -> None:
    diagnostics.log_footer_created_reply(
        _snapshot_document({}),
        [{"createFooter": {"sectionBreakLocation": {"tabId": "PRIVATE_TAB_ID"}}}],
        {"replies": [{"createFooter": {"footerId": footer_id}}]},
    )
    assert footer_snapshot_logs[0].fields["footer_snapshot_created_id_state"] == expected
    assert footer_snapshot_logs[0].fields["footer_snapshot_tab_created_in_map"] is False
    assert "SECRET" not in json.dumps(footer_snapshot_logs)


async def test_footer_snapshot_keeps_the_original_invariant_stop_and_write_count(
    monkeypatch: pytest.MonkeyPatch,
    safety_logs: list[dict[str, JsonValue]],
    footer_snapshot_logs: list[_FooterSnapshotLog],
) -> None:
    document = _document()
    gateway, initial, provider = await _plan(monkeypatch, document)
    await gateway.apply_copy_footer_stage(document_id=FileId("doc"), checkpoint=initial)
    document["tabs"][0]["documentTab"]["headers"]["h1"] = {"content": ["SECRET"]}
    before = deepcopy(document)
    footer_snapshot_logs.clear()
    with pytest.raises(InPlaceNeedsReviewError):
        await gateway.apply_copy_footer_stage(document_id=FileId("doc"), checkpoint=initial)
    assert [entry.phase for entry in footer_snapshot_logs] == ["create_input", "projection_empty"]
    assert footer_snapshot_logs[-1].fields["footer_snapshot_tab_content_state"] == "nonempty"
    assert safety_logs[-1] == {
        "safety_stage": "footer_projection_empty",
        "safety_reason": "invariant",
    }
    assert len(provider.calls) == 1
    assert document == before
    assert "SECRET" not in json.dumps(footer_snapshot_logs)
