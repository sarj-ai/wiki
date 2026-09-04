from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

import pytest

from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.errors import UnsupportedSourceError
from app.document_transform.schemas import TransformMode
from app.document_transform.slides_brand import (
    build_slides_mutation_plan,
    presentation_fingerprint,
)
from app.document_transform.slides_ir import (
    parse_google_presentation,
    sarj_logo_object_id,
)


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
    target = _text_shape("unsupported-1", "Visible", left=24, top=230, width=200)
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
    logo_id = sarj_logo_object_id("slide-1")
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
    if bad_geometry == "missing":
        target.pop("transform")
    elif bad_geometry == "zero":
        target["size"]["width"]["magnitude"] = 0
    elif bad_geometry == "nan":
        target["transform"]["translateX"] = math.nan
    else:
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

    assert any(
        request.get("updateTextStyle", {}).get("objectId") == "grouped-text"
        for request in plan.requests
    )


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
    assert (transform["translateX"], transform["translateY"]) not in {(604, 64), (604, 317.143)}


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


def _format_plan(presentation: dict[str, Any], *, parsed: Any) -> Any:
    return build_slides_mutation_plan(
        presentation,
        parsed=parsed,
        transformed=parsed.document,
        mode=TransformMode.FORMAT,
        logo_uri=CURRENT_BRAND_MANIFEST.assets.logo_uri,
    )


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
                        left=24,
                        top=140,
                        width=500,
                    ),
                ],
            }
        ],
    }


def _text_shape(
    object_id: str,
    text: str,
    *,
    placeholder_type: str | None = None,
    left: float,
    top: float,
    width: float,
) -> dict[str, Any]:
    shape: dict[str, Any] = {
        "shapeType": "TEXT_BOX",
        "text": {
            "textElements": [
                {"startIndex": 0, "endIndex": 0, "paragraphMarker": {"style": {}}},
                {
                    "startIndex": 0,
                    "endIndex": len(text) + 1,
                    "textRun": {"content": f"{text}\n", "style": {}},
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
        if update := request.get("updateTextStyle"):
            for text_element in by_id[update["objectId"]]["shape"]["text"]["textElements"]:
                run = text_element.get("textRun")
                if isinstance(run, dict):
                    run["style"].update(update["style"])
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
