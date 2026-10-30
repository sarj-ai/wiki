from copy import deepcopy
import json

import pytest

from app.document_transform.docx_renderer import markdown_import_manifest
from app.document_transform.markdown_import import (
    markdown_import_brand_diagnostics,
    verify_markdown_import,
)
from tests.test_markdown_native_header_title import _titled_document


@pytest.mark.parametrize("inherited", [False, True])
def test_explicit_unit_only_zero_header_spacing_matches_live_readback(*, inherited: bool) -> None:
    document = _titled_document("Chosen Title", newline=True)
    tab = document["tabs"][0]["documentTab"]
    style = tab["headers"]["header-1"]["content"][0]["paragraph"]["paragraphStyle"]
    if inherited:
        style.pop("spaceAbove")
        style.pop("spaceBelow")
        tab["namedStyles"] = {
            "styles": [
                {
                    "namedStyleType": "NORMAL_TEXT",
                    "paragraphStyle": {"spaceAbove": {"unit": "PT"}, "spaceBelow": {"unit": "PT"}},
                }
            ]
        }
    else:
        style.update(spaceAbove={"unit": "PT"}, spaceBelow={"unit": "PT"})
    before = deepcopy(document)
    manifest = markdown_import_manifest(
        "Body", title="Import", branded=True, header_title="Chosen Title"
    )
    result = verify_markdown_import(document, manifest)
    checks = markdown_import_brand_diagnostics(document, manifest)
    assert result.brand_applied
    assert result.content_preserved
    assert result.structure_preserved
    assert checks["import_header_title_style"]
    assert checks["import_footer_shape"]
    assert checks["import_body_style"]
    assert document == before


@pytest.mark.parametrize("field", ["spaceAbove", "spaceBelow", "fontSize"])
@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"magnitude": 0},
        {"unit": "PX"},
        {"unit": "PT", "secret": "PRIVATE"},
        {"unit": "PT", "magnitude": None},
        {"unit": "PT", "magnitude": False},
        {"unit": "PT", "magnitude": True},
        {"unit": "PT", "magnitude": "0"},
        {"unit": "PT", "magnitude": float("nan")},
        {"unit": "PT", "magnitude": float("inf")},
        {"unit": "PT", "magnitude": -float("inf")},
        {"unit": "PT", "magnitude": 10**400},
        {"unit": "PT", "magnitude": -1},
    ],
    ids=[
        "null",
        "empty",
        "missing-unit",
        "wrong-unit",
        "unknown-field",
        "null-magnitude",
        "false-magnitude",
        "true-magnitude",
        "string-magnitude",
        "nan",
        "positive-infinity",
        "negative-infinity",
        "huge-integer",
        "negative-magnitude",
    ],
)
def test_zero_dimension_acceptance_rejects_malformed_and_nonzero_values(
    field: str, value: object
) -> None:
    document = _titled_document("Chosen Title", newline=True)
    paragraph = document["tabs"][0]["documentTab"]["headers"]["header-1"]["content"][0]["paragraph"]
    target = (
        paragraph["elements"][0]["textRun"]["textStyle"]
        if field == "fontSize"
        else paragraph["paragraphStyle"]
    )
    target[field] = value
    manifest = markdown_import_manifest(
        "Body", title="Import", branded=True, header_title="Chosen Title"
    )
    result = verify_markdown_import(document, manifest)
    checks = markdown_import_brand_diagnostics(document, manifest)
    assert not result.brand_applied
    assert result.content_preserved
    assert not checks["import_header_title_style"]
    assert "PRIVATE" not in json.dumps(checks)


def test_unit_only_dimension_does_not_satisfy_nonzero_font_size() -> None:
    document = _titled_document("Chosen Title", newline=True)
    paragraph = document["tabs"][0]["documentTab"]["headers"]["header-1"]["content"][0]["paragraph"]
    paragraph["elements"][0]["textRun"]["textStyle"]["fontSize"] = {"unit": "PT"}
    manifest = markdown_import_manifest(
        "Body", title="Import", branded=True, header_title="Chosen Title"
    )
    assert not verify_markdown_import(document, manifest).brand_applied
