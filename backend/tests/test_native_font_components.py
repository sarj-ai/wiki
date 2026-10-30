from copy import deepcopy
from typing import Any

from loguru import logger
import pytest

from app.document_transform.ir import parse_google_document
from app.document_transform.native_font_proof import (
    NativeFontProof,
    capture_native_font_proof,
    verify_native_font_proof,
)
from tests.test_native_font_proof import _formatted, _parts
from tests.test_native_preservation_findings import _native


_COMPONENT_FIELDS = {
    "font_proof_paragraph_metadata_equal",
    "font_proof_visible_residual_equal",
    "font_proof_terminal_residual_equal",
    "font_proof_terminal_family_representation_equal",
    "font_proof_parent_context_equal",
}


def _checks(document: dict[str, Any], proofs: tuple[NativeFontProof, ...]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    sink = logger.add(lambda message: records.append(message.record["extra"]["checks"]))
    try:
        assert not verify_native_font_proof(document, proofs, diagnostic_stage="content")
    finally:
        logger.remove(sink)
    assert len(records) == 1
    return records[0]


@pytest.mark.parametrize("component", sorted(_COMPONENT_FIELDS))
def test_components_isolate_mismatch_without_changing_acceptance(component: str) -> None:
    source = _native()
    for part in _parts(source):
        part["textRun"]["textStyle"]["weightedFontFamily"] = {"fontFamily": "Nunito"}
    proofs = capture_native_font_proof(source, parse_google_document(source, title="SECRET"))
    actual = _formatted(source)
    match component:
        case "font_proof_paragraph_metadata_equal":
            actual["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"][
                "paragraphStyle"
            ] = {"alignment": "CENTER"}
        case "font_proof_visible_residual_equal":
            _parts(actual)[0]["textRun"]["textStyle"]["bold"] = False
        case "font_proof_terminal_residual_equal":
            _parts(actual)[-1]["textRun"]["textStyle"]["underline"] = True
        case "font_proof_terminal_family_representation_equal":
            _parts(actual)[-1]["textRun"]["textStyle"].pop("weightedFontFamily")
        case _:
            actual["namedStyles"] = {"styles": []}
    legacy = tuple(
        NativeFontProof.model_validate(item.model_dump(exclude={"components"})) for item in proofs
    )
    assert [item.sha256 for item in legacy] == [item.sha256 for item in proofs]
    assert [item.named_styles_sha256 for item in legacy] == [
        item.named_styles_sha256 for item in proofs
    ]
    assert not verify_native_font_proof(actual, legacy)
    checks = _checks(actual, proofs)
    assert checks["font_proof_components_evaluated"] is True
    assert {key: checks[key] for key in _COMPONENT_FIELDS} == {
        key: key != component for key in _COMPONENT_FIELDS
    }
    assert "font_proof_components_evaluated" not in _checks(actual, legacy)
    assert "SECRET" not in str(checks)
    assert not any(item.sha256 in str(checks) for item in proofs)


def test_diagnostic_fingerprints_are_not_acceptance_authority() -> None:
    source = _native()
    proofs = capture_native_font_proof(source, parse_google_document(source, title=""))
    components = proofs[0].components
    assert components is not None
    altered = proofs[0].model_copy(
        update={"components": components.model_copy(update={"visible_residual_sha256": "0" * 64})}
    )
    assert verify_native_font_proof(source, (altered,))
    assert verify_native_font_proof(_formatted(source), (altered,))
    assert altered.sha256 == proofs[0].sha256


def test_unknown_parent_computation_is_omitted_not_reported_as_inequality() -> None:
    source = _native()
    proofs = capture_native_font_proof(source, parse_google_document(source, title=""))
    actual = deepcopy(source)
    _parts(actual)[0]["textRun"]["textStyle"]["bold"] = False
    actual["namedStyles"] = {"SECRET": object()}
    checks = _checks(actual, proofs)
    assert checks["font_proof_reason"] == "paragraph_payload"
    assert checks["font_proof_visible_residual_equal"] is False
    assert "font_proof_parent_context_equal" not in checks
    assert "SECRET" not in str(checks)


def test_oversized_component_capture_keeps_legacy_acceptance() -> None:
    source = _native()
    source["tabs"][0]["documentTab"]["body"]["content"][0]["opaque"] = "x" * 131073
    proofs = capture_native_font_proof(source, parse_google_document(source, title=""))
    assert proofs[0].components is None
    assert verify_native_font_proof(source, proofs)


def test_oversized_postimage_diagnostics_fail_closed_without_new_verifier_error() -> None:
    source = _native()
    proofs = capture_native_font_proof(source, parse_google_document(source, title=""))
    source["tabs"][0]["documentTab"]["body"]["content"][0]["opaque"] = "SECRET" * 24000
    checks = _checks(source, proofs)
    assert checks["font_proof_reason"] == "paragraph_payload"
    assert checks["font_proof_components_evaluated"] is False
    assert not _COMPONENT_FIELDS.intersection(checks)
    assert "SECRET" not in str(checks)
