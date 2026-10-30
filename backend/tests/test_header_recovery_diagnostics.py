from dataclasses import replace
from typing import Any

from loguru import logger
import pytest

from app.document_transform.errors import DocumentGatewayError
from app.document_transform.header_recovery import recover_empty_header_postcondition
from app.document_transform.schemas import DriveFileSource
from tests.test_header_postcondition_recovery import _Session


@pytest.mark.parametrize("change", ["none", "body", "marker", "style", "ownership", "indices"])
async def test_recovery_diagnostics_preserve_decision_and_exclude_payload(change: str) -> None:
    session = _Session(newline=True)
    with pytest.raises(DocumentGatewayError):
        await session.run()
    checkpoint = session.store.checkpoints[-1]
    assert isinstance(session.request.source, DriveFileSource)
    snapshot = await session.gateway.load_drive_source(session.request.source)
    inspection = await session.gateway.inspect_in_place(snapshot=snapshot, title=snapshot.title)
    expected = "recovered"
    if change == "body":
        inspection = replace(inspection, body_sha256="SECRET")
        expected = "invariants"
    elif change == "marker":
        checkpoint = checkpoint.model_copy(update={"marker_range": {"SECRET": "SECRET"}})
        expected = "invariants"
    elif change == "ownership":
        checkpoint = checkpoint.model_copy(update={"headers": {"SECRET": "SECRET"}})
        expected = "ownership"
    elif change in {"style", "indices"}:
        assert snapshot.google_document is not None
        header = snapshot.google_document["tabs"][0]["documentTab"]["headers"]["created-0"]
        if change == "style":
            header["content"][0]["paragraph"]["paragraphStyle"]["alignment"] = "SECRET"
        else:
            header["content"][0]["endIndex"] = 2
        expected = "inventory"
    records: list[dict[str, Any]] = []
    sink = logger.add(lambda message: records.append(message.record["extra"]["checks"]))
    try:
        result = recover_empty_header_postcondition(
            snapshot, inspection, checkpoint, session.operation
        )
    finally:
        logger.remove(sink)
    assert (result is not None) is (change == "none")
    assert len(records) == 1
    checks = records[0]
    assert checks["header_recovery_reason"] == expected
    assert "SECRET" not in str(checks)
    assert str(session.operation) not in str(checks)
    assert checkpoint.header_safe_sha256 not in str(checks)
    assert all(type(value) is bool for key, value in checks.items() if key.endswith("_equal"))
    if change == "style":
        assert checks["header_recovery_empty_reason"] == "paragraph_style"
    if change == "indices":
        assert checks["header_recovery_empty_reason"] == "paragraph_wrapper"
    if change == "body":
        assert checks["header_recovery_body_equal"] is False
    if change == "marker":
        assert checks["header_recovery_marker_range_equal"] is False
    if change == "ownership":
        assert set(checks) == {"header_recovery_reason"}


@pytest.mark.parametrize(
    ("dimension", "unit_only", "explicit_zero"),
    [
        ({"unit": "PT"}, True, False),
        ({"unit": "PT", "magnitude": 0}, False, True),
        ({"unit": "PT", "magnitude": False}, False, False),
        ({"unit": "PT", "magnitude": 10**1000}, False, False),
        ({"unit": "PT", "magnitude": float("inf")}, False, False),
        ({"unit": "PT", "magnitude": float("nan")}, False, False),
        ({"unit": "SECRET", "magnitude": 0}, False, False),
        ({"unit": "PT", "magnitude": 0, "SECRET": "SECRET"}, False, False),
        (None, False, False),
    ],
)
async def test_default_shapes_are_diagnostic_only(
    dimension: object, *, unit_only: bool, explicit_zero: bool
) -> None:
    header = {
        "content": [
            {
                "paragraph": {
                    "paragraphStyle": {
                        "namedStyleType": "NORMAL_TEXT",
                        "alignment": "START",
                        "spaceAbove": dimension,
                        "keepWithNext": False,
                        "SECRET": "SECRET",
                    },
                    "elements": [{"textRun": {"content": "\n"}}],
                }
            }
        ]
    }
    checks = await _rejected_header_checks(header)
    assert checks["header_recovery_paragraph_spaceAbove_unit_only"] is unit_only
    assert checks["header_recovery_paragraph_spaceAbove_explicit_zero"] is explicit_zero
    assert checks["header_recovery_paragraph_spaceBelow_present"] is False
    assert checks["header_recovery_paragraph_spaceBelow_explicit_zero"] is False
    assert checks["header_recovery_paragraph_keepWithNext_false"] is True
    assert checks["header_recovery_paragraph_unknown_fields"] is True
    assert "SECRET" not in str(checks)


async def test_nonempty_run_style_diagnostics_exclude_private_values() -> None:
    header = {
        "content": [
            {
                "paragraph": {
                    "elements": [
                        {"textRun": {"content": "SECRET", "textStyle": {"SECRET": "SECRET"}}}
                    ]
                }
            }
        ]
    }
    checks = await _rejected_header_checks(header)
    assert checks["header_recovery_run_content_lf"] is False
    assert checks["header_recovery_run_style_present"] is True
    assert checks["header_recovery_run_style_empty"] is False
    assert "SECRET" not in str(checks)


@pytest.mark.parametrize("direction", ["LEFT_TO_RIGHT", "RIGHT_TO_LEFT", "SECRET", None])
@pytest.mark.parametrize("spacing", [0, 100, False, 10**1000, float("inf"), None])
async def test_direction_and_line_spacing_are_finite(direction: object, spacing: object) -> None:
    checks = await _rejected_header_checks(
        {
            "content": [
                {
                    "paragraph": {
                        "paragraphStyle": {"direction": direction, "lineSpacing": spacing},
                        "elements": [{"textRun": {"content": "\n"}}],
                    }
                }
            ]
        }
    )
    assert checks["header_recovery_paragraph_direction_ltr"] is (direction == "LEFT_TO_RIGHT")
    assert checks["header_recovery_paragraph_direction_rtl"] is (direction == "RIGHT_TO_LEFT")
    assert checks["header_recovery_paragraph_lineSpacing_zero"] is (
        type(spacing) is int and spacing == 0
    )
    assert checks["header_recovery_paragraph_lineSpacing_hundred"] is (
        type(spacing) is int and spacing == 100
    )
    assert "SECRET" not in str(checks)


async def _rejected_header_checks(header: dict[str, Any]) -> dict[str, Any]:
    session = _Session(newline=True)
    with pytest.raises(DocumentGatewayError):
        await session.run()
    checkpoint = session.store.checkpoints[-1]
    assert isinstance(session.request.source, DriveFileSource)
    snapshot = await session.gateway.load_drive_source(session.request.source)
    inspection = await session.gateway.inspect_in_place(snapshot=snapshot, title=snapshot.title)
    assert snapshot.google_document is not None
    snapshot.google_document["tabs"][0]["documentTab"]["headers"]["created-0"] = header
    records: list[dict[str, Any]] = []
    sink = logger.add(lambda message: records.append(message.record["extra"]["checks"]))
    try:
        assert (
            recover_empty_header_postcondition(snapshot, inspection, checkpoint, session.operation)
            is None
        )
    finally:
        logger.remove(sink)
    assert len(records) == 1
    return records[0]
