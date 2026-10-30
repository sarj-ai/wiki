from copy import deepcopy
import json

import pytest

from app.document_transform.docx_renderer import markdown_import_manifest
from app.document_transform.markdown_import import (
    markdown_import_brand_diagnostics,
    verify_markdown_import,
)
from tests.test_markdown_native_header_title import _titled_document


@pytest.mark.parametrize(
    "change",
    [
        "above_unit_only",
        "below_unit_only",
        "keep_absent",
        "unknown_field",
        "color_huge",
        "weight_wrong",
        "font_wrong",
        "size_wrong",
        "effects_wrong",
        "parent_duplicate",
    ],
)
def test_header_component_checks_preserve_review_without_private_payload(change: str) -> None:
    document = _titled_document("Chosen Title", newline=True)
    tab = document["tabs"][0]["documentTab"]
    paragraph = tab["headers"]["header-1"]["content"][0]["paragraph"]
    style = paragraph["paragraphStyle"]
    run = paragraph["elements"][0]["textRun"]["textStyle"]
    match change:
        case "above_unit_only":
            style["spaceAbove"] = {"unit": "PT"}
            # Zero is valid; an independent font failure keeps component diagnostics evaluated.
            run["weightedFontFamily"]["fontFamily"] = "SECRET"
            field = "import_header_space_above_unit_only"
        case "below_unit_only":
            style["spaceBelow"] = {"unit": "PT"}
            run["weightedFontFamily"]["fontFamily"] = "SECRET"
            field = "import_header_space_below_unit_only"
        case "keep_absent":
            style.pop("keepWithNext")
            field = "import_header_keep_next_absent"
        case "parent_duplicate":
            tab["namedStyles"] = {"styles": [{"namedStyleType": "NORMAL_TEXT"}] * 2}
            field = "import_header_named_identity_known"
        case "unknown_field":
            tab["namedStyles"] = {
                "styles": [{"namedStyleType": "NORMAL_TEXT", "textStyle": {"PRIVATE": "SECRET"}}]
            }
            field = "import_header_run_fields_known"
        case "color_huge":
            run["foregroundColor"] = {"color": {"rgbColor": {"red": 10**400}}}
            field = "import_header_run_color_exact"
        case "weight_wrong":
            run["weightedFontFamily"]["weight"] = 900
            field = "import_header_run_family_weight_exact"
        case "font_wrong":
            run["weightedFontFamily"]["fontFamily"] = "SECRET"
            field = "import_header_run_font_exact"
        case "size_wrong":
            run["fontSize"]["magnitude"] = 10**400
            field = "import_header_run_size_exact"
        case _:
            run["italic"] = "SECRET"
            field = "import_header_run_effects_exact"
    manifest = markdown_import_manifest(
        "Body", title="Import", branded=True, header_title="Chosen Title"
    )
    before = deepcopy(document)
    verification = verify_markdown_import(document, manifest)
    checks = markdown_import_brand_diagnostics(document, manifest)
    assert not verification.brand_applied
    assert verification.content_preserved
    assert verification.structure_preserved
    assert not checks["import_header_title_style"]
    assert checks[field] is (change in {"above_unit_only", "below_unit_only", "keep_absent"})
    assert "import_footer_shape" not in checks
    assert "import_body_style" not in checks
    if change in {"above_unit_only", "below_unit_only"}:
        assert checks["import_header_space_above_exact"]
        assert checks["import_header_space_below_exact"]
        assert not checks["import_header_run_font_exact"]
    if change in {"keep_absent", "parent_duplicate"}:
        assert "import_header_first_failed_run_only" not in checks
    assert all(type(value) is bool for value in checks.values())
    assert "SECRET" not in json.dumps(checks)
    assert "PRIVATE" not in json.dumps(checks)
    assert document == before


def test_failed_logo_does_not_evaluate_header_style_components() -> None:
    document = _titled_document("Chosen Title", newline=True)
    document["tabs"][0]["documentTab"]["inlineObjects"]["logo-1"]["inlineObjectProperties"][
        "embeddedObject"
    ]["description"] = "SECRET"
    manifest = markdown_import_manifest(
        "Body", title="Import", branded=True, header_title="Chosen Title"
    )
    checks = markdown_import_brand_diagnostics(document, manifest)
    assert "import_header_title_style" not in checks
    assert "import_header_named_identity_known" not in checks


def test_valid_header_omits_failure_components() -> None:
    document = _titled_document("Chosen Title", newline=True)
    manifest = markdown_import_manifest(
        "Body", title="Import", branded=True, header_title="Chosen Title"
    )
    checks = markdown_import_brand_diagnostics(document, manifest)
    assert checks["import_header_title_style"]
    assert "import_header_first_failed_run_only" not in checks


@pytest.mark.parametrize(
    "value", [None, {}, [], True, 10**400], ids=["null", "object", "list", "boolean", "huge"]
)
def test_unknown_inherited_fields_do_not_make_malformed_color_diagnostics_raise(
    value: object,
) -> None:
    document = _titled_document("Chosen Title", newline=True)
    tab = document["tabs"][0]["documentTab"]
    tab["namedStyles"] = {
        "styles": [{"namedStyleType": "NORMAL_TEXT", "textStyle": {"PRIVATE": "SECRET"}}]
    }
    run = tab["headers"]["header-1"]["content"][0]["paragraph"]["elements"][0]["textRun"]
    run["textStyle"]["foregroundColor"] = value
    manifest = markdown_import_manifest(
        "Body", title="Import", branded=True, header_title="Chosen Title"
    )
    assert not verify_markdown_import(document, manifest).brand_applied
    checks = markdown_import_brand_diagnostics(document, manifest)
    assert not checks["import_header_run_fields_known"]
    assert not checks["import_header_run_color_exact"]
    assert "SECRET" not in json.dumps(checks)


def test_direct_unknown_run_field_stops_before_style_diagnostics() -> None:
    document = _titled_document("Chosen Title", newline=True)
    tab = document["tabs"][0]["documentTab"]
    tab["headers"]["header-1"]["content"][0]["paragraph"]["elements"][0]["textRun"]["textStyle"][
        "PRIVATE"
    ] = "SECRET"
    manifest = markdown_import_manifest(
        "Body", title="Import", branded=True, header_title="Chosen Title"
    )
    checks = markdown_import_brand_diagnostics(document, manifest)
    assert not checks["import_header_shape"]
    assert "import_header_title_style" not in checks
    assert "import_header_run_fields_known" not in checks
