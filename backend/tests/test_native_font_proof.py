from copy import deepcopy
from typing import Any, Literal

from pydantic import ValidationError
import pytest

from app.document_transform.ir import parse_google_document
from app.document_transform.native_font_proof import (
    NativeFontProof,
    capture_native_font_proof,
    verify_native_font_proof,
)
from tests.test_native_preservation_findings import _native


def _parts(document: dict[str, Any]) -> list[dict[str, Any]]:
    return document["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["elements"]


def _formatted(document: dict[str, Any]) -> dict[str, Any]:
    actual = deepcopy(document)
    parts = _parts(actual)
    last = deepcopy(parts[-1])
    parts[-1]["endIndex"] -= 1
    parts[-1]["textRun"]["content"] = "Beta"
    last["startIndex"] = last["endIndex"] - 1
    last["textRun"]["content"] = "\n"
    for part in parts:
        part["textRun"]["textStyle"]["weightedFontFamily"] = {"fontFamily": "Nunito"}
    parts.append(last)
    return actual


def test_only_authorized_font_family_and_equivalent_provider_fragmentation_are_accepted():
    original = _native()
    proofs = capture_native_font_proof(original, parse_google_document(original, title="Source"))
    assert verify_native_font_proof(original, proofs)
    actual = _formatted(original)
    assert verify_native_font_proof(actual, proofs)
    assert not verify_native_font_proof(actual, (), preserved_block_ids=("b0",))
    part = _parts(actual)[0]
    remainder = deepcopy(part)
    part["endIndex"] = 3
    part["textRun"]["content"] = "Al"
    remainder["startIndex"] = 3
    remainder["textRun"]["content"] = "pha "
    _parts(actual).insert(1, remainder)
    assert verify_native_font_proof(actual, proofs)
    assert verify_native_font_proof(actual, proofs, preserved_block_ids=("b0",))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bold", False),
        ("italic", True),
        ("underline", True),
        ("link", {"url": "https://example.test/changed"}),
        ("fontSize", {"magnitude": 18, "unit": "PT"}),
        ("unknownProperty", "changed"),
        ("weightedFontFamily", {"fontFamily": "Nunito", "weight": 700}),
    ],
)
def test_font_proof_rejects_non_font_style_changes(field: str, value: object):
    original = _native()
    proofs = capture_native_font_proof(original, parse_google_document(original, title="Source"))
    actual = _formatted(original)
    _parts(actual)[0]["textRun"]["textStyle"][field] = value
    assert not verify_native_font_proof(actual, proofs)


@pytest.mark.parametrize("change", ["text", "index", "tab", "parent", "terminator", "missing"])
def test_font_proof_rejects_topology_content_parent_and_unauthorized_terminator_changes(
    change: Literal["text", "index", "tab", "parent", "terminator", "missing"],
):
    original = _native()
    proofs = capture_native_font_proof(original, parse_google_document(original, title="Source"))
    actual = _formatted(original)
    match change:
        case "text":
            _parts(actual)[0]["textRun"]["content"] = "Other "
        case "index":
            _parts(actual)[0]["startIndex"] = True
        case "tab":
            actual["tabs"][0]["tabProperties"]["tabId"] = "other"
        case "parent":
            actual["tabs"][0]["documentTab"]["namedStyles"] = {"styles": [{"bold": True}]}
        case "terminator":
            _parts(actual)[-1]["textRun"]["textStyle"]["weightedFontFamily"] = {
                "fontFamily": "Nunito"
            }
        case "missing":
            _parts(actual).pop()
    assert not verify_native_font_proof(actual, proofs)
    assert not verify_native_font_proof(original, (*proofs, *proofs))


def test_partial_proof_coverage_and_unknown_versions_fail_closed():
    original = _native()
    content = original["tabs"][0]["documentTab"]["body"]["content"]
    second = deepcopy(content[0])
    second["startIndex"] += 11
    second["endIndex"] += 11
    for part in second["paragraph"]["elements"]:
        part["startIndex"] += 11
        part["endIndex"] += 11
    content.append(second)
    proofs = capture_native_font_proof(original, parse_google_document(original, title="Source"))
    assert len(proofs) == 2
    assert verify_native_font_proof(original, proofs, preserved_block_ids=("b0", "b1"))
    assert not verify_native_font_proof(original, proofs[:1], preserved_block_ids=("b0", "b1"))
    with pytest.raises(ValidationError):
        NativeFontProof.model_validate({**proofs[0].model_dump(), "version": 2})


@pytest.mark.parametrize(
    "family", [None, False, "", {"unknown": "value"}], ids=["null", "boolean", "empty", "object"]
)
def test_malformed_font_family_is_not_normalized_away(family: object):
    original = _native()
    proofs = capture_native_font_proof(original, parse_google_document(original, title="Source"))
    actual = _formatted(original)
    _parts(actual)[0]["textRun"]["textStyle"]["weightedFontFamily"]["fontFamily"] = family
    assert not verify_native_font_proof(actual, proofs)
