from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

import pytest

from app.document_transform import slides_ir
from app.document_transform.errors import SourceTooLargeError, UnsupportedSourceError
from app.document_transform.ir import BlockKind, utf16_length
from app.document_transform.slides_ir import SlideId, parse_google_presentation, sarj_logo_object_id


def _text_shape(
    object_id: str,
    content: str,
    *,
    placeholder_type: str | None = None,
    bullet: bool = False,
    link: bool = False,
) -> dict[str, Any]:
    run_content = f"{content}\n"
    paragraph_marker: dict[str, Any] = {"style": {"alignment": "START"}}
    if bullet:
        paragraph_marker["bullet"] = {"listId": "list-1"}
    style: dict[str, Any] = {"fontSize": {"magnitude": 18, "unit": "PT"}}
    if link:
        style["link"] = {"url": "https://example.test"}
    shape: dict[str, Any] = {
        "shapeType": "TEXT_BOX",
        "text": {
            "textElements": [
                {"startIndex": 0, "endIndex": 0, "paragraphMarker": paragraph_marker},
                {
                    "startIndex": 0,
                    "endIndex": utf16_length(run_content),
                    "textRun": {"content": run_content, "style": style},
                },
            ]
        },
    }
    if placeholder_type is not None:
        shape["placeholder"] = {"type": placeholder_type}
    return {
        "objectId": object_id,
        "size": {
            "width": {"magnitude": 400, "unit": "PT"},
            "height": {"magnitude": 80, "unit": "PT"},
        },
        "transform": {"translateX": 24, "translateY": 32, "unit": "PT"},
        "shape": shape,
    }


def _presentation() -> dict[str, Any]:
    return {
        "presentationId": "deck-1",
        "revisionId": "revision-1",
        "title": "Quarterly review source",
        "pageSize": {
            "width": {"magnitude": 720, "unit": "PT"},
            "height": {"magnitude": 405, "unit": "PT"},
        },
        "masters": [{"objectId": "master-1", "pageType": "MASTER"}],
        "layouts": [{"objectId": "layout-1", "pageType": "LAYOUT"}],
        "slides": [
            {
                "objectId": "slide-1",
                "slideProperties": {
                    "layoutObjectId": "layout-1",
                    "notesPage": {
                        "objectId": "notes-1",
                        "pageElements": [
                            _text_shape("speaker-notes", "Do not edit these speaker notes")
                        ],
                        "notesProperties": {"speakerNotesObjectId": "speaker-notes"},
                    },
                },
                "pageElements": [
                    _text_shape("title-1", "Quarterly review", placeholder_type="TITLE"),
                    {
                        "objectId": "group-1",
                        "size": {"width": {"magnitude": 500, "unit": "PT"}},
                        "transform": {"translateX": 16, "translateY": 120, "unit": "PT"},
                        "elementGroup": {
                            "children": [
                                _text_shape("group-copy", "Moreover, results improved"),
                                {
                                    "objectId": "image-1",
                                    "size": {"width": {"magnitude": 180, "unit": "PT"}},
                                    "transform": {
                                        "translateX": 420,
                                        "translateY": 120,
                                        "unit": "PT",
                                    },
                                    "image": {
                                        "contentUrl": "https://example.test/image.png",
                                        "sourceUrl": "https://example.test/source.png",
                                    },
                                },
                            ]
                        },
                    },
                    {
                        "objectId": "table-1",
                        "size": {"width": {"magnitude": 300, "unit": "PT"}},
                        "transform": {"translateX": 20, "translateY": 220, "unit": "PT"},
                        "table": {"rows": 1, "columns": 1, "tableRows": [{"rowHeight": 40}]},
                    },
                    {
                        "objectId": "chart-1",
                        "sheetsChart": {"spreadsheetId": "sheet-1", "chartId": 7},
                    },
                    {"objectId": "video-1", "video": {"source": "YOUTUBE", "id": "abc"}},
                ],
            },
            {
                "objectId": "slide-2",
                "slideProperties": {"layoutObjectId": "layout-1"},
                "pageElements": [
                    _text_shape("subtitle-2", "Next steps", placeholder_type="SUBTITLE")
                ],
            },
        ],
    }


def test_parse_slides_has_stable_order_locators_and_group_paths() -> None:
    first = parse_google_presentation(_presentation(), title="Review deck")
    second = parse_google_presentation(deepcopy(_presentation()), title="Review deck")

    assert first.document == second.document
    assert first.document.source_format == "google_slides"
    assert [block.text for block in first.document.blocks] == [
        "Quarterly review",
        "Moreover, results improved",
        "Next steps",
    ]
    assert [block.kind for block in first.document.blocks] == [
        BlockKind.HEADING,
        BlockKind.PARAGRAPH,
        BlockKind.HEADING,
    ]
    assert [block.level for block in first.document.blocks] == [1, None, 2]
    assert tuple(first.locators) == tuple(second.locators)
    title, grouped, subtitle = tuple(first.locators.values())
    assert (title.slide_id, title.object_id, title.start_index, title.end_index) == (
        "slide-1",
        "title-1",
        0,
        utf16_length("Quarterly review"),
    )
    assert grouped.group_path == ("group-1",)
    assert subtitle.placeholder_type == "SUBTITLE"


def test_protected_inventory_preserves_structure_but_ignores_safe_text_content() -> None:
    source = _presentation()
    parsed = parse_google_presentation(source, title="Review deck")
    first_slide = parsed.protected_inventory.slides[0]

    assert first_slide.object_ids == (
        "title-1",
        "group-1",
        "group-copy",
        "image-1",
        "table-1",
        "chart-1",
        "video-1",
    )
    assert first_slide.object_kinds == (
        "shape",
        "elementGroup",
        "shape",
        "image",
        "table",
        "sheetsChart",
        "video",
    )
    normalized = json.loads(first_slide.normalized_json)
    assert normalized["slideProperties"]["notesPage"]["notesProperties"] == {
        "speakerNotesObjectId": "speaker-notes"
    }
    assert "Quarterly review" not in first_slide.normalized_json
    assert "Do not edit these speaker notes" in first_slide.normalized_json

    text_changed = deepcopy(source)
    title_run = text_changed["slides"][0]["pageElements"][0]["shape"]["text"]["textElements"][1]
    title_run["textRun"]["content"] = "Updated review\n"
    title_run["endIndex"] = utf16_length("Updated review\n")
    assert (
        parse_google_presentation(text_changed, title="Review deck").protected_sha256
        == parsed.protected_sha256
    )

    text_style_changed = deepcopy(source)
    text_style_changed["slides"][0]["pageElements"][0]["shape"]["text"]["textElements"][1][
        "textRun"
    ]["style"] = {
        "bold": True,
        "fontSize": {"magnitude": 42, "unit": "PT"},
        "weightedFontFamily": {"fontFamily": "Google Sans"},
    }
    assert (
        parse_google_presentation(text_style_changed, title="Review deck").protected_sha256
        == parsed.protected_sha256
    )

    native_copy = deepcopy(source)
    native_copy["presentationId"] = "deck-copy"
    native_copy["revisionId"] = "copy-revision"
    native_copy["title"] = "Quarterly review transformed"
    assert (
        parse_google_presentation(
            native_copy, title="Quarterly review transformed"
        ).protected_sha256
        == parsed.protected_sha256
    )

    logo_added = deepcopy(source)
    logo_id = sarj_logo_object_id(SlideId("slide-1"))
    logo_added["slides"][0]["pageElements"].insert(
        0,
        {
            "objectId": logo_id,
            "size": {"width": {"magnitude": 52, "unit": "PT"}},
            "transform": {"translateX": 640, "translateY": 24, "unit": "PT"},
            "image": {"sourceUrl": "https://wiki.sarj.ai/brand/sarj-logo-light.png"},
        },
    )
    logo_parsed = parse_google_presentation(logo_added, title="Review deck")
    assert logo_parsed.protected_sha256 == parsed.protected_sha256
    assert logo_id not in logo_parsed.protected_inventory.slides[0].object_ids
    assert logo_id not in logo_parsed.protected_inventory.slides[0].normalized_json

    volatile_url_changed = deepcopy(source)
    volatile_url_changed["slides"][0]["pageElements"][1]["elementGroup"]["children"][1]["image"][
        "contentUrl"
    ] = "https://temporary.example.test/rotated.png"
    assert (
        parse_google_presentation(volatile_url_changed, title="Review deck").protected_sha256
        == parsed.protected_sha256
    )

    stable_image_source_changed = deepcopy(source)
    stable_image_source_changed["slides"][0]["pageElements"][1]["elementGroup"]["children"][1][
        "image"
    ]["sourceUrl"] = "https://example.test/different-source.png"
    assert (
        parse_google_presentation(stable_image_source_changed, title="Review deck").protected_sha256
        != parsed.protected_sha256
    )

    geometry_changed = deepcopy(source)
    geometry_changed["slides"][0]["pageElements"][0]["transform"]["translateX"] = 25
    assert (
        parse_google_presentation(geometry_changed, title="Review deck").protected_sha256
        != parsed.protected_sha256
    )

    notes_changed = deepcopy(source)
    notes_changed["slides"][0]["slideProperties"]["notesPage"]["notesProperties"][
        "speakerNotesObjectId"
    ] = "other-notes"
    assert (
        parse_google_presentation(notes_changed, title="Review deck").protected_sha256
        != parsed.protected_sha256
    )


@pytest.mark.parametrize(
    ("object_id", "source_url", "nested"),
    [
        pytest.param(
            "sarjLogo-foreign",
            "https://wiki.sarj.ai/brand/sarj-logo-light.png",
            False,
            id="foreign-reserved-prefix",
        ),
        pytest.param(
            sarj_logo_object_id(SlideId("slide-1")),
            "https://example.test/not-the-sarj-logo.png",
            False,
            id="wrong-pinned-source",
        ),
        pytest.param(
            sarj_logo_object_id(SlideId("slide-1")),
            "https://wiki.sarj.ai/brand/sarj-logo-light.png",
            True,
            id="logo-is-not-top-level",
        ),
    ],
)
def test_parser_rejects_untrusted_reserved_logo_objects(
    object_id: str,
    source_url: str,
    nested: bool,
) -> None:
    presentation = _presentation()
    logo = {
        "objectId": object_id,
        "image": {"sourceUrl": source_url},
    }
    page_element = (
        {
            "objectId": "logo-wrapper",
            "elementGroup": {"children": [logo]},
        }
        if nested
        else logo
    )
    presentation["slides"][0]["pageElements"].append(page_element)

    with pytest.raises(UnsupportedSourceError):
        parse_google_presentation(presentation, title="Untrusted logo")


@pytest.mark.parametrize(
    "unsafe_shape",
    [
        _text_shape("unsafe-bullet", "Bulleted copy", bullet=True),
        _text_shape("unsafe-link", "Linked copy", link=True),
        {
            "objectId": "unsafe-runs",
            "shape": {
                "shapeType": "TEXT_BOX",
                "text": {
                    "textElements": [
                        {"startIndex": 0, "endIndex": 0, "paragraphMarker": {}},
                        {
                            "startIndex": 0,
                            "endIndex": 4,
                            "textRun": {"content": "Two ", "style": {"bold": True}},
                        },
                        {
                            "startIndex": 4,
                            "endIndex": 9,
                            "textRun": {"content": "runs\n", "style": {}},
                        },
                    ]
                },
            },
        },
        {
            "objectId": "unsafe-paragraphs",
            "shape": {
                "shapeType": "TEXT_BOX",
                "text": {
                    "textElements": [
                        {"startIndex": 0, "endIndex": 0, "paragraphMarker": {}},
                        {"startIndex": 0, "endIndex": 4, "textRun": {"content": "One\n"}},
                        {"startIndex": 4, "endIndex": 4, "paragraphMarker": {}},
                        {"startIndex": 4, "endIndex": 8, "textRun": {"content": "Two\n"}},
                    ]
                },
            },
        },
    ],
    ids=["bullet", "link", "multiple-runs", "multiple-paragraphs"],
)
def test_parser_protects_text_shapes_that_are_not_plain_single_runs(
    unsafe_shape: dict[str, Any],
) -> None:
    presentation = _presentation()
    presentation["slides"] = [
        {
            "objectId": "slide-unsafe",
            "pageElements": [unsafe_shape],
            "slideProperties": {
                "notesPage": {
                    "objectId": "notes-unsafe",
                    "pageElements": [_text_shape("speaker-notes", "Notes")],
                }
            },
        }
    ]

    parsed = parse_google_presentation(presentation, title="Unsafe deck")

    assert parsed.document.blocks == ()
    assert parsed.locators == {}
    assert parsed.protected_inventory.slides[0].object_ids == (unsafe_shape["objectId"],)
    assert "speaker-notes" in parsed.protected_inventory.slides[0].normalized_json


def test_parser_enforces_slide_text_block_and_element_caps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    presentation = _presentation()
    monkeypatch.setattr(slides_ir, "MAX_PRESENTATION_SLIDES", 1)
    with pytest.raises(SourceTooLargeError):
        parse_google_presentation(presentation, title="Too many slides")

    monkeypatch.setattr(slides_ir, "MAX_PRESENTATION_SLIDES", 200)
    monkeypatch.setattr(slides_ir, "MAX_PRESENTATION_TEXT_UTF16_UNITS", 10)
    with pytest.raises(SourceTooLargeError):
        parse_google_presentation(presentation, title="Too much text")

    monkeypatch.setattr(slides_ir, "MAX_PRESENTATION_TEXT_UTF16_UNITS", 500_000)
    monkeypatch.setattr(slides_ir, "MAX_PRESENTATION_BLOCKS", 1)
    with pytest.raises(SourceTooLargeError):
        parse_google_presentation(presentation, title="Too many blocks")

    monkeypatch.setattr(slides_ir, "MAX_PRESENTATION_BLOCKS", 750)
    monkeypatch.setattr(slides_ir, "MAX_PRESENTATION_ELEMENTS", 1)
    with pytest.raises(SourceTooLargeError):
        parse_google_presentation(presentation, title="Too many elements")


@pytest.mark.parametrize("block_count", [1_062, slides_ir.MAX_PRESENTATION_BLOCKS])
def test_parser_accepts_dense_decks_within_the_editable_block_cap(block_count: int) -> None:
    presentation = _presentation()
    presentation["slides"] = [
        {
            "objectId": "dense-slide",
            "pageElements": [
                _text_shape(f"dense-text-{index}", "Dense cell") for index in range(block_count)
            ],
        }
    ]

    parsed = parse_google_presentation(presentation, title="Dense deck")

    assert len(parsed.document.blocks) == block_count


def test_parser_rejects_decks_over_the_editable_block_cap() -> None:
    presentation = _presentation()
    presentation["slides"] = [
        {
            "objectId": "over-limit-slide",
            "pageElements": [
                _text_shape(f"over-limit-text-{index}", "Dense cell")
                for index in range(slides_ir.MAX_PRESENTATION_BLOCKS + 1)
            ],
        }
    ]

    with pytest.raises(SourceTooLargeError):
        parse_google_presentation(presentation, title="Over-limit deck")


def test_parser_rejects_duplicate_or_missing_native_ids() -> None:
    duplicate = _presentation()
    duplicate["slides"][1]["objectId"] = "slide-1"
    with pytest.raises(UnsupportedSourceError):
        parse_google_presentation(duplicate, title="Duplicate slides")

    missing_object = _presentation()
    del missing_object["slides"][0]["pageElements"][0]["objectId"]
    with pytest.raises(UnsupportedSourceError):
        parse_google_presentation(missing_object, title="Missing object")
