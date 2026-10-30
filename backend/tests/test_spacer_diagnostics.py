from copy import deepcopy
import json
from typing import Any
import uuid

from loguru import logger
import pytest

from app.document_transform.ir import parse_google_document
from app.document_transform.native_pagination import plan_native_pagination
from app.document_transform.schemas import DocumentTransformRequest, DriveFileSource, TransformMode
from app.identifiers import FileId, TaskId
from tests.test_authored_labels import _snapshot
from tests.test_document_transform import _copy_executor, _CopyCheckpointStore, _Gateway
from tests.test_native_spacer_layout import _spaced_document
from tests.test_transform_diagnostics_workflow import MODULE, TASK, _record


async def test_spacer_initial_checkpoint_logs_but_durable_replay_is_silent() -> None:
    gateway = _Gateway(_snapshot(_spaced_document()))
    store = _CopyCheckpointStore()
    task_id = TaskId(uuid.uuid7())
    request = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file", file_id=FileId("source"), expected_modified_time="initial"
        ),
        mode=TransformMode.FORMAT,
    )
    records: list[dict[str, Any]] = []
    sink = logger.add(
        lambda message: records.append(message.record["extra"]),
        filter=lambda record: record["message"].startswith("document spacer eligibility"),
    )
    try:
        result = await _copy_executor(gateway=gateway, store=store, task_id=task_id).execute(
            operation_id=task_id, request=request
        )
        assert len(records) == 1
        replay = await _copy_executor(
            gateway=gateway, store=store, task_id=task_id, checkpoint=store.checkpoints[-1]
        ).execute(operation_id=task_id, request=request)
        assert replay == result
        assert len(records) == 1
    finally:
        logger.remove(sink)


@pytest.mark.parametrize("change", ["none", "indices", "runs", "style", "text_run", "container"])
def test_spacer_diagnostics_preserve_proof_and_never_emit_private_values(change: str) -> None:
    native = _spaced_document()
    document = parse_google_document(native, title="PRIVATE TITLE")
    spacer = native["tabs"][0]["documentTab"]["body"]["content"][1]
    if change == "indices":
        spacer["startIndex"] = True
    elif change == "runs":
        spacer["paragraph"]["elements"] = []
    elif change == "style":
        spacer["paragraph"]["paragraphStyle"] = {"PRIVATE FIELD": "PRIVATE VALUE"}
    elif change == "text_run":
        spacer["paragraph"]["elements"][0]["textRun"]["textStyle"] = {"PRIVATE": "VALUE"}
    elif change == "container":
        spacer["PRIVATE FIELD"] = "VALUE"
    before = deepcopy(spacer)
    expected = plan_native_pagination(native, document)
    records: list[dict[str, Any]] = []
    sink = logger.add(lambda message: records.append(message.record["extra"]))
    try:
        assert plan_native_pagination(native, document) == expected
        assert records == []
        assert plan_native_pagination(native, document, emit_diagnostics=True) == expected
    finally:
        logger.remove(sink)
    diagnostics = records[-1]["diagnostics"]
    assert len(records) == 1
    assert any(rule.above_spacer_sha256 is not None for rule in expected) == (change == "none")
    assert diagnostics["spacer_planned_above_count"] == int(change == "none")
    assert "PRIVATE" not in json.dumps(diagnostics)
    assert spacer == before


@pytest.mark.parametrize("scope", ["eligible", "named_style", "unavailable"])
def test_spacer_log_distinguishes_checked_scope_without_false_absence(scope: str) -> None:
    native = _spaced_document()
    if scope == "named_style":
        native["namedStyles"] = {"styles": [{"paragraphStyle": {"PRIVATE FIELD": 1}}]}
    document = parse_google_document(native, title="PRIVATE TITLE")
    records: list[dict[str, Any]] = []
    sink = logger.add(
        lambda message: records.append(message.record["extra"]),
        filter=lambda record: record["message"].startswith("document spacer eligibility"),
    )
    try:
        rules = plan_native_pagination(
            None if scope == "unavailable" else native, document, emit_diagnostics=True
        )
    finally:
        logger.remove(sink)
    diagnostics = records[-1]["diagnostics"]
    assert diagnostics.get("spacer_scope_checked") is (None if scope == "unavailable" else True)
    assert (
        diagnostics.get("spacer_named_styles_known")
        == {"eligible": True, "named_style": False, "unavailable": None}[scope]
    )
    assert diagnostics["spacer_planned_above_count"] == sum(
        rule.above_spacer_sha256 is not None for rule in rules
    )
    record = _record()
    record["jsonPayload"]["record"]["message"] = "document spacer eligibility"
    record["jsonPayload"]["record"]["extra"]["diagnostics"] = diagnostics
    rows = MODULE["sanitize"]([record], TASK)
    assert rows[0]["spacer_planned_above_count"] == int(scope == "eligible")
    assert "PRIVATE" not in json.dumps(records)
    assert "SECRET" not in json.dumps(rows)


@pytest.mark.parametrize("invalid", [True, -1, "PRIVATE", 1_000_001])
def test_spacer_reader_rejects_invalid_counts_and_discards_raw_keys(invalid: object) -> None:
    record = _record()
    record["jsonPayload"]["record"]["message"] = "document spacer eligibility"
    record["jsonPayload"]["record"]["extra"]["diagnostics"] = {
        "spacer_planned_above_count": invalid,
        "PRIVATE FIELD": "PRIVATE CONTENT",
        "tab_id": "PRIVATE ID",
        "spacer_sha256": "a" * 64,
    }
    with pytest.raises(ValueError, match=r"^$"):
        MODULE["sanitize"]([record], TASK)
    record["jsonPayload"]["record"]["extra"]["diagnostics"]["spacer_planned_above_count"] = 1
    row = MODULE["sanitize"]([record], TASK)[0]
    assert row["spacer_planned_above_count"] == 1
    assert "PRIVATE" not in json.dumps(row)
    assert "a" * 64 not in json.dumps(row)
