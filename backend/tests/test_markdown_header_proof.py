from copy import deepcopy
from dataclasses import replace
import json
from typing import Any

import pytest

from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.docx_renderer import markdown_import_manifest
from app.document_transform.markdown_import import (
    markdown_import_brand_diagnostics,
    verify_markdown_import,
)
from tests.test_markdown_import import _branded_document


def _header_document(*, newline: bool = True) -> dict[str, Any]:
    document = _branded_document()
    runs: list[dict[str, Any]] = [
        {"startIndex": 0, "endIndex": 1, "inlineObjectElement": {"inlineObjectId": "logo-1"}}
    ]
    if newline:
        runs.append({"startIndex": 1, "endIndex": 2, "textRun": {"content": "\n", "textStyle": {}}})
    document["tabs"][0]["documentTab"]["headers"]["header-1"]["content"] = [
        {"startIndex": 0, "endIndex": len(runs), "paragraph": {"elements": runs}}
    ]
    return document


@pytest.mark.parametrize("newline", [False, True])
@pytest.mark.parametrize("indices", ["full", "none", "start_only", "end_only"])
def test_exact_header_accepts_legal_native_metadata(*, newline: bool, indices: str) -> None:
    document = _header_document(newline=newline)
    outer = document["tabs"][0]["documentTab"]["headers"]["header-1"]["content"][0]
    for wrapper in [outer, *outer["paragraph"]["elements"]]:
        if indices in {"none", "end_only"}:
            wrapper.pop("startIndex")
        if indices in {"none", "start_only"}:
            wrapper.pop("endIndex")
    before = deepcopy(document)
    manifest = replace(
        markdown_import_manifest("Body", title="Import", branded=True), header_title=None
    )
    result = verify_markdown_import(document, manifest)
    assert result.brand_applied
    assert result.content_preserved
    assert result.structure_preserved
    checks = markdown_import_brand_diagnostics(document, manifest)
    assert all(
        value
        for key, value in checks.items()
        if key not in {"import_logo_source_uri_absent", "import_logo_source_uri_exact"}
    )
    assert checks["import_logo_source_uri_absent"] or checks["import_logo_source_uri_exact"]
    assert document == before


@pytest.mark.parametrize(
    "change",
    [
        "unknown_wrapper",
        "suggestion",
        "foreign_text",
        "extra_logo",
        "extra_lf",
        "link",
        "wrong_id",
        "wrong_metadata",
        "wrong_dimensions",
        "outer_end",
        "gap",
        "boolean",
        "negative",
        "wrong_width",
        "reversed",
        "unknown_payload",
    ],
)
def test_header_proof_never_accepts_extra_or_changed_logo_content(change: str) -> None:
    document = _header_document()
    tab = document["tabs"][0]["documentTab"]
    outer = tab["headers"]["header-1"]["content"][0]
    runs = outer["paragraph"]["elements"]
    match change:
        case "unknown_wrapper":
            runs[0]["unknown"] = {}
        case "suggestion":
            runs[0]["suggestedInsertionIds"] = ["SECRET"]
        case "foreign_text":
            runs[1]["textRun"]["content"] = "SECRET"
        case "extra_logo" | "extra_lf":
            runs.append(deepcopy(runs[0 if change == "extra_logo" else 1]))
        case "link":
            runs[1]["textRun"]["textStyle"]["link"] = {"url": "https://example.com/SECRET"}
        case "wrong_id":
            runs[0]["inlineObjectElement"]["inlineObjectId"] = "SECRET"
        case "wrong_metadata":
            tab["inlineObjects"]["logo-1"]["inlineObjectProperties"]["embeddedObject"][
                "description"
            ] = "SECRET"
        case "wrong_dimensions":
            tab["inlineObjects"]["logo-1"]["inlineObjectProperties"]["embeddedObject"]["size"][
                "width"
            ]["magnitude"] = 100
        case "outer_end":
            outer["endIndex"] = 3
        case "gap":
            runs[1].update(startIndex=2, endIndex=3)
        case "boolean":
            runs[0]["startIndex"] = False
        case "negative":
            runs[0]["startIndex"] = -1
        case "wrong_width":
            runs[0]["endIndex"] = 2
        case "reversed":
            runs.reverse()
        case _:
            runs[0]["inlineObjectElement"]["unknown"] = {}
    manifest = replace(
        markdown_import_manifest("Body", title="Import", branded=True), header_title=None
    )
    assert not verify_markdown_import(document, manifest).brand_applied
    checks = markdown_import_brand_diagnostics(document, manifest)
    assert all(type(value) is bool for value in checks.values())
    assert "SECRET" not in json.dumps(checks)


def test_unchecked_diagnostics_are_omitted_not_claimed_as_failures() -> None:
    document = _header_document()
    manifest = replace(
        markdown_import_manifest("Body", title="Import", branded=True), header_title=None
    )
    document["tabs"][0]["documentTab"]["headers"] = {}
    checks = markdown_import_brand_diagnostics(document, manifest)
    assert checks["import_header_shape"] is False
    assert checks["import_logo_object"] is False
    assert "import_logo_metadata" not in checks
    assert "import_logo_dimensions" not in checks
    document["tabs"].append(deepcopy(document["tabs"][0]))
    checks = markdown_import_brand_diagnostics(document, manifest)
    assert checks["import_single_tab"] is False
    assert "import_body_style" not in checks
    assert "import_margins" not in checks
    unbranded = markdown_import_manifest("Body", title="Import", branded=False)
    assert markdown_import_brand_diagnostics(document, unbranded) == {
        "import_brand_requested": False
    }


@pytest.mark.parametrize(
    "magnitude", [10**400, {}, None, "SECRET"], ids=["huge", "object", "null", "string"]
)
def test_diagnostics_preserve_logo_metadata_short_circuit(magnitude: object) -> None:
    document = _header_document()
    tab = document["tabs"][0]["documentTab"]
    embedded = tab["inlineObjects"]["logo-1"]["inlineObjectProperties"]["embeddedObject"]
    embedded["description"] = "invalid"
    embedded["size"]["width"]["magnitude"] = magnitude
    manifest = replace(
        markdown_import_manifest("Body", title="Import", branded=True), header_title=None
    )
    before = verify_markdown_import(document, manifest)
    checks = markdown_import_brand_diagnostics(document, manifest)
    assert not before.brand_applied
    assert before.content_preserved
    assert before.structure_preserved
    assert checks["import_logo_metadata"] is False
    assert "import_logo_dimensions" not in checks
    assert "import_footer_shape" not in checks
    assert "import_body_style" not in checks
    assert verify_markdown_import(document, manifest) == before
    assert "SECRET" not in json.dumps(checks)


@pytest.mark.parametrize(
    "change", ["title", "description", "malformed", "absent", "null", "empty", "foreign", "exact"]
)
def test_logo_metadata_components_are_static_and_do_not_relax_proof(change: str) -> None:
    document = _header_document()
    embedded = document["tabs"][0]["documentTab"]["inlineObjects"]["logo-1"][
        "inlineObjectProperties"
    ]["embeddedObject"]
    embedded["imageProperties"] = {}
    if change in {"title", "description"}:
        embedded[change] = "SECRET"
    elif change == "malformed":
        embedded["imageProperties"] = "SECRET"
    elif change != "absent":
        embedded["imageProperties"]["sourceUri"] = {
            "null": None,
            "empty": "",
            "foreign": "SECRET",
            "exact": CURRENT_BRAND_MANIFEST.assets.logo_uri,
        }[change]
    manifest = replace(
        markdown_import_manifest("Body", title="Import", branded=True), header_title=None
    )
    checks = markdown_import_brand_diagnostics(document, manifest)
    expected = change in {"absent", "null", "exact"}
    assert checks["import_logo_metadata"] is expected
    assert verify_markdown_import(document, manifest).brand_applied is expected
    assert checks["import_logo_title_exact"] is (change != "title")
    assert checks["import_logo_description_exact"] is (change != "description")
    assert checks["import_logo_image_properties_valid"] is (change != "malformed")
    if change == "malformed":
        assert "import_logo_source_uri_absent" not in checks
        assert "import_logo_source_uri_exact" not in checks
    else:
        assert checks["import_logo_source_uri_absent"] is (
            change in {"title", "description", "absent", "null"}
        )
        assert checks["import_logo_source_uri_exact"] is (change == "exact")
    assert all(type(value) is bool for value in checks.values())
    assert "SECRET" not in json.dumps(checks)
