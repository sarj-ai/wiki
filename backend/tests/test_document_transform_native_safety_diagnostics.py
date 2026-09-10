from copy import deepcopy
from dataclasses import replace
import json
from typing import Any
import uuid

import pytest

from app.document_transform import native_safety_diagnostics as diagnostics
from app.document_transform.errors import InPlaceNeedsReviewError
from app.document_transform.native_footer_flow import verified_footer_projection
from app.identifiers import DocsRevisionId, FileId
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
