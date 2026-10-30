from copy import deepcopy
import json
from typing import Any

import pytest

from app.document_transform import native_footer_flow as footer_flow
from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST


def _footer() -> dict[str, Any]:
    profile = CURRENT_BRAND_MANIFEST.document
    return {
        "footerId": "SECRET",
        "content": [
            {
                "paragraph": {
                    "paragraphStyle": {"alignment": "END", "namedStyleType": "NORMAL_TEXT"},
                    "elements": [
                        {
                            "textRun": {
                                "content": "Open in Wiki\n",
                                "textStyle": {
                                    "weightedFontFamily": {"fontFamily": profile.font_family},
                                    "fontSize": {
                                        "magnitude": profile.caption_size_pt,
                                        "unit": "PT",
                                    },
                                    "link": {"url": "https://private.example/SECRET"},
                                    "bold": False,
                                    "italic": False,
                                },
                            }
                        }
                    ],
                }
            }
        ],
    }


@pytest.mark.parametrize(
    ("field", "check"),
    [
        ("weightedFontFamily", "font_family"),
        ("fontSize", "font_size"),
        ("link", "link"),
        ("bold", "bold"),
        ("italic", "italic"),
        ("alignment", "alignment"),
        ("namedStyleType", "named_style"),
    ],
)
@pytest.mark.parametrize("missing", [False, True])
def test_style_failure_classification_preserves_matching_decisions(
    field: str,
    check: str,
    missing: bool,
) -> None:
    footer = _footer()
    paragraph = footer["content"][0]["paragraph"]
    style = (
        paragraph["paragraphStyle"]
        if field in {"alignment", "namedStyleType"}
        else paragraph["elements"][0]["textRun"]["textStyle"]
    )
    if missing:
        style.pop(field)
    else:
        style[field] = {"fontFamily": "SECRET"} if field == "weightedFontFamily" else "SECRET"
    before = deepcopy(footer)
    failure = footer_flow._footer_shape_failure(
        footer, "https://private.example/SECRET", populated=True, display_text="Open in Wiki"
    )
    expected_match = missing and field in {"bold", "italic"}
    assert (
        footer_flow._footer_matches(
            footer, "https://private.example/SECRET", populated=True, display_text="Open in Wiki"
        )
        is expected_match
    )
    assert failure == (None if expected_match else (check, "missing" if missing else "mismatch"))
    assert "SECRET" not in json.dumps(failure)
    assert footer == before


@pytest.mark.parametrize(
    ("change", "check"),
    [
        ("footer_fields", "footer_fields"),
        ("content_missing", "content"),
        ("content_type", "content"),
        ("paragraph_count", "paragraph_count"),
        ("paragraph_fields", "paragraph_fields"),
        ("paragraph_missing", "paragraph_container"),
        ("runs_missing", "runs"),
        ("runs_empty", "runs"),
        ("run_type", "run_container"),
        ("run_fields", "run_fields"),
        ("text_run_missing", "text_run"),
        ("text_run_fields", "text_run_fields"),
        ("text_content_missing", "text_content"),
        ("caption", "caption"),
    ],
)
def test_container_and_caption_failures_are_classified_without_provider_data(
    change: str, check: str
) -> None:
    footer = _footer()
    paragraph = footer["content"][0]["paragraph"]
    run = paragraph["elements"][0]
    match change:
        case "footer_fields":
            footer["SECRET"] = "SECRET"
        case "content_missing":
            footer.pop("content")
        case "content_type":
            footer["content"] = "SECRET"
        case "paragraph_count":
            footer["content"].append(deepcopy(footer["content"][0]))
        case "paragraph_fields":
            paragraph["SECRET"] = "SECRET"
        case "paragraph_missing":
            footer["content"][0].pop("paragraph")
        case "runs_missing":
            paragraph.pop("elements")
        case "runs_empty":
            paragraph["elements"] = []
        case "run_type":
            paragraph["elements"] = ["SECRET"]
        case "run_fields":
            run["SECRET"] = "SECRET"
        case "text_run_missing":
            run.pop("textRun")
        case "text_run_fields":
            run["textRun"]["SECRET"] = "SECRET"
        case "text_content_missing":
            run["textRun"].pop("content")
        case _:
            run["textRun"]["content"] = "SECRET\n"
    failure = footer_flow._footer_shape_failure(
        footer, "https://private.example/SECRET", populated=True, display_text="Open in Wiki"
    )
    assert not footer_flow._footer_matches(
        footer, "https://private.example/SECRET", populated=True, display_text="Open in Wiki"
    )
    assert failure == (check, "missing" if change.endswith("missing") else "mismatch")
    assert "SECRET" not in json.dumps(failure)


@pytest.mark.parametrize("populated", [False, True])
def test_valid_empty_and_split_run_footers_remain_accepted(populated: bool) -> None:
    footer = _footer()
    runs = footer["content"][0]["paragraph"]["elements"]
    if populated:
        second = deepcopy(runs[0])
        runs[0]["textRun"]["content"] = "Open in "
        second["textRun"]["content"] = "Wiki\n"
        runs.append(second)
    else:
        runs[0]["textRun"]["content"] = "\n"
    assert footer_flow._footer_matches(
        footer, "https://private.example/SECRET", populated=populated, display_text="Open in Wiki"
    )
    assert (
        footer_flow._footer_shape_failure(
            footer,
            "https://private.example/SECRET",
            populated=populated,
            display_text="Open in Wiki",
        )
        is None
    )
