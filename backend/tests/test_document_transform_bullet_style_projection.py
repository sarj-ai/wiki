from copy import deepcopy
from dataclasses import dataclass, replace
import json
from typing import Any, override

from pydantic import JsonValue
import pytest

from app.document_transform.brand import SarjBrandProfile, build_copy_mutations
from app.document_transform.bullet_style_projection import (
    bullet_style_diagnostics,
    project_branded_bullet_styles,
)
from app.document_transform.gateway import NativeDocumentSnapshot, OutputDraft
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.ir import DocumentIR, parse_google_document
from app.document_transform.schemas import DocumentOutput
from app.document_transform.source_access import NativeSourceAccess, PersonalDrive
from app.identifiers import DocsRevisionId, DocsTabId, FileId
from tests.json_values import json_array_at, json_object_at


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
            json_object_at(bullet, "textStyle", "weightedFontFamily")["weight"] = 400
            color = json_object_at(bullet, "textStyle", "foregroundColor", "color")
            rgb = json_object_at(color, "rgbColor")
            rounded: dict[str, JsonValue] = {}
            for channel, value in rgb.items():
                assert isinstance(value, int | float)
                if value != 0:
                    rounded[channel] = value + 0.00000001
            color["rgbColor"] = rounded
        case "wrong_weight":
            json_object_at(bullet, "textStyle", "weightedFontFamily")["weight"] = 700
        case "unknown_font_field":
            bullet["textStyle"]["weightedFontFamily"]["unknown"] = True
        case "unknown_color_field":
            json_object_at(bullet, "textStyle", "foregroundColor", "color", "rgbColor")["alpha"] = 1
        case "wrong_font":
            bullet["textStyle"]["weightedFontFamily"] = {"fontFamily": "Arial"}
        case "wrong_color":
            json_object_at(bullet, "textStyle")["foregroundColor"] = {
                "color": {"rgbColor": {"red": 1}}
            }
        case "nesting":
            bullet["nestingLevel"] = 1
        case "identity":
            bullet["listId"] = "other"
        case "unknown":
            bullet["unknown"] = "changed"
        case "link":
            json_object_at(bullet, "textStyle")["link"] = {"url": "https://example.test/unsafe"}
        case "numbering":
            json_object_at(tab, "lists", "numbered", "listProperties", "nestingLevels", 0)[
                "startNumber"
            ] = 1
        case "planned":
            pass
        case _:
            raise AssertionError(mutation)
    gateway = object.__new__(GoogleDocumentGateway)
    gateway._logo_uri = "https://example.test/logo.png"
    inspection = await gateway.inspect_in_place(
        snapshot=NativeDocumentSnapshot(
            title="Review",
            parent_folder_id=FileId("folder"),
            modified_time="now",
            revision_id=DocsRevisionId("r1"),
            google_document=original,
            access=NativeSourceAccess(owned_by_me=True, can_edit=True, location=PersonalDrive()),
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
        json_object_at(bullet, "textStyle", "weightedFontFamily")["weight"] = 400
    if rename:
        bullet["listId"] = "copy-list"
        tab["lists"]["copy-list"] = tab["lists"].pop("numbered")
    if tamper:
        json_object_at(bullet, "textStyle")["link"] = {"url": "https://example.test/changed"}
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
        source=NativeDocumentSnapshot(
            title="Review",
            parent_folder_id=FileId("folder"),
            modified_time="now",
            revision_id=DocsRevisionId("r1"),
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
    json_object_at(bullet, "textStyle")["link"] = {"url": "https://private.example/secret"}
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


@dataclass(frozen=True, slots=True)
class _RebasedNativeListCase:
    source: NativeDocumentSnapshot
    expected: DocumentIR
    copied: NativeDocumentSnapshot


def _rebased_native_list_case() -> _RebasedNativeListCase:
    original = _document()
    tab = original["tabs"][0]["documentTab"]
    item = tab["body"]["content"][0]
    item["paragraph"]["paragraphStyle"] = {"namedStyleType": "NORMAL_TEXT"}
    item["paragraph"]["bullet"]["textStyle"] = {}
    prefix = deepcopy(item)
    prefix["paragraph"].pop("bullet")
    tab["body"]["content"] = [prefix, item]
    texts = [
        "An unnecessarily verbose 🦊 prefix.\n",
        "Moreover, preserve exact measurement context.\n",
    ]
    source = NativeDocumentSnapshot(
        title="Review",
        parent_folder_id=FileId("folder"),
        modified_time="now",
        revision_id=DocsRevisionId("r1"),
        google_document=original,
    )
    _set_native_paragraph_texts(source, texts)
    tab["lists"]["unreferenced"] = deepcopy(tab["lists"]["numbered"])
    original_ir = parse_google_document(original, title="Review")
    expected = original_ir.replace_blocks(
        {"b0": "Short 🦊.", "b1": "Preserve exact measurement context."}
    )
    copied = deepcopy(original)
    copied_snapshot = replace(source, google_document=copied)
    _set_native_paragraph_texts(copied_snapshot, [block.text + "\n" for block in expected.blocks])
    copied_tab = copied["tabs"][0]["documentTab"]
    copied_tab["lists"].pop("unreferenced")
    copied_tab["body"]["content"][1]["paragraph"]["bullet"]["textStyle"] = {
        "weightedFontFamily": {"fontFamily": "Nunito", "weight": 400},
        "foregroundColor": {
            "color": {
                "rgbColor": {
                    "red": 0.039215688,
                    "green": 0.039215688,
                    "blue": 0.039215688,
                }
            }
        },
    }
    return _RebasedNativeListCase(source, expected, copied_snapshot)


def _set_native_paragraph_texts(snapshot: NativeDocumentSnapshot, texts: list[str]) -> None:
    document = snapshot.google_document
    assert document is not None
    cursor = 1
    for raw_element, text in zip(
        json_array_at(document, "tabs", 0, "documentTab", "body", "content"), texts, strict=True
    ):
        element = json_object_at(raw_element)
        end = cursor + len(text.encode("utf-16-le")) // 2
        element["startIndex"], element["endIndex"] = cursor, end
        json_object_at(element, "paragraph")["elements"] = [
            {"startIndex": cursor, "endIndex": end, "textRun": {"content": text}}
        ]
        cursor = end


@pytest.mark.parametrize(
    "mutation",
    [
        "planned",
        "wrong_font",
        "wrong_weight",
        "wrong_color",
        "link",
        "unknown_style",
        "unknown_bullet",
        "nesting",
        "identity",
        "numbering",
    ],
)
async def test_copy_bullet_projection_tracks_shortened_utf16_prefix_and_list_item(
    mutation: str,
) -> None:
    case = _rebased_native_list_case()
    source, expected, copied = case.source, case.expected, case.copied
    assert source.google_document is not None
    assert copied.google_document is not None
    untouched = deepcopy(source.google_document)
    actual = copied.google_document
    tab = json_object_at(actual, "tabs", 0, "documentTab")
    bullet = json_object_at(tab, "body", "content", 1, "paragraph", "bullet")
    match mutation:
        case "planned":
            pass
        case "wrong_font":
            json_object_at(bullet, "textStyle", "weightedFontFamily")["fontFamily"] = "Arial"
        case "wrong_weight":
            json_object_at(bullet, "textStyle", "weightedFontFamily")["weight"] = 700
        case "wrong_color":
            json_object_at(bullet, "textStyle")["foregroundColor"] = {
                "color": {"rgbColor": {"red": 1}}
            }
        case "link":
            json_object_at(bullet, "textStyle")["link"] = {"url": "https://example.test/changed"}
        case "unknown_style":
            json_object_at(bullet, "textStyle")["futureStyle"] = True
        case "unknown_bullet":
            bullet["futureBullet"] = True
        case "nesting":
            bullet["nestingLevel"] = 1
        case "identity":
            bullet["listId"] = "other"
        case "numbering":
            json_object_at(tab, "lists", "numbered", "listProperties", "nestingLevels", 0)[
                "startNumber"
            ] = 1
        case _:
            pytest.fail(f"Unknown bullet mutation: {mutation}")
    counts = bullet_style_diagnostics(parse_google_document(actual, title="Review"), expected)
    if mutation == "planned":
        assert counts["bullet_projection_accepted"] == 1
        assert counts["bullet_font_mismatch"] == 0
        assert counts["bullet_color_mismatch"] == 0

    class _ReadbackGateway(GoogleDocumentGateway):
        @override
        def _get_document_sync(self, file_id: FileId):
            assert file_id == FileId("copy")
            return actual

    gateway = object.__new__(_ReadbackGateway)
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
        source=source,
        expected=expected,
        verify_brand=True,
    )
    assert verification.structure_preserved is (mutation == "planned")
    if mutation != "numbering":
        assert verification.content_preserved is (mutation == "planned")
    assert source.google_document == untouched


def test_subparagraph_styles_do_not_authorize_bullet_changes_after_prefix_rebase() -> None:
    case = _rebased_native_list_case()
    source, copied = case.source, case.copied
    assert source.google_document is not None
    assert copied.google_document is not None
    original = parse_google_document(source.google_document, title="Review")
    expected = original.replace_blocks({"b0": "Short 🦊."})
    _set_native_paragraph_texts(copied, [block.text + "\n" for block in expected.blocks])
    assert project_branded_bullet_styles(copied.google_document, expected) == copied.google_document
    counts = bullet_style_diagnostics(
        parse_google_document(copied.google_document, title="Review"), expected
    )
    assert counts["bullet_projection_accepted"] == 0
