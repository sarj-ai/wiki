from copy import deepcopy
from typing import Any

import pytest

from app.document_transform import native_footer_flow as flow
from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.errors import InPlaceNeedsReviewError
from app.identifiers import DocsRevisionId, DocsTabId


def _tab() -> dict[str, Any]:
    return {
        "namedStyles": {
            "styles": [
                {
                    "namedStyleType": "NORMAL_TEXT",
                    "textStyle": {
                        "weightedFontFamily": {
                            "fontFamily": CURRENT_BRAND_MANIFEST.document.font_family
                        }
                    },
                }
            ]
        }
    }


def _footer() -> dict[str, Any]:
    return {
        "content": [
            {
                "paragraph": {
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT", "alignment": "END"},
                    "elements": [
                        {
                            "textRun": {
                                "content": "caption\n",
                                "textStyle": {
                                    "fontSize": {
                                        "magnitude": CURRENT_BRAND_MANIFEST.document.caption_size_pt,
                                        "unit": "PT",
                                    },
                                    "link": {"url": "https://example.com"},
                                },
                            }
                        }
                    ],
                }
            }
        ]
    }


@pytest.mark.parametrize(
    "value",
    [None, {}, [], {"fontFamily": ""}, {"fontFamily": "Arial"}],
    ids=["null", "empty", "list", "blank", "wrong"],
)
def test_explicit_invalid_family_never_inherits(value: object) -> None:
    footer = _footer()
    footer["content"][0]["paragraph"]["elements"][0]["textRun"]["textStyle"][
        "weightedFontFamily"
    ] = value
    family = flow._normal_text_family(_tab())
    assert not flow._footer_matches(
        footer,
        "https://example.com",
        populated=True,
        display_text="caption",
        inherited_family=family,
    )
    assert (
        flow._footer_shape_failure(
            footer,
            "https://example.com",
            populated=True,
            display_text="caption",
            inherited_family=family,
        )
        is not None
    )


@pytest.mark.parametrize("change", ["missing", "duplicate", "wrong", "malformed", "no_family"])
def test_parent_requires_unique_explicit_normal_text(change: str) -> None:
    tab = _tab()
    styles = tab["namedStyles"]["styles"]
    match change:
        case "missing":
            styles.clear()
        case "duplicate":
            styles.append(deepcopy(styles[0]))
        case "wrong":
            styles[0]["namedStyleType"] = "TITLE"
        case "malformed":
            styles.append(None)
        case _:
            styles[0]["textStyle"].clear()
    assert flow._normal_text_family(tab) is None


@pytest.mark.parametrize("missing", [None, "fontSize", "link", "namedStyleType"])
def test_inherited_family_does_not_relax_other_checks(missing: str | None) -> None:
    footer = _footer()
    paragraph = footer["content"][0]["paragraph"]
    if missing == "namedStyleType":
        paragraph["paragraphStyle"].pop(missing)
    elif missing is not None:
        paragraph["elements"][0]["textRun"]["textStyle"].pop(missing)
    before = deepcopy(footer)
    family = flow._normal_text_family(_tab())
    assert flow._footer_matches(
        footer,
        "https://example.com",
        populated=True,
        display_text="caption",
        inherited_family=family,
    ) is (missing is None)
    assert (
        flow._footer_shape_failure(
            footer,
            "https://example.com",
            populated=True,
            display_text="caption",
            inherited_family=family,
        )
        is None
    ) is (missing is None)
    assert footer == before


@pytest.mark.parametrize("change", ["none", "other_tab", "parent_mutation"])
def test_projection_uses_same_tab_parent_and_preserves_invariant(change: str) -> None:
    tab = _tab()
    tab["documentStyle"] = {}
    other = _tab()
    if change == "other_tab":
        tab.pop("namedStyles")
    document = {
        "tabs": [
            {"tabProperties": {"tabId": "t1"}, "documentTab": tab},
            {"tabProperties": {"tabId": "t2"}, "documentTab": other},
        ]
    }
    checkpoint = flow.NativeFooterCheckpoint(
        tab_id=DocsTabId("t1"),
        url="https://example.com",
        revision_id=DocsRevisionId("r1"),
        create_marker="create",
        populate_marker="populate",
        marker_range={},
        invariant_sha256=flow.footer_invariant_sha256(document),
    )
    footer = _footer()
    footer["content"][0]["paragraph"]["elements"][0]["textRun"]["content"] = "https://example.com\n"
    tab["documentStyle"]["defaultFooterId"] = "f1"
    tab["footers"] = {"f1": footer}
    if change == "parent_mutation":
        tab["namedStyles"]["styles"][0]["textStyle"]["bold"] = True
    before = deepcopy(document)
    if change == "none":
        projection = flow.verified_footer_projection(document, checkpoint, populated=True)
        assert flow.footer_invariant_sha256(projection.document) == checkpoint.invariant_sha256
    else:
        with pytest.raises(InPlaceNeedsReviewError):
            flow.verified_footer_projection(document, checkpoint, populated=True)
    assert document == before
