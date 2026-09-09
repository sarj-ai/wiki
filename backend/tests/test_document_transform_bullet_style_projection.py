from copy import deepcopy
from dataclasses import replace
import json
from typing import Any

import pytest

from app.document_transform.brand import SarjBrandProfile, build_copy_mutations
from app.document_transform.bullet_style_projection import (
    bullet_style_diagnostics,
    project_branded_bullet_styles,
)
from app.document_transform.gateway import OutputDraft, SourceSnapshot
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.ir import parse_google_document
from app.document_transform.schemas import DocumentOutput
from app.drive.schemas import MIME_DOC
from app.identifiers import DocsRevisionId, DocsTabId, FileId


@pytest.mark.parametrize("changed", [False, True])
@pytest.mark.parametrize("named_style", [None, "NORMAL_TEXT", "HEADING_1", "UNKNOWN"])
def test_matching_native_list_style_preserves_emphasis_without_reserialization(
    changed: bool, named_style: str | None
) -> None:
    document = _document()
    paragraph = document["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]
    paragraph["paragraphStyle"] = {} if named_style is None else {"namedStyleType": named_style}
    paragraph["elements"][0]["textRun"]["textStyle"] = {
        "bold": False,
        "italic": True,
        "underline": False,
    }
    original = parse_google_document(document, title="Review")
    expected = replace(
        original,
        blocks=tuple(
            replace(block, text="Changed text" if changed else block.text)
            for block in original.blocks
        ),
    )
    requests = build_copy_mutations(
        original, expected, profile=SarjBrandProfile(), headers=(), logo_uri=""
    )
    paragraph_requests = [
        request["updateParagraphStyle"] for request in requests if "updateParagraphStyle" in request
    ]
    style_request = paragraph_requests[-1]
    preserve = named_style == "NORMAL_TEXT" and not changed
    assert ("namedStyleType" not in style_request["paragraphStyle"]) == preserve
    assert ("namedStyleType" not in style_request["fields"]) == preserve
    text_requests = [
        request["updateTextStyle"] for request in requests if "updateTextStyle" in request
    ]
    body_style = text_requests[-1]
    omit_underline = preserve or named_style == "HEADING_1"
    assert ("underline" not in body_style["textStyle"]) == omit_underline
    assert ("underline" not in body_style["fields"]) == omit_underline
    reconstructed = build_copy_mutations(
        expected, expected, profile=SarjBrandProfile(), headers=(), logo_uri=""
    )
    reconstructed_styles = [
        request["updateTextStyle"] for request in reconstructed if "updateTextStyle" in request
    ]
    assert reconstructed_styles[-1] == body_style
    assert expected.blocks[0].source_style_text_sha256 == original.blocks[0].sha256


def test_unchanged_editable_bullet_is_not_reported_as_projected() -> None:
    document = _document()
    expected = parse_google_document(document, title="Review")

    counts = bullet_style_diagnostics(expected, expected)

    assert counts["bullet_projection_accepted"] == 0


def test_matching_style_does_not_normalize_missing_bullet_false() -> None:
    document = _document()
    paragraph = document["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]
    paragraph["paragraphStyle"] = {"namedStyleType": "NORMAL_TEXT"}
    paragraph["bullet"]["textStyle"] = {"underline": False}
    actual = deepcopy(document)
    actual["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["bullet"]["textStyle"] = {}
    expected = parse_google_document(document, title="Review")
    assert project_branded_bullet_styles(actual, expected) == actual
    assert (
        bullet_style_diagnostics(parse_google_document(actual, title="Review"), expected)[
            "bullet_underline_removed_false"
        ]
        == 1
    )


@pytest.mark.parametrize(
    "missing", ["source_named_style", "source_style_text_sha256", "native_bullet_json"]
)
def test_matching_list_style_requires_complete_native_provenance(missing: str) -> None:
    document = _document()
    paragraph = document["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]
    paragraph["paragraphStyle"] = {"namedStyleType": "NORMAL_TEXT"}
    original = parse_google_document(document, title="Review")
    expected = replace(
        original, blocks=tuple(replace(block, **{missing: None}) for block in original.blocks)
    )
    requests = build_copy_mutations(
        expected, expected, profile=SarjBrandProfile(), headers=(), logo_uri=""
    )
    paragraph_styles = [
        request["updateParagraphStyle"] for request in requests if "updateParagraphStyle" in request
    ]
    assert paragraph_styles[-1]["paragraphStyle"]["namedStyleType"] == "NORMAL_TEXT"
    assert "namedStyleType" in paragraph_styles[-1]["fields"]


@pytest.mark.parametrize("field", ["bold", "italic", "underline"])
@pytest.mark.parametrize(
    ("before", "after", "direction"),
    [
        (None, True, "added_true"),
        (None, False, "added_false"),
        (True, None, "removed_true"),
        (False, None, "removed_false"),
        (True, False, "changed"),
    ],
)
def test_emphasis_diagnostics_count_exact_direction_without_relaxing_verification(
    field: str,
    before: bool | None,
    after: bool | None,
    direction: str,
) -> None:
    original = _document()
    actual = deepcopy(original)
    for document, value in ((original, before), (actual, after)):
        bullet = document["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["bullet"]
        bullet["textStyle"] = {} if value is None else {field: value}
    expected = parse_google_document(original, title="Private title")
    # Font-only/noneditable means no authored-emphasis override is requested.
    expected = replace(
        expected, blocks=tuple(replace(block, editable=False) for block in expected.blocks)
    )
    counts = bullet_style_diagnostics(
        parse_google_document(actual, title="Private title"), expected
    )
    assert counts[f"bullet_{field}_{direction}"] == 1
    assert counts[f"bullet_{field}_request_present"] == 0
    assert counts[f"bullet_{field}_request_expected_false"] == 0
    assert counts["bullet_emphasis_mismatch"] == 1
    assert all(type(value) is int for value in counts.values())
    assert "Private" not in json.dumps(counts)
    assert project_branded_bullet_styles(actual, expected) == actual


def test_removed_false_records_exact_requested_false_without_acceptance() -> None:
    original = _document()
    bullet = original["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["bullet"]
    bullet["textStyle"] = {"bold": False}
    actual = deepcopy(original)
    actual["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["bullet"]["textStyle"] = {}
    expected = parse_google_document(original, title="Review")
    expected = replace(
        expected,
        blocks=tuple(
            replace(block, authored_emphasis=(("bold", False),)) for block in expected.blocks
        ),
    )
    counts = bullet_style_diagnostics(parse_google_document(actual, title="Review"), expected)
    assert counts["bullet_bold_removed_false"] == 1
    assert counts["bullet_bold_request_present"] == 1
    assert counts["bullet_bold_request_expected_false"] == 1
    assert counts["bullet_projection_accepted"] == 0
    assert project_branded_bullet_styles(actual, expected) == actual


def _document() -> dict[str, Any]:
    return {
        "documentId": "doc",
        "revisionId": "r1",
        "tabs": [
            {
                "tabProperties": {"tabId": "tab"},
                "documentTab": {
                    "body": {
                        "content": [
                            {
                                "startIndex": 1,
                                "endIndex": 6,
                                "paragraph": {
                                    "bullet": {"listId": "numbered", "nestingLevel": 0},
                                    "elements": [
                                        {
                                            "startIndex": 1,
                                            "endIndex": 6,
                                            "textRun": {"content": "Body\n"},
                                        }
                                    ],
                                },
                            }
                        ]
                    },
                    "lists": {
                        "numbered": {
                            "listProperties": {
                                "nestingLevels": [
                                    {
                                        "glyphType": "DECIMAL",
                                        "glyphFormat": "%0.",
                                        "startNumber": 7,
                                    }
                                ]
                            }
                        }
                    },
                },
            }
        ],
    }


def _apply_provider_styles(document: dict[str, Any]) -> dict[str, Any]:
    expected = parse_google_document(document, title="Review")
    actual = deepcopy(document)
    paragraph = actual["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]
    for request in build_copy_mutations(
        expected, expected, profile=SarjBrandProfile(), headers=(), logo_uri=""
    ):
        if "updateTextStyle" not in request:
            continue
        style = request["updateTextStyle"]["textStyle"]
        paragraph["elements"][0]["textRun"].setdefault("textStyle", {}).update(style)
        # Documented provider behavior omitted by the previous native-list fake:
        # full paragraph styling also updates the bullet with matching fields.
        paragraph["bullet"].setdefault("textStyle", {}).update(style)
    return actual


@pytest.mark.parametrize(
    "mutation",
    [
        "planned",
        "provider_defaults",
        "wrong_weight",
        "unknown_font_field",
        "unknown_color_field",
        "wrong_font",
        "wrong_color",
        "nesting",
        "identity",
        "unknown",
        "link",
        "numbering",
    ],
)
async def test_in_place_rejects_all_native_bullet_style_side_effects(mutation: str) -> None:
    original = _document()
    expected = parse_google_document(original, title="Review")
    actual = _apply_provider_styles(original)
    tab = actual["tabs"][0]["documentTab"]
    bullet = tab["body"]["content"][0]["paragraph"]["bullet"]
    match mutation:
        case "provider_defaults":
            bullet["textStyle"]["weightedFontFamily"]["weight"] = 400
            rgb = bullet["textStyle"]["foregroundColor"]["color"]["rgbColor"]
            for channel, value in tuple(rgb.items()):
                if value == 0:
                    del rgb[channel]
                else:
                    rgb[channel] = value + 0.00000001
        case "wrong_weight":
            bullet["textStyle"]["weightedFontFamily"]["weight"] = 700
        case "unknown_font_field":
            bullet["textStyle"]["weightedFontFamily"]["unknown"] = True
        case "unknown_color_field":
            bullet["textStyle"]["foregroundColor"]["color"]["rgbColor"]["alpha"] = 1
        case "wrong_font":
            bullet["textStyle"]["weightedFontFamily"] = {"fontFamily": "Arial"}
        case "wrong_color":
            bullet["textStyle"]["foregroundColor"] = {"color": {"rgbColor": {"red": 1}}}
        case "nesting":
            bullet["nestingLevel"] = 1
        case "identity":
            bullet["listId"] = "other"
        case "unknown":
            bullet["unknown"] = "changed"
        case "link":
            bullet["textStyle"]["link"] = {"url": "https://example.test/unsafe"}
        case "numbering":
            tab["lists"]["numbered"]["listProperties"]["nestingLevels"][0]["startNumber"] = 1
        case "planned":
            pass
        case _:
            raise AssertionError(mutation)
    gateway = object.__new__(GoogleDocumentGateway)
    gateway._logo_uri = "https://example.test/logo.png"
    inspection = await gateway.inspect_in_place(
        snapshot=SourceSnapshot(
            title="Review",
            parent_folder_id=FileId("folder"),
            modified_time="now",
            revision_id=DocsRevisionId("r1"),
            media_type=MIME_DOC,
            google_document=original,
            owned_by_me=True,
            can_edit=True,
            is_personal_drive=True,
        ),
        title="Review",
    )
    verification = await gateway.verify_in_place(
        document=actual,
        title="Review",
        expected=expected,
        structural_sha256=inspection.structural_sha256,
        protected_structure_sha256=inspection.protected_structure_sha256,
        verify_brand=True,
    )
    assert not verification.structure_preserved


@pytest.mark.parametrize("rename", [False, True])
@pytest.mark.parametrize("tamper", [False, True])
@pytest.mark.parametrize("provider_defaults", [False, True])
async def test_copy_verifies_exact_bullet_styles_with_independent_identity_proof(
    monkeypatch: pytest.MonkeyPatch, rename: bool, tamper: bool, provider_defaults: bool
) -> None:
    original = _document()
    expected = parse_google_document(original, title="Review")
    actual = _apply_provider_styles(original)
    tab = actual["tabs"][0]["documentTab"]
    bullet = tab["body"]["content"][0]["paragraph"]["bullet"]
    if provider_defaults:
        bullet["textStyle"]["weightedFontFamily"]["weight"] = 400
    if rename:
        bullet["listId"] = "copy-list"
        tab["lists"]["copy-list"] = tab["lists"].pop("numbered")
    if tamper:
        bullet["textStyle"]["link"] = {"url": "https://example.test/changed"}
    gateway = object.__new__(GoogleDocumentGateway)
    monkeypatch.setattr(gateway, "_get_document_sync", lambda _: actual)
    verification = await gateway.verify_output(
        draft=OutputDraft(
            output=DocumentOutput(
                file_id=FileId("copy"), name="Copy", parent_folder_id=FileId("folder")
            ),
            document_id=FileId("copy"),
            tab_id=DocsTabId("tab"),
            revision_id=DocsRevisionId("r1"),
            source_sha256="source",
        ),
        source=SourceSnapshot(
            title="Review",
            parent_folder_id=FileId("folder"),
            modified_time="now",
            revision_id=DocsRevisionId("r1"),
            media_type=MIME_DOC,
            google_document=original,
        ),
        expected=expected,
        verify_brand=True,
    )
    assert verification.structure_preserved is not tamper
    assert verification.content_preserved is not tamper


def test_bullet_diagnostics_emit_only_static_counts_without_document_content():
    original = _document()
    actual = _apply_provider_styles(original)
    bullet = actual["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["bullet"]
    bullet["textStyle"]["link"] = {"url": "https://private.example/secret"}
    counts = bullet_style_diagnostics(
        parse_google_document(actual, title="Private title"),
        parse_google_document(original, title="Private title"),
    )
    assert counts["bullet_link_mismatch"] == 1
    assert counts["bullet_projection_accepted"] == 0
    assert all(type(value) is int for value in counts.values())
    assert "private" not in repr(counts).lower()
    assert "secret" not in repr(counts).lower()
    assert "numbered" not in repr(counts)
