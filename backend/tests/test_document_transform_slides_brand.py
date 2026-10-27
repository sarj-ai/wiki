from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, NamedTuple

import pytest

from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.errors import UnsupportedSourceError
from app.document_transform.schemas import TransformMode
from app.document_transform.slides_brand import (
    build_slides_mutation_plan,
    presentation_fingerprint,
)
from app.document_transform.slides_ir import (
    SlideId,
    parse_google_presentation,
    sarj_logo_object_id,
)


_LONG_NARRATIVE = (
    "Detailed implementation guidance explains the operating sequence, evidence requirements, "
    "review controls, ownership boundaries, and expected decision points."
)


class _LogoGeometry(NamedTuple):
    left: float
    top: float
    width: float
    height: float


def test_format_plan_preserves_text_and_has_verifiable_brand_fingerprint() -> None:
    presentation = _presentation()
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    assert not any("deleteText" in request or "insertText" in request for request in plan.requests)
    assert sum("updateTextStyle" in request for request in plan.requests) == 2
    assert sum("createImage" in request for request in plan.requests) == 1
    assert plan.findings == ()
    assert plan.expected_brand_sha256 is not None

    branded = deepcopy(presentation)
    _apply_brand_requests(branded, plan.requests)
    fingerprints = presentation_fingerprint(branded, "Review — Sarj formatted")
    assert fingerprints.brand_sha256 == plan.expected_brand_sha256
    assert fingerprints.protected_structure_sha256 == parsed.protected_sha256


def test_unslop_plan_emits_reverse_exact_deletes_and_case_only_insertion() -> None:
    presentation = _presentation()
    parsed = parse_google_presentation(presentation, title="Review")
    first, second = parsed.document.blocks
    transformed = parsed.document.replace_blocks({second.id: "Results improved"})

    plan = build_slides_mutation_plan(
        presentation,
        parsed=parsed,
        transformed=transformed,
        mode=TransformMode.UNSLOP,
        logo_uri="",
    )

    assert first.text == "Quarterly review"
    assert plan.expected_brand_sha256 is None
    assert [next(iter(request)) for request in plan.requests] == [
        "deleteText",
        "insertText",
        "deleteText",
    ]
    assert all("replaceAllText" not in request for request in plan.requests)
    deletes = [request["deleteText"] for request in plan.requests if "deleteText" in request]
    assert [delete["textRange"]["startIndex"] for delete in deletes] == [10, 0]
    assert all(delete["textRange"]["type"] == "FIXED_RANGE" for delete in deletes)
    assert plan.requests[1]["insertText"] == {
        "objectId": "body-1",
        "insertionIndex": 10,
        "text": "R",
    }


def test_both_styles_before_deletion_and_brand_fingerprint_matches_shorter_text() -> None:
    presentation = _presentation()
    parsed = parse_google_presentation(presentation, title="Review")
    body = parsed.document.blocks[1]
    transformed = parsed.document.replace_blocks({body.id: "Results improved"})

    plan = build_slides_mutation_plan(
        presentation,
        parsed=parsed,
        transformed=transformed,
        mode=TransformMode.BOTH,
        logo_uri=CURRENT_BRAND_MANIFEST.assets.logo_uri,
    )

    keys = [next(iter(request)) for request in plan.requests]
    assert keys.index("updateTextStyle") < keys.index("deleteText")
    branded = deepcopy(presentation)
    _apply_brand_requests(branded, plan.requests)
    assert presentation_fingerprint(branded, "Review").brand_sha256 == plan.expected_brand_sha256


def test_unslop_rejects_non_deletion_non_case_rewrite() -> None:
    presentation = _presentation()
    parsed = parse_google_presentation(presentation, title="Review")
    second = parsed.document.blocks[1]
    transformed = parsed.document.replace_blocks({second.id: "Revenue improved"})

    with pytest.raises(UnsupportedSourceError):
        build_slides_mutation_plan(
            presentation,
            parsed=parsed,
            transformed=transformed,
            mode=TransformMode.UNSLOP,
            logo_uri="",
        )


def test_rich_bulleted_text_is_preserved_and_reported_for_review() -> None:
    presentation = _presentation()
    presentation["slides"][0]["pageElements"].append(_rich_bulleted_shape())
    parsed = parse_google_presentation(presentation, title="Review")
    original = deepcopy(presentation["slides"][0]["pageElements"][-1])

    plan = _format_plan(presentation, parsed=parsed)

    assert not any(
        request.get("updateTextStyle", {}).get("objectId") == "rich-1" for request in plan.requests
    )
    assert "slide_text_target_unsupported" in {finding.code for finding in plan.findings}

    branded = deepcopy(presentation)
    _apply_brand_requests(branded, plan.requests)
    rich = next(
        element
        for element in branded["slides"][0]["pageElements"]
        if element["objectId"] == "rich-1"
    )
    original_runs = _text_run_styles(original)
    branded_runs = _text_run_styles(rich)
    assert branded_runs == original_runs


def test_unslop_reports_rich_visible_text_that_cannot_be_scanned() -> None:
    presentation = _presentation()
    rich = _rich_bulleted_shape()
    rich["shape"]["text"]["textElements"][1]["textRun"]["content"] = (
        "Moreover, we reviewed the plan."
    )
    presentation["slides"][0]["pageElements"] = [rich]
    parsed = parse_google_presentation(presentation, title="Review")

    plan = build_slides_mutation_plan(
        presentation,
        parsed=parsed,
        transformed=parsed.document,
        mode=TransformMode.UNSLOP,
        logo_uri="",
    )

    assert plan.requests == []
    assert [finding.code for finding in plan.findings] == ["slide_text_target_unsupported"]


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (
            lambda shape: shape["shape"]["text"]["textElements"][1]["textRun"].pop("style"),
            "slide_text_style_unresolved",
        ),
        (
            lambda shape: (
                shape.pop("shape"),
                shape.update(
                    {"table": {"tableRows": [{"tableCells": [{"text": {"content": "Visible"}}]}]}}
                ),
            ),
            "slide_text_target_unsupported",
        ),
        (
            lambda shape: (
                shape.pop("shape"),
                shape.update({"wordArt": {"renderedText": "Visible"}}),
            ),
            "slide_text_target_unsupported",
        ),
    ],
)
def test_unsupported_visible_text_target_produces_honest_finding(
    mutate: Any,
    expected_code: str,
) -> None:
    presentation = _presentation()
    target = _text_shape(
        "unsupported-1",
        "Visible",
        placeholder_type="BODY",
        left=24,
        top=230,
        width=200,
    )
    mutate(target)
    presentation["slides"][0]["pageElements"].append(target)
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    assert expected_code in [finding.code for finding in plan.findings]
    assert not any(
        request.get("updateTextStyle", {}).get("objectId") == "unsupported-1"
        for request in plan.requests
    )


def test_exact_existing_logo_is_idempotent_and_fingerprint_includes_asset_identity() -> None:
    presentation = _presentation()
    parsed = parse_google_presentation(presentation, title="Review")
    first = _format_plan(presentation, parsed=parsed)
    branded = deepcopy(presentation)
    _apply_brand_requests(branded, first.requests)
    branded_parsed = parse_google_presentation(branded, title="Review")

    second = _format_plan(branded, parsed=branded_parsed)

    assert not any("createImage" in request for request in second.requests)
    assert second.findings == ()
    assert (
        presentation_fingerprint(
            branded,
            "Review — Sarj formatted",
        ).brand_sha256
        == second.expected_brand_sha256
    )


def test_logo_geometry_scales_manifest_canvas_and_fits_manifest_box() -> None:
    presentation = _presentation()
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    create = next(request["createImage"] for request in plan.requests if "createImage" in request)
    size = create["elementProperties"]["size"]
    transform = create["elementProperties"]["transform"]
    expected = _expected_top_right_logo(presentation)
    assert size["width"]["magnitude"] == pytest.approx(expected.width)
    assert size["height"]["magnitude"] == pytest.approx(expected.height)
    assert transform["translateX"] == pytest.approx(expected.left)
    assert transform["translateY"] == pytest.approx(expected.top)
    assert expected.top == pytest.approx(36)


def test_nonmatching_canvas_aspect_fails_closed_for_logo_only() -> None:
    presentation = _presentation()
    presentation["pageSize"]["height"]["magnitude"] = 540
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    assert not any("createImage" in request for request in plan.requests)
    assert any("updateTextStyle" in request for request in plan.requests)
    assert [finding.code for finding in plan.findings] == ["slide_geometry_unsafe"]


def test_wrong_existing_logo_source_is_not_accepted_as_brand_success() -> None:
    presentation = _presentation()
    parsed = parse_google_presentation(presentation, title="Review")
    first = _format_plan(presentation, parsed=parsed)
    branded = deepcopy(presentation)
    _apply_brand_requests(branded, first.requests)
    logo = branded["slides"][0]["pageElements"][-1]
    logo["image"]["sourceUrl"] = "https://example.invalid/lookalike.png"

    plan = _format_plan(branded, parsed=parsed)

    assert [finding.code for finding in plan.findings] == ["slide_logo_id_collision"]
    assert not any("createImage" in request for request in plan.requests)
    with pytest.raises(UnsupportedSourceError):
        presentation_fingerprint(branded, "Review")


def test_hidden_existing_logo_is_not_accepted_as_brand_success() -> None:
    presentation = _presentation()
    parsed = parse_google_presentation(presentation, title="Review")
    first = _format_plan(presentation, parsed=parsed)
    branded = deepcopy(presentation)
    _apply_brand_requests(branded, first.requests)
    logo = branded["slides"][0]["pageElements"][-1]
    logo["image"]["imageProperties"] = {"transparency": 1}

    plan = _format_plan(branded, parsed=parsed)

    assert [finding.code for finding in plan.findings] == ["slide_logo_id_collision"]
    with pytest.raises(UnsupportedSourceError):
        presentation_fingerprint(branded, "Review")


def test_existing_logo_with_off_page_geometry_reports_geometry_finding() -> None:
    presentation = _presentation()
    parsed = parse_google_presentation(presentation, title="Review")
    first = _format_plan(presentation, parsed=parsed)
    branded = deepcopy(presentation)
    _apply_brand_requests(branded, first.requests)
    branded["slides"][0]["pageElements"][-1]["transform"]["translateX"] = 900

    plan = _format_plan(branded, parsed=parsed)

    assert "slide_geometry_unsafe" in [finding.code for finding in plan.findings]
    assert not any("createImage" in request for request in plan.requests)


def test_deck_global_logo_id_collision_inside_element_group_fails_closed() -> None:
    presentation = _presentation()
    parsed = parse_google_presentation(presentation, title="Review")
    logo_id = sarj_logo_object_id(SlideId("slide-1"))
    presentation["masters"] = [
        {
            "objectId": "master-1",
            "pageElements": [
                {
                    **_box("group-1", left=0, top=0, width=10, height=10),
                    "elementGroup": {"children": [_box(logo_id, left=0, top=0, width=1, height=1)]},
                }
            ],
        }
    ]

    plan = _format_plan(presentation, parsed=parsed)

    assert "slide_logo_id_collision" in [finding.code for finding in plan.findings]
    assert not any("createImage" in request for request in plan.requests)


@pytest.mark.parametrize("bad_geometry", ["missing", "zero", "nan", "off_page"])
def test_unknown_or_unsafe_geometry_blocks_logo_insertion(bad_geometry: str) -> None:
    presentation = _presentation()
    parsed = parse_google_presentation(presentation, title="Review")
    target = presentation["slides"][0]["pageElements"][0]
    match bad_geometry:
        case "missing":
            target.pop("transform")
        case "zero":
            target["size"]["width"]["magnitude"] = 0
        case "nan":
            target["transform"]["translateX"] = math.nan
        case _:
            target["transform"]["translateX"] = 800
    plan = _format_plan(presentation, parsed=parsed)

    assert "slide_geometry_unsafe" in [finding.code for finding in plan.findings]
    assert not any("createImage" in request for request in plan.requests)


def test_element_group_shape_is_a_style_target() -> None:
    presentation = _presentation()
    presentation["slides"][0]["pageElements"].append(
        {
            **_box("group-1", left=20, top=220, width=220, height=80),
            "elementGroup": {
                "children": [_text_shape("grouped-text", "Grouped", left=0, top=0, width=180)]
            },
        }
    )
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    assert not any(
        request.get("updateTextStyle", {}).get("objectId") == "grouped-text"
        for request in plan.requests
    )
    assert "slide_text_role_unresolved" in {finding.code for finding in plan.findings}


def test_logo_collision_fails_open_to_review_without_covering_content() -> None:
    presentation = _presentation()
    slide = presentation["slides"][0]
    slide["pageElements"].extend(
        _box(f"corner-{index}", left=left, top=top, width=80, height=50)
        for index, (left, top) in enumerate(((590, 50), (590, 300), (50, 300)))
    )
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    assert not any("createImage" in request for request in plan.requests)
    assert [finding.code for finding in plan.findings] == ["slide_logo_collision"]


def test_logo_placement_avoids_inherited_layout_and_master_content() -> None:
    presentation = _presentation()
    presentation["slides"][0]["slideProperties"] = {"layoutObjectId": "layout-1"}
    presentation["layouts"] = [
        {
            "objectId": "layout-1",
            "layoutProperties": {"masterObjectId": "master-1"},
            "pageElements": [_box("layout-corner", left=590, top=300, width=100, height=80)],
        }
    ]
    presentation["masters"] = [
        {
            "objectId": "master-1",
            "pageElements": [_box("master-corner", left=590, top=50, width=100, height=80)],
        }
    ]
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    create = next(request["createImage"] for request in plan.requests if "createImage" in request)
    transform = create["elementProperties"]["transform"]
    expected = _expected_top_right_logo(presentation)
    bottom = presentation["pageSize"]["height"]["magnitude"] - expected.top - expected.height
    actual = (transform["translateX"], transform["translateY"])
    assert not any(
        actual == pytest.approx(candidate)
        for candidate in ((expected.left, expected.top), (expected.left, bottom))
    )


def test_inherited_placeholder_template_does_not_block_logo_placement() -> None:
    presentation = _presentation()
    presentation["slides"][0]["slideProperties"] = {"layoutObjectId": "layout-1"}
    presentation["layouts"] = [
        {
            "objectId": "layout-1",
            "layoutProperties": {},
            "pageElements": [
                _text_shape(
                    "layout-title",
                    "Template title",
                    placeholder_type="TITLE",
                    left=590,
                    top=50,
                    width=100,
                )
            ],
        }
    ]
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    create = next(request["createImage"] for request in plan.requests if "createImage" in request)
    transform = create["elementProperties"]["transform"]
    expected = _expected_top_right_logo(presentation)
    assert transform["translateX"] == pytest.approx(expected.left)
    assert transform["translateY"] == pytest.approx(expected.top)


def test_caption_placeholder_uses_caption_size_and_muted_color() -> None:
    presentation = _presentation()
    presentation["slides"][0]["pageElements"].append(
        _text_shape(
            "footer-1",
            "Confidential",
            placeholder_type="FOOTER",
            left=24,
            top=360,
            width=200,
        )
    )
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    update = next(
        request["updateTextStyle"]
        for request in plan.requests
        if request.get("updateTextStyle", {}).get("objectId") == "footer-1"
    )
    assert update["style"]["fontSize"] == {
        "magnitude": CURRENT_BRAND_MANIFEST.slides.caption_size_pt,
        "unit": "PT",
    }


def test_dark_slide_skips_dark_brand_text_and_black_logo() -> None:
    presentation = _presentation()
    presentation["slides"][0]["pageProperties"] = {
        "pageBackgroundFill": {
            "solidFill": {"color": {"rgbColor": {"red": 0.04, "green": 0.04, "blue": 0.04}}}
        }
    }
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    assert not any("updateTextStyle" in request for request in plan.requests)
    assert not any("createImage" in request for request in plan.requests)
    assert {finding.code for finding in plan.findings} == {
        "slide_logo_contrast_unresolved",
        "slide_text_contrast_unresolved",
    }


@pytest.mark.parametrize(
    "page_background_fill",
    [
        {"solidFill": {"color": {"rgbColor": {"red": 0.04, "green": 0.04, "blue": 0.04}}}},
        {
            "propertyState": "RENDERED",
            "stretchedPictureFill": {"contentUrl": "https://example.invalid/background.png"},
        },
    ],
    ids=["dark", "unresolved"],
)
def test_unsafe_background_removes_only_exact_existing_logo(
    page_background_fill: dict[str, Any],
) -> None:
    presentation = _presentation()
    first = _format_plan(
        presentation,
        parsed=parse_google_presentation(presentation, title="Review"),
    )
    branded = deepcopy(presentation)
    _apply_brand_requests(branded, first.requests)
    branded["slides"][0]["pageProperties"] = {
        "pageBackgroundFill": page_background_fill,
    }
    parsed = parse_google_presentation(branded, title="Review")

    second = _format_plan(branded, parsed=parsed)

    logo_id = sarj_logo_object_id(SlideId("slide-1"))
    assert [request for request in second.requests if "deleteObject" in request] == [
        {"deleteObject": {"objectId": logo_id}}
    ]
    assert not any("createImage" in request for request in second.requests)
    assert {finding.code for finding in second.findings} == {
        "slide_logo_contrast_unresolved",
        "slide_text_contrast_unresolved",
    }
    updated = deepcopy(branded)
    _apply_brand_requests(updated, second.requests)
    fingerprints = presentation_fingerprint(updated, "Review")
    assert fingerprints.brand_sha256 == second.expected_brand_sha256
    assert fingerprints.protected_structure_sha256 == parsed.protected_sha256


def test_unsafe_background_does_not_delete_reserved_logo_collision() -> None:
    presentation = _presentation()
    parsed = parse_google_presentation(presentation, title="Review")
    first = _format_plan(presentation, parsed=parsed)
    branded = deepcopy(presentation)
    _apply_brand_requests(branded, first.requests)
    branded["slides"][0]["pageProperties"] = {
        "pageBackgroundFill": {
            "solidFill": {"color": {"rgbColor": {"red": 0.04, "green": 0.04, "blue": 0.04}}}
        }
    }
    branded["slides"][0]["pageElements"][-1]["image"]["sourceUrl"] = (
        "https://example.invalid/lookalike.png"
    )

    plan = _format_plan(branded, parsed=parsed)

    assert not any("deleteObject" in request for request in plan.requests)
    assert "slide_logo_id_collision" in {finding.code for finding in plan.findings}


def test_unsafe_background_does_not_delete_mirrored_logo_collision() -> None:
    presentation = _presentation()
    parsed = parse_google_presentation(presentation, title="Review")
    first = _format_plan(presentation, parsed=parsed)
    branded = deepcopy(presentation)
    _apply_brand_requests(branded, first.requests)
    branded["slides"][0]["pageProperties"] = {
        "pageBackgroundFill": {
            "solidFill": {"color": {"rgbColor": {"red": 0.04, "green": 0.04, "blue": 0.04}}}
        }
    }
    logo = branded["slides"][0]["pageElements"][-1]
    logo["transform"]["scaleX"] = -1
    logo["transform"]["translateX"] += logo["size"]["width"]["magnitude"]

    plan = _format_plan(branded, parsed=parsed)

    assert not any("deleteObject" in request for request in plan.requests)
    assert "slide_logo_id_collision" in {finding.code for finding in plan.findings}


def test_theme_colors_resolve_through_slide_layout_master_chain() -> None:
    presentation = _presentation_with_theme_background("LIGHT1")
    title_style = presentation["slides"][0]["pageElements"][0]["shape"]["text"]["textElements"][1][
        "textRun"
    ]["style"]
    title_style["foregroundColor"] = {"opaqueColor": {"themeColor": "DARK1"}}
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    assert plan.findings == ()
    assert sum("updateTextStyle" in request for request in plan.requests) == 2
    assert sum("createImage" in request for request in plan.requests) == 1
    branded = deepcopy(presentation)
    _apply_brand_requests(branded, plan.requests)
    assert presentation_fingerprint(branded, "Review").brand_sha256 == plan.expected_brand_sha256


def test_dark_theme_background_fails_closed() -> None:
    presentation = _presentation_with_theme_background("DARK1")
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    assert not any("updateTextStyle" in request for request in plan.requests)
    assert not any("createImage" in request for request in plan.requests)
    assert {finding.code for finding in plan.findings} == {
        "slide_logo_contrast_unresolved",
        "slide_text_contrast_unresolved",
    }


def test_inherited_placeholder_style_is_preserved_for_review() -> None:
    presentation = _presentation()
    title = presentation["slides"][0]["pageElements"][0]
    title["shape"]["placeholder"]["parentObjectId"] = "layout-title"
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    assert not any(
        request.get("updateTextStyle", {}).get("objectId") == "title-1" for request in plan.requests
    )
    assert "slide_text_style_inherited_unresolved" in {finding.code for finding in plan.findings}


def test_autofit_text_is_preserved_for_review() -> None:
    presentation = _presentation()
    body = presentation["slides"][0]["pageElements"][1]
    body["shape"]["shapeProperties"]["autofit"]["autofitType"] = "TEXT_AUTOFIT"
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    assert not any(
        request.get("updateTextStyle", {}).get("objectId") == "body-1" for request in plan.requests
    )
    assert "slide_text_fit_unresolved" in {finding.code for finding in plan.findings}


@pytest.mark.parametrize("placeholder_type", ["BODY", "OBJECT"])
def test_long_resolved_small_narrative_is_advisory_only(placeholder_type: str) -> None:
    presentation = _presentation()
    narrative = _text_shape(
        "body-1",
        _LONG_NARRATIVE,
        placeholder_type=placeholder_type,
        left=24,
        top=140,
        width=500,
    )
    narrative_style = narrative["shape"]["text"]["textElements"][1]["textRun"]["style"]
    narrative_style["fontSize"]["magnitude"] = 11
    presentation["slides"][0]["pageElements"][1] = narrative
    floor_presentation = deepcopy(presentation)
    floor_presentation["slides"][0]["pageElements"][1]["shape"]["text"]["textElements"][1][
        "textRun"
    ]["style"]["fontSize"]["magnitude"] = 12

    plan = _format_plan(
        presentation,
        parsed=parse_google_presentation(presentation, title="Review"),
    )
    floor_plan = _format_plan(
        floor_presentation,
        parsed=parse_google_presentation(floor_presentation, title="Review"),
    )

    assert plan.requests == floor_plan.requests
    assert [finding.code for finding in plan.findings].count("slide_text_too_small") == 1
    assert "slide_text_too_small" not in {finding.code for finding in floor_plan.findings}


@pytest.mark.parametrize(
    "placeholder_type",
    [
        "TITLE",
        "CENTERED_TITLE",
        "SUBTITLE",
        "HEADER",
        "FOOTER",
        "DATE_AND_TIME",
        "SLIDE_NUMBER",
        None,
    ],
)
def test_small_non_narrative_text_is_not_flagged(placeholder_type: str | None) -> None:
    presentation = _presentation()
    element = _text_shape(
        "non-narrative",
        _LONG_NARRATIVE,
        placeholder_type=placeholder_type,
        left=24,
        top=140,
        width=500,
    )
    element["shape"]["text"]["textElements"][1]["textRun"]["style"]["fontSize"]["magnitude"] = 11
    presentation["slides"][0]["pageElements"] = [element]

    plan = _format_plan(
        presentation,
        parsed=parse_google_presentation(presentation, title="Review"),
    )

    assert "slide_text_too_small" not in {finding.code for finding in plan.findings}


@pytest.mark.parametrize(("character_count", "expected"), [(79, False), (80, True)])
def test_small_narrative_text_length_boundary(character_count: int, expected: bool) -> None:
    presentation = _presentation()
    body = _text_shape(
        "body-1",
        "x" * character_count,
        placeholder_type="BODY",
        left=24,
        top=140,
        width=500,
    )
    body["shape"]["text"]["textElements"][1]["textRun"]["style"]["fontSize"]["magnitude"] = 11
    presentation["slides"][0]["pageElements"] = [body]
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    assert ("slide_text_too_small" in {finding.code for finding in plan.findings}) is expected


def test_small_rich_table_and_chart_text_are_not_flagged() -> None:
    presentation = _presentation()
    rich = _rich_bulleted_shape()
    rich["shape"]["placeholder"] = {"type": "BODY"}
    rich["shape"]["text"]["textElements"][1]["textRun"]["content"] = _LONG_NARRATIVE
    presentation["slides"][0]["pageElements"] = [
        rich,
        {
            "objectId": "table-1",
            "table": {
                "tableRows": [
                    {
                        "tableCells": [
                            {"text": {"textElements": rich["shape"]["text"]["textElements"]}}
                        ]
                    }
                ]
            },
        },
        {"objectId": "chart-1", "sheetsChart": {"spreadsheetId": "sheet-1", "chartId": 7}},
    ]
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    assert "slide_text_too_small" not in {finding.code for finding in plan.findings}


@pytest.mark.parametrize(
    "unsafe_state",
    ["autofit", "inheritance", "style", "contrast", "background"],
)
def test_small_narrative_requires_resolved_fit_inheritance_and_contrast(
    unsafe_state: str,
) -> None:
    presentation = _presentation()
    narrative = _text_shape(
        "body-1",
        _LONG_NARRATIVE,
        placeholder_type="BODY",
        left=24,
        top=140,
        width=500,
    )
    run_style = narrative["shape"]["text"]["textElements"][1]["textRun"]["style"]
    run_style["fontSize"]["magnitude"] = 11
    presentation["slides"][0]["pageElements"] = [narrative]
    match unsafe_state:
        case "autofit":
            narrative["shape"]["shapeProperties"]["autofit"]["autofitType"] = "TEXT_AUTOFIT"
        case "inheritance":
            narrative["shape"]["placeholder"]["parentObjectId"] = "missing-parent"
        case "style":
            run_style.pop("weightedFontFamily")
        case "contrast":
            run_style["foregroundColor"] = {
                "opaqueColor": {"rgbColor": {"red": 1, "green": 1, "blue": 1}}
            }
        case _:
            presentation["slides"][0]["pageProperties"] = {
                "pageBackgroundFill": {
                    "propertyState": "RENDERED",
                    "stretchedPictureFill": {
                        "contentUrl": "https://example.invalid/background.png"
                    },
                }
            }

    plan = _format_plan(
        presentation,
        parsed=parse_google_presentation(presentation, title="Review"),
    )

    assert "slide_text_too_small" not in {finding.code for finding in plan.findings}


def test_font_family_change_is_preserved_for_review() -> None:
    presentation = _presentation()
    body = presentation["slides"][0]["pageElements"][1]
    body["shape"]["text"]["textElements"][1]["textRun"]["style"]["weightedFontFamily"] = {
        "fontFamily": "Roboto Condensed"
    }
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    assert not any(
        request.get("updateTextStyle", {}).get("objectId") == "body-1" for request in plan.requests
    )
    assert "slide_text_fit_unresolved" in {finding.code for finding in plan.findings}


def test_text_highlight_contrast_blocks_brand_recoloring() -> None:
    presentation = _presentation()
    title_style = presentation["slides"][0]["pageElements"][0]["shape"]["text"]["textElements"][1][
        "textRun"
    ]["style"]
    title_style["backgroundColor"] = {
        "opaqueColor": {"rgbColor": {"red": 0, "green": 0, "blue": 0}}
    }
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    assert not any(
        request.get("updateTextStyle", {}).get("objectId") == "title-1" for request in plan.requests
    )
    assert "slide_text_contrast_unresolved" in {finding.code for finding in plan.findings}


def test_visual_underlay_blocks_all_text_recoloring() -> None:
    presentation = _presentation()
    presentation["slides"][0]["pageElements"].insert(
        0,
        {
            **_box("background-image", left=0, top=0, width=720, height=405),
            "image": {"sourceUrl": "https://example.invalid/background.png"},
        },
    )
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    assert not any("updateTextStyle" in request for request in plan.requests)
    assert "slide_text_contrast_unresolved" in {finding.code for finding in plan.findings}


@pytest.mark.parametrize("kind", ["line", "sheetsChart", "wordArt"])
def test_native_visual_underlay_blocks_text_recoloring(kind: str) -> None:
    presentation = _presentation()
    payload: dict[str, Any] = {"renderedText": "Backdrop"} if kind == "wordArt" else {}
    presentation["slides"][0]["pageElements"].insert(
        0,
        {
            **_box("visual-underlay", left=0, top=0, width=720, height=405),
            kind: payload,
        },
    )
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    assert not any("updateTextStyle" in request for request in plan.requests)
    assert "slide_text_contrast_unresolved" in {finding.code for finding in plan.findings}


def test_filled_text_shape_is_treated_as_a_visual_underlay() -> None:
    presentation = _presentation()
    underlay = _text_shape("filled-underlay", "Backdrop", left=0, top=0, width=720)
    underlay["size"]["height"]["magnitude"] = 405
    underlay["shape"]["shapeProperties"]["shapeBackgroundFill"] = {
        "propertyState": "RENDERED",
        "solidFill": {"color": {"rgbColor": {}}, "alpha": 1},
    }
    presentation["slides"][0]["pageElements"].insert(0, underlay)
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    assert not any("updateTextStyle" in request for request in plan.requests)
    assert "slide_text_contrast_unresolved" in {finding.code for finding in plan.findings}


def test_font_weight_is_preserved_and_verified() -> None:
    presentation = _presentation()
    title_style = presentation["slides"][0]["pageElements"][0]["shape"]["text"]["textElements"][1][
        "textRun"
    ]["style"]
    title_style["weightedFontFamily"]["weight"] = 700
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)
    update = next(
        request["updateTextStyle"]
        for request in plan.requests
        if request.get("updateTextStyle", {}).get("objectId") == "title-1"
    )

    assert update["style"]["weightedFontFamily"]["weight"] == 700
    branded = deepcopy(presentation)
    _apply_brand_requests(branded, plan.requests)
    branded["slides"][0]["pageElements"][0]["shape"]["text"]["textElements"][1]["textRun"]["style"][
        "weightedFontFamily"
    ]["weight"] = 400
    assert presentation_fingerprint(branded, "Review").brand_sha256 != plan.expected_brand_sha256


def test_resolved_parent_placeholder_style_is_supported() -> None:
    presentation = _presentation()
    title = presentation["slides"][0]["pageElements"][0]
    title["shape"]["placeholder"]["parentObjectId"] = "layout-title"
    title["shape"]["text"]["textElements"][1]["textRun"]["style"] = {}
    title["shape"]["shapeProperties"] = {}
    presentation["slides"][0]["slideProperties"] = {"layoutObjectId": "layout-1"}
    presentation["layouts"] = [
        {
            "objectId": "layout-1",
            "layoutProperties": {},
            "pageElements": [
                _text_shape(
                    "layout-title",
                    "Title placeholder",
                    placeholder_type="TITLE",
                    left=24,
                    top=20,
                    width=400,
                )
            ],
        }
    ]
    _use_native_split_terminal_newline(title)
    layout_title = presentation["layouts"][0]["pageElements"][0]
    assert isinstance(layout_title, dict)
    _use_native_split_terminal_newline(layout_title)
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    assert any(
        request.get("updateTextStyle", {}).get("objectId") == "title-1" for request in plan.requests
    )
    assert "slide_text_style_inherited_unresolved" not in {
        finding.code for finding in plan.findings
    }


@pytest.mark.parametrize("style_levels", [8, 9])
def test_canonical_provider_placeholder_style_table_resolves_level_zero(
    style_levels: int,
) -> None:
    presentation = _presentation_with_canonical_inherited_title(
        master_level_zero_style={
            "fontSize": {"magnitude": 60, "unit": "PT"},
            "foregroundColor": {
                "opaqueColor": {"rgbColor": {"red": 0.1, "green": 0.1, "blue": 0.1}}
            },
            "weightedFontFamily": {
                "fontFamily": CURRENT_BRAND_MANIFEST.document.font_family,
                "weight": 700,
            },
        },
        style_levels=style_levels,
    )
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    update = next(
        request["updateTextStyle"]
        for request in plan.requests
        if request.get("updateTextStyle", {}).get("objectId") == "title-1"
    )
    assert update["style"]["weightedFontFamily"]["weight"] == 700
    assert "slide_text_style_inherited_unresolved" not in {
        finding.code for finding in plan.findings
    }


def test_split_parent_inheritance_uses_terminal_newline_style() -> None:
    presentation = _presentation()
    title = presentation["slides"][0]["pageElements"][0]
    title["shape"]["placeholder"]["parentObjectId"] = "layout-title"
    title["shape"]["text"]["textElements"][1]["textRun"]["style"] = {}
    presentation["slides"][0]["slideProperties"] = {"layoutObjectId": "layout-1"}
    layout_title = _text_shape(
        "layout-title",
        "Layout",
        placeholder_type="TITLE",
        left=24,
        top=20,
        width=400,
    )
    _use_native_split_terminal_newline(layout_title)
    visible_style = layout_title["shape"]["text"]["textElements"][1]["textRun"]["style"]
    visible_style["weightedFontFamily"]["weight"] = 300
    terminal_style = layout_title["shape"]["text"]["textElements"][2]["textRun"]["style"]
    terminal_style["weightedFontFamily"]["weight"] = 700
    presentation["layouts"] = [
        {
            "objectId": "layout-1",
            "layoutProperties": {},
            "pageElements": [layout_title],
        }
    ]
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    update = next(
        request["updateTextStyle"]
        for request in plan.requests
        if request.get("updateTextStyle", {}).get("objectId") == "title-1"
    )
    assert update["style"]["weightedFontFamily"]["weight"] == 700


@pytest.mark.parametrize(
    "malformation",
    [
        "boolean_bound",
        "boolean_nesting",
        "paragraph_bounds",
        "paragraph_union",
        "run_union",
    ],
)
def test_malformed_placeholder_style_table_fails_closed(malformation: str) -> None:
    presentation = _presentation_with_canonical_inherited_title(
        master_level_zero_style={
            "fontSize": {"magnitude": 60, "unit": "PT"},
            "foregroundColor": {
                "opaqueColor": {"rgbColor": {"red": 0.1, "green": 0.1, "blue": 0.1}}
            },
            "weightedFontFamily": {
                "fontFamily": CURRENT_BRAND_MANIFEST.document.font_family,
                "weight": 700,
            },
        }
    )
    layout_elements = presentation["layouts"][0]["pageElements"][0]["shape"]["text"]["textElements"]
    match malformation:
        case "boolean_bound":
            layout_elements[0]["startIndex"] = False
        case "boolean_nesting":
            layout_elements[0]["paragraphMarker"]["bullet"]["nestingLevel"] = False
        case "paragraph_bounds":
            layout_elements[0]["endIndex"] = 2
        case "paragraph_union":
            layout_elements[0]["textRun"] = {"content": "\n", "style": {}}
        case _:
            layout_elements[1]["paragraphMarker"] = {"style": {}}
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    assert not any(
        request.get("updateTextStyle", {}).get("objectId") == "title-1" for request in plan.requests
    )
    assert "slide_text_style_inherited_unresolved" in {finding.code for finding in plan.findings}


def test_centered_title_uses_cover_title_size() -> None:
    presentation = _presentation()
    title = presentation["slides"][0]["pageElements"][0]
    title["shape"]["placeholder"]["type"] = "CENTERED_TITLE"
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    update = next(
        request["updateTextStyle"]
        for request in plan.requests
        if request.get("updateTextStyle", {}).get("objectId") == "title-1"
    )
    assert update["style"]["fontSize"] == {
        "magnitude": CURRENT_BRAND_MANIFEST.slides.deck_title_size_pt,
        "unit": "PT",
    }


def test_native_split_terminal_newline_and_omitted_zero_index_are_supported() -> None:
    presentation = _presentation()
    body = presentation["slides"][0]["pageElements"][1]
    _use_native_split_terminal_newline(body)
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    assert [block.text for block in parsed.document.blocks] == [
        "Quarterly review",
        "Moreover, results improved",
    ]
    update = next(
        request["updateTextStyle"]
        for request in plan.requests
        if request.get("updateTextStyle", {}).get("objectId") == "body-1"
    )
    assert update["textRange"] == {
        "type": "FIXED_RANGE",
        "startIndex": 0,
        "endIndex": len("Moreover, results improved"),
    }
    branded = deepcopy(presentation)
    _apply_brand_requests(branded, plan.requests)
    assert presentation_fingerprint(branded, "Review").brand_sha256 == plan.expected_brand_sha256


def test_existing_logo_remains_idempotent_when_an_earlier_corner_becomes_free() -> None:
    presentation = _presentation()
    presentation["slides"][0]["pageElements"].append(
        _box("top-right-occupant", left=590, top=50, width=100, height=80)
    )
    first = _format_plan(
        presentation,
        parsed=parse_google_presentation(presentation, title="Review"),
    )
    branded = deepcopy(presentation)
    _apply_brand_requests(branded, first.requests)
    branded["slides"][0]["pageElements"] = [
        element
        for element in branded["slides"][0]["pageElements"]
        if element.get("objectId") != "top-right-occupant"
    ]

    second = _format_plan(
        branded,
        parsed=parse_google_presentation(branded, title="Review"),
    )

    assert not any("createImage" in request for request in second.requests)
    assert "slide_logo_id_collision" not in {finding.code for finding in second.findings}
    assert presentation_fingerprint(branded, "Review").brand_sha256 == second.expected_brand_sha256


def test_inherited_page_fill_does_not_trust_stale_child_solid_fill() -> None:
    presentation = _presentation()
    presentation["slides"][0]["slideProperties"] = {"layoutObjectId": "layout-1"}
    presentation["slides"][0]["pageProperties"] = {
        "pageBackgroundFill": {
            "propertyState": "INHERIT",
            "solidFill": {
                "color": {"rgbColor": {"red": 1, "green": 1, "blue": 1}},
                "alpha": 1,
            },
        }
    }
    presentation["layouts"] = [
        {
            "objectId": "layout-1",
            "layoutProperties": {},
            "pageProperties": {
                "pageBackgroundFill": {
                    "propertyState": "RENDERED",
                    "solidFill": {"color": {"rgbColor": {}}, "alpha": 1},
                }
            },
        }
    ]
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    assert not any("updateTextStyle" in request for request in plan.requests)
    assert not any("createImage" in request for request in plan.requests)
    assert "slide_text_contrast_unresolved" in {finding.code for finding in plan.findings}


def test_rgb_missing_zero_channels_is_canonicalized() -> None:
    presentation = _presentation()
    style = presentation["slides"][0]["pageElements"][1]["shape"]["text"]["textElements"][1][
        "textRun"
    ]["style"]
    style["foregroundColor"] = {"opaqueColor": {"rgbColor": {"blue": 1}}}
    parsed = parse_google_presentation(presentation, title="Review")

    plan = _format_plan(presentation, parsed=parsed)

    assert any(
        request.get("updateTextStyle", {}).get("objectId") == "body-1" for request in plan.requests
    )


def _format_plan(presentation: dict[str, Any], *, parsed: Any) -> Any:
    return build_slides_mutation_plan(
        presentation,
        parsed=parsed,
        transformed=parsed.document,
        mode=TransformMode.FORMAT,
        logo_uri=CURRENT_BRAND_MANIFEST.assets.logo_uri,
    )


def _expected_top_right_logo(presentation: dict[str, Any]) -> _LogoGeometry:
    page_width = presentation["pageSize"]["width"]["magnitude"]
    page_height = presentation["pageSize"]["height"]["magnitude"]
    slides = CURRENT_BRAND_MANIFEST.slides
    header = CURRENT_BRAND_MANIFEST.document.header
    asset_width = CURRENT_BRAND_MANIFEST.assets.logo_pixel_width
    asset_height = CURRENT_BRAND_MANIFEST.assets.logo_pixel_height
    assert header is not None
    assert asset_width is not None
    assert asset_height is not None
    scale = min(header.logo_width_pt / asset_width, header.logo_height_pt / asset_height)
    width = asset_width * scale
    height = asset_height * scale
    inset_x = slides.safe_inset * page_width / slides.canvas_width
    inset_y = slides.safe_inset * page_height / slides.canvas_height
    return _LogoGeometry(page_width - inset_x - width, inset_y, width, height)


def _presentation() -> dict[str, Any]:
    return {
        "presentationId": "deck-1",
        "revisionId": "revision-1",
        "pageSize": {
            "width": {"magnitude": 720, "unit": "PT"},
            "height": {"magnitude": 405, "unit": "PT"},
        },
        "slides": [
            {
                "objectId": "slide-1",
                "pageElements": [
                    _text_shape(
                        "title-1",
                        "Quarterly review",
                        placeholder_type="TITLE",
                        left=24,
                        top=20,
                        width=400,
                    ),
                    _text_shape(
                        "body-1",
                        "Moreover, results improved",
                        placeholder_type="BODY",
                        left=24,
                        top=140,
                        width=500,
                    ),
                ],
            }
        ],
    }


def _presentation_with_theme_background(theme_type: str) -> dict[str, Any]:
    presentation = _presentation()
    presentation["slides"][0]["slideProperties"] = {"layoutObjectId": "layout-1"}
    presentation["layouts"] = [
        {
            "objectId": "layout-1",
            "layoutProperties": {"masterObjectId": "master-1"},
            "pageProperties": {"pageBackgroundFill": {"propertyState": "INHERIT"}},
            "pageElements": [],
        }
    ]
    presentation["masters"] = [
        {
            "objectId": "master-1",
            "pageProperties": {
                "pageBackgroundFill": {
                    "solidFill": {"alpha": 1, "color": {"themeColor": theme_type}}
                },
                "colorScheme": {
                    "colors": [
                        {"type": "DARK1", "color": {}},
                        {
                            "type": "LIGHT1",
                            "color": {"red": 1, "green": 1, "blue": 1},
                        },
                    ]
                },
            },
            "pageElements": [],
        }
    ]
    return presentation


def _text_shape(
    object_id: str,
    text: str,
    *,
    placeholder_type: str | None = None,
    left: float,
    top: float,
    width: float,
) -> dict[str, Any]:
    size = 60 if placeholder_type in {"TITLE", "CENTERED_TITLE"} else 20
    if placeholder_type in {"DATE_AND_TIME", "FOOTER", "HEADER", "SLIDE_NUMBER"}:
        size = 12
    shape: dict[str, Any] = {
        "shapeType": "TEXT_BOX",
        "shapeProperties": {
            "autofit": {"autofitType": "NONE"},
            "shapeBackgroundFill": {"propertyState": "NOT_RENDERED"},
        },
        "text": {
            "textElements": [
                {
                    "startIndex": 0,
                    "endIndex": len(text) + 1,
                    "paragraphMarker": {"style": {}},
                },
                {
                    "startIndex": 0,
                    "endIndex": len(text) + 1,
                    "textRun": {
                        "content": f"{text}\n",
                        "style": {
                            "weightedFontFamily": {
                                "fontFamily": CURRENT_BRAND_MANIFEST.document.font_family
                            },
                            "fontSize": {"magnitude": size, "unit": "PT"},
                            "foregroundColor": {
                                "opaqueColor": {"rgbColor": {"red": 0.1, "green": 0.1, "blue": 0.1}}
                            },
                        },
                    },
                },
            ]
        },
    }
    if placeholder_type is not None:
        shape["placeholder"] = {"type": placeholder_type}
    return {
        **_box(object_id, left=left, top=top, width=width, height=60),
        "shape": shape,
    }


def _use_native_split_terminal_newline(element: dict[str, Any]) -> None:
    text_elements = element["shape"]["text"]["textElements"]
    visible = text_elements[1]
    run = visible["textRun"]
    content = run["content"]
    assert content.endswith("\n")
    run["content"] = content[:-1]
    visible.pop("startIndex")
    visible["endIndex"] -= 1
    text_elements.append(
        {
            "startIndex": visible["endIndex"],
            "endIndex": visible["endIndex"] + 1,
            "textRun": {"content": "\n", "style": deepcopy(run["style"])},
        }
    )


def _rich_bulleted_shape() -> dict[str, Any]:
    shape = _text_shape("rich-1", "Unused", left=24, top=230, width=450)
    shape["shape"]["text"]["textElements"] = [
        {
            "startIndex": 0,
            "endIndex": 0,
            "paragraphMarker": {"bullet": {"listId": "list-1"}, "style": {}},
        },
        {
            "startIndex": 0,
            "endIndex": 5,
            "textRun": {"content": "Bold ", "style": {"bold": True}},
        },
        {
            "startIndex": 5,
            "endIndex": 10,
            "textRun": {
                "content": "link\n",
                "style": {
                    "italic": True,
                    "link": {"url": "https://sarj.ai"},
                },
            },
        },
    ]
    return shape


def _placeholder_style_table(
    level_zero_style: dict[str, Any],
    *,
    style_levels: int = 8,
) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    for level in range(style_levels):
        bullet: dict[str, Any] = {"listId": "bodyPlaceholderListEntity", "glyph": " "}
        if level:
            bullet["nestingLevel"] = level
        elements.extend(
            (
                {
                    "startIndex": level,
                    "endIndex": level + 1,
                    "paragraphMarker": {"bullet": bullet, "style": {}},
                },
                {
                    "startIndex": level,
                    "endIndex": level + 1,
                    "textRun": {
                        "content": "\n",
                        "style": level_zero_style if level == 0 else {},
                    },
                },
            )
        )
    return elements


def _presentation_with_canonical_inherited_title(
    *,
    master_level_zero_style: dict[str, Any],
    style_levels: int = 8,
) -> dict[str, Any]:
    presentation = _presentation()
    title = presentation["slides"][0]["pageElements"][0]
    title["shape"]["placeholder"]["parentObjectId"] = "layout-title"
    title["shape"]["text"]["textElements"][1]["textRun"]["style"] = {}
    presentation["slides"][0]["slideProperties"] = {"layoutObjectId": "layout-1"}
    layout_title = _text_shape(
        "layout-title",
        "Layout",
        placeholder_type="TITLE",
        left=24,
        top=20,
        width=400,
    )
    layout_title["shape"]["placeholder"]["parentObjectId"] = "master-title"
    layout_title["shape"]["text"]["textElements"] = _placeholder_style_table(
        {"fontSize": {"magnitude": 60, "unit": "PT"}},
        style_levels=style_levels,
    )
    master_title = _text_shape(
        "master-title",
        "Master",
        placeholder_type="TITLE",
        left=24,
        top=20,
        width=400,
    )
    master_title["shape"]["text"]["textElements"] = _placeholder_style_table(
        master_level_zero_style,
        style_levels=style_levels,
    )
    presentation["layouts"] = [
        {
            "objectId": "layout-1",
            "layoutProperties": {"masterObjectId": "master-1"},
            "pageElements": [layout_title],
        }
    ]
    presentation["masters"] = [{"objectId": "master-1", "pageElements": [master_title]}]
    return presentation


def _box(
    object_id: str,
    *,
    left: float,
    top: float,
    width: float,
    height: float,
) -> dict[str, Any]:
    return {
        "objectId": object_id,
        "size": {
            "width": {"magnitude": width, "unit": "PT"},
            "height": {"magnitude": height, "unit": "PT"},
        },
        "transform": {
            "scaleX": 1,
            "scaleY": 1,
            "translateX": left,
            "translateY": top,
            "unit": "PT",
        },
    }


def _apply_brand_requests(
    presentation: dict[str, Any],
    requests: list[dict[str, Any]],
) -> None:
    slides = {slide["objectId"]: slide for slide in presentation["slides"]}
    by_id = {
        element["objectId"]: element
        for slide in slides.values()
        for element in _walk_elements(slide["pageElements"])
    }
    for request in requests:
        if delete_object := request.get("deleteObject"):
            object_id = delete_object["objectId"]
            for slide in slides.values():
                slide["pageElements"] = [
                    element
                    for element in slide["pageElements"]
                    if element.get("objectId") != object_id
                ]
            by_id.pop(object_id, None)
        if update := request.get("updateTextStyle"):
            for text_element in by_id[update["objectId"]]["shape"]["text"]["textElements"]:
                run = text_element.get("textRun")
                if isinstance(run, dict):
                    run["style"].update(update["style"])
        if delete := request.get("deleteText"):
            text_elements = by_id[delete["objectId"]]["shape"]["text"]["textElements"]
            run_element = next(element for element in text_elements if "textRun" in element)
            run = run_element["textRun"]
            start = delete["textRange"]["startIndex"]
            end = delete["textRange"]["endIndex"]
            run["content"] = run["content"][:start] + run["content"][end:]
            run_element["endIndex"] -= end - start
        if insert := request.get("insertText"):
            text_elements = by_id[insert["objectId"]]["shape"]["text"]["textElements"]
            run_element = next(element for element in text_elements if "textRun" in element)
            run = run_element["textRun"]
            index = insert["insertionIndex"]
            run["content"] = run["content"][:index] + insert["text"] + run["content"][index:]
            run_element["endIndex"] += len(insert["text"])
        if create := request.get("createImage"):
            properties = create["elementProperties"]
            slide = slides[properties["pageObjectId"]]
            slide["pageElements"].append(
                {
                    "objectId": create["objectId"],
                    "image": {"sourceUrl": create["url"]},
                    "size": properties["size"],
                    "transform": properties["transform"],
                }
            )


def _walk_elements(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    pending = list(elements)
    while pending:
        element = pending.pop(0)
        result.append(element)
        group = element.get("elementGroup")
        if isinstance(group, dict):
            pending[0:0] = group.get("children", [])
    return result


def _text_run_styles(element: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        text_element["textRun"]["style"]
        for text_element in element["shape"]["text"]["textElements"]
        if "textRun" in text_element
    ]
