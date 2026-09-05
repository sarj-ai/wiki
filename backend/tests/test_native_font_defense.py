from copy import deepcopy
from typing import Any

import pytest

from app.document_transform.brand import SarjBrandProfile, build_copy_mutations
from app.document_transform.ir import parse_google_document
from app.document_transform.native_font_defense import (
    apply_native_font_preservation,
    defend_native_font_requests,
    preserved_native_font_blocks,
)
from app.document_transform.native_font_noop import filter_redundant_native_fonts
from app.document_transform.native_font_proof import (
    NativeFontVerificationPolicy,
    capture_native_font_proof,
    verify_native_font_proof,
)
from tests.test_native_font_proof import _parts
from tests.test_native_preservation_findings import _native


def _document() -> dict[str, Any]:
    document = _native()
    document["tabs"][0]["documentTab"]["namedStyles"] = {
        "styles": [
            {
                "namedStyleType": "NORMAL_TEXT",
                "textStyle": {
                    "bold": False,
                    "weightedFontFamily": {"fontFamily": "Arial", "weight": 400},
                },
            }
        ]
    }
    return document


def test_authored_bold_survives_family_side_effect_and_terminal_scope_is_explicit() -> None:
    document = _document()
    original = parse_google_document(document, title="Source")
    assert preserved_native_font_blocks(document, original, original) == ()
    requests = build_copy_mutations(
        original,
        original,
        profile=SarjBrandProfile(),
        headers=(),
        logo_uri="https://example.test/logo.png",
    )
    defended = defend_native_font_requests(requests, document=document, original=original)
    styles = [item["updateTextStyle"] for item in defended if "updateTextStyle" in item]
    assert len(styles) == 2
    assert [item["textStyle"]["bold"] for item in styles] == [True, False]
    assert all(item["fields"] == "weightedFontFamily,bold" for item in styles)
    assert all(
        item["textStyle"]["weightedFontFamily"] == {"fontFamily": "Nunito", "weight": 400}
        for item in styles
    )
    assert styles[-1]["range"]["endIndex"] == _parts(document)[-1]["endIndex"]
    # Provider double: family updates reset bold before explicitly supplied bold.
    actual = deepcopy(document)
    for item, part in zip(styles, _parts(actual), strict=True):
        style = part["textRun"]["textStyle"]
        style["bold"] = False
        style.update(item["textStyle"])
    assert _parts(actual)[0]["textRun"]["textStyle"]["bold"] is True
    assert _parts(document)[0]["textRun"]["textStyle"]["bold"] is True
    proof = capture_native_font_proof(document, original, target_font_family="Nunito")
    policy = NativeFontVerificationPolicy(require_target_family=True)
    assert verify_native_font_proof(actual, proof, font_policy=policy)
    assert not verify_native_font_proof(document, proof, font_policy=policy)
    _parts(actual)[0]["textRun"]["textStyle"]["bold"] = False
    assert not verify_native_font_proof(actual, proof, font_policy=policy)


@pytest.mark.parametrize(
    "change",
    [
        "missing_parent",
        "duplicate_parent",
        "invalid_bold",
        "invalid_weight",
        "unknown_family_field",
        "partial",
        "text_change",
    ],
)
def test_ambiguous_font_block_is_preserved_without_font_requests(change: str) -> None:
    document = _document()
    if change == "missing_parent":
        del document["tabs"][0]["documentTab"]["namedStyles"]
    elif change == "duplicate_parent":
        styles = document["tabs"][0]["documentTab"]["namedStyles"]["styles"]
        styles.append(deepcopy(styles[0]))
    elif change == "invalid_bold":
        _parts(document)[0]["textRun"]["textStyle"]["bold"] = "unknown"
    elif change == "invalid_weight":
        _parts(document)[0]["textRun"]["textStyle"]["weightedFontFamily"] = {"weight": 150}
    elif change == "unknown_family_field":
        _parts(document)[0]["textRun"]["textStyle"]["weightedFontFamily"] = {
            "fontFamily": "Arial",
            "weight": 400,
            "unknown": "retained",
        }
    elif change == "partial":
        _parts(document)[0]["textRun"]["suggestedInsertionIds"] = ["pending"]
    original = parse_google_document(document, title="Source")
    transformed = (
        original.replace_blocks({original.blocks[0].id: "Other"})
        if change == "text_change"
        else original
    )
    identifiers = preserved_native_font_blocks(document, original, transformed)
    assert identifiers == (original.blocks[0].id,)
    preserved = apply_native_font_preservation(original, identifiers)
    requests = build_copy_mutations(
        preserved,
        preserved,
        profile=SarjBrandProfile(),
        headers=(),
        logo_uri="https://example.test/logo.png",
    )
    assert not any("updateTextStyle" in item for item in requests)


def test_already_target_family_noops_are_removed_before_defense() -> None:
    document = _document()
    for part in _parts(document):
        part["textRun"]["textStyle"]["weightedFontFamily"] = {"fontFamily": "Nunito", "weight": 400}
    original = parse_google_document(document, title="Source")
    requests = build_copy_mutations(
        original,
        original,
        profile=SarjBrandProfile(),
        headers=(),
        logo_uri="https://example.test/logo.png",
    )
    requests = filter_redundant_native_fonts(
        requests, document=document, original=original, transformed=original, font_family="Nunito"
    )
    assert defend_native_font_requests(requests, document=document, original=original) == requests
    assert not any("updateTextStyle" in item for item in requests)


@pytest.mark.parametrize("field", ["bold", "family", "terminal"])
def test_ambiguous_preserved_font_requires_exact_authored_style(field: str) -> None:
    document = _native()
    original = parse_google_document(document, title="Source")
    identifiers = preserved_native_font_blocks(document, original, original)
    proof = capture_native_font_proof(
        document, original, target_font_family="Nunito", preserved_family_block_ids=identifiers
    )
    policy = NativeFontVerificationPolicy(
        preserved_family_block_ids=identifiers, require_target_family=True
    )
    assert verify_native_font_proof(document, proof, font_policy=policy)
    actual = deepcopy(document)
    part = _parts(actual)[-1 if field == "terminal" else 0]
    if field == "bold":
        part["textRun"]["textStyle"]["bold"] = False
    else:
        part["textRun"]["textStyle"]["weightedFontFamily"] = {"fontFamily": "Nunito"}
    assert not verify_native_font_proof(actual, proof, font_policy=policy)
