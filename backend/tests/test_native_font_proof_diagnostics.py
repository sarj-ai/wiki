from copy import deepcopy
from typing import Any, Literal

from loguru import logger
import pytest

from app.document_transform.ir import parse_google_document
from app.document_transform.native_font_proof import (
    capture_native_font_proof,
    verify_native_font_proof,
)
from tests.test_custom_furniture import _custom_document, _native_gateway, _verify_preserved
from tests.test_document_transform_repeat import _inspection_snapshot
from tests.test_native_preservation_findings import _native


@pytest.mark.parametrize(
    "reason",
    [
        "matched",
        "document_missing",
        "coverage",
        "duplicate_binding",
        "paragraph_payload",
        "parent_styles",
        "malformed",
    ],
)
@pytest.mark.parametrize("stage", ["resume", "headers", "content"])
def test_font_proof_diagnostics_are_finite_and_preserve_result(
    reason: str, stage: Literal["resume", "headers", "content"]
) -> None:
    source = _native()
    source["documentId"] = "SECRET"
    proofs = capture_native_font_proof(source, parse_google_document(source, title="SECRET"))
    # Old checkpoints omit the optional diagnostic fingerprints entirely.
    proofs = tuple(proof.model_copy(update={"components": None}) for proof in proofs)
    actual: dict[str, Any] | None = deepcopy(source)
    block_ids: tuple[str, ...] = ()
    match reason:
        case "document_missing":
            actual = None
        case "coverage":
            proofs = ()
            block_ids = ("b0",)
        case "duplicate_binding":
            proofs = (*proofs, *proofs)
        case "paragraph_payload":
            actual["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["elements"][0][
                "textRun"
            ]["textStyle"]["bold"] = False
            actual["namedStyles"] = {"unserializable": object()}
        case "parent_styles":
            actual["namedStyles"] = {"SECRET": "SECRET"}
        case "malformed":
            actual["tabs"] = None
        case _:
            pass
    expected = verify_native_font_proof(actual, proofs, preserved_block_ids=block_ids)
    records: list[dict[str, object]] = []
    sink = logger.add(lambda message: records.append(message.record["extra"]["checks"]))
    try:
        result = verify_native_font_proof(
            actual, proofs, preserved_block_ids=block_ids, diagnostic_stage=stage
        )
    finally:
        logger.remove(sink)
    assert result is expected is (reason == "matched")
    assert records == [
        {
            "font_proof_count": len(proofs),
            "font_proof_verified_count": 1 if result else 0,
            "font_proof_reason": reason,
            "font_proof_stage": stage,
            "font_proof_valid": result,
        }
    ]
    assert "SECRET" not in str(records)


def test_default_font_verification_emits_no_new_diagnostic() -> None:
    records: list[object] = []
    sink = logger.add(lambda message: records.append(message.record["extra"]))
    try:
        assert not verify_native_font_proof(None, ())
    finally:
        logger.remove(sink)
    assert not records


def test_font_proof_reports_only_the_verified_prefix_count() -> None:
    source = _native()
    content = source["tabs"][0]["documentTab"]["body"]["content"]
    second = deepcopy(content[0])
    second["startIndex"] += 11
    second["endIndex"] += 11
    for part in second["paragraph"]["elements"]:
        part["startIndex"] += 11
        part["endIndex"] += 11
    content.append(second)
    proofs = capture_native_font_proof(source, parse_google_document(source, title=""))
    second["paragraph"]["elements"][0]["textRun"]["textStyle"]["bold"] = False
    records: list[dict[str, object]] = []
    sink = logger.add(lambda message: records.append(message.record["extra"]["checks"]))
    try:
        assert not verify_native_font_proof(source, proofs, diagnostic_stage="content")
    finally:
        logger.remove(sink)
    assert records[0]["font_proof_count"] == 2
    assert records[0]["font_proof_verified_count"] == 1
    assert records[0]["font_proof_reason"] == "paragraph_payload"


async def test_in_place_diagnostics_reject_supported_brand_without_native_header_title() -> None:
    document = _custom_document(footer=True)
    inspection = _native_gateway()._inspect_in_place_sync(
        _inspection_snapshot(document), "Decision"
    )
    records: list[dict[str, Any]] = []
    sink = logger.add(lambda message: records.append(message.record["extra"]))
    try:
        result = await _verify_preserved(document, inspection, document)
    finally:
        logger.remove(sink)
    assert not result.brand_applied
    assert not result.supported_brand_applied
    assert any(
        record.get("diagnostics", {}).get("supported_brand_applied") is False for record in records
    )
