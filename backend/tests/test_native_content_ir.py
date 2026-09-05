from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest

from app.document_transform import brand, google_gateway
from app.document_transform.errors import UnsupportedSourceError
from app.document_transform.ir import parse_google_document, verify_planned_date_conversions
from app.document_transform.native_elements import plan_native_elements


def _document(elements: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "revisionId": "r1",
        "tabs": [
            {
                "tabProperties": {"tabId": "tab1"},
                "documentTab": {
                    "body": {
                        "content": [
                            {"startIndex": 1, "endIndex": 12, "paragraph": {"elements": elements}},
                        ]
                    }
                },
            }
        ],
    }


def _date() -> dict[str, Any]:
    return {
        "dateElement": {
            "dateId": "date1",
            "dateElementProperties": {
                "displayText": "2026-09-04",
                "timestamp": "2026-09-04T00:00:00Z",
                "timeZoneId": "Etc/UTC",
                "locale": "en",
                "dateFormat": "DATE_FORMAT_ISO8601",
                "timeFormat": "TIME_FORMAT_DISABLED",
            },
        }
    }


def _parse(elements: list[dict[str, Any]]) -> Any:
    return parse_google_document(_document(elements), title="Title")


@pytest.mark.parametrize(
    ("chip", "display"),
    [
        (_date(), "2026-09-04"),
        (
            {
                "person": {
                    "personId": "p1",
                    "personProperties": {"name": "Ada", "email": "ada@example.com"},
                }
            },
            "Ada",
        ),
        ({"person": {"personProperties": {"email": "ada@example.com"}}}, "ada@example.com"),
        (
            {
                "richLink": {
                    "richLinkId": "r1",
                    "richLinkProperties": {
                        "title": "Plan",
                        "uri": "https://docs.google.com/document/d/a",
                    },
                }
            },
            "Plan",
        ),
    ],
)
def test_native_content_retains_display_identity_and_order(
    chip: dict[str, Any], display: str
) -> None:
    document = _parse([{"textRun": {"content": "Due "}}, chip, {"textRun": {"content": " now\n"}}])
    block = document.blocks[0]
    assert block.text == f"Due {display} now"
    assert not block.editable
    assert block.font_style_ranges == ()
    assert block.native_content[0].text_offset == 4
    assert block.native_content[0].display_text == display
    assert plan_native_elements(document) == ()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("timestamp", "2026-09-05T00:00:00Z"),
        ("timeZoneId", "America/New_York"),
        ("displayText", "September 4"),
        ("unknownFutureProperty", "changed"),
    ],
)
def test_date_hidden_identity_changes_affect_both_fingerprints(key: str, value: str) -> None:
    original = _date()
    changed = deepcopy(original)
    changed["dateElement"]["dateElementProperties"][key] = value
    before, after = _parse([original]), _parse([changed])
    assert before.semantic_sha256 != after.semantic_sha256
    assert before.text_sha256 != after.text_sha256


def test_same_person_display_with_different_email_is_not_equal() -> None:
    original = {"person": {"personProperties": {"name": "Ada", "email": "ada@example.com"}}}
    changed = deepcopy(original)
    changed["person"]["personProperties"]["email"] = "other@example.com"
    assert _parse([original]).semantic_sha256 != _parse([changed]).semantic_sha256


def test_style_changes_do_not_change_native_semantics_but_suggestions_do() -> None:
    original = _date()
    changed = deepcopy(original)
    changed["dateElement"]["textStyle"] = {"bold": True}
    assert _parse([original]).semantic_sha256 == _parse([changed]).semantic_sha256
    changed["dateElement"]["suggestedDeletionIds"] = ["suggestion1"]
    assert _parse([original]).semantic_sha256 != _parse([changed]).semantic_sha256


@pytest.mark.parametrize(
    "part",
    [{"dateElement": {}}, {"person": None}, {"richLink": {"richLinkProperties": {}}}],
    ids=["missing-date-properties", "null-person", "empty-rich-link"],
)
def test_malformed_chips_fail_closed(part: dict[str, Any]) -> None:
    with pytest.raises(UnsupportedSourceError):
        _parse([part])


def test_planned_exact_iso_date_conversion_has_explicit_equivalence_only() -> None:
    before = _parse([{"textRun": {"content": "2026-09-04\n"}}])
    after = _parse([_date(), {"textRun": {"content": "\n"}}])
    assert before.semantic_sha256 != after.semantic_sha256
    assert not verify_planned_date_conversions(before, after, source_block_hashes={})
    assert not verify_planned_date_conversions(before, after, source_block_hashes={"b0": "stale"})
    assert verify_planned_date_conversions(
        before, after, source_block_hashes={"b0": before.blocks[0].sha256}
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("timestamp", "2026-09-05T00:00:00Z"),
        ("timeZoneId", "America/New_York"),
        ("dateFormat", "DATE_FORMAT_MONTH_DAY_FULL"),
        ("timeFormat", "TIME_FORMAT_HOUR_MINUTE"),
        ("locale", "fr"),
        ("unknownFutureProperty", "x"),
    ],
)
def test_conversion_rejects_hidden_date_substitution(key: str, value: str) -> None:
    before = _parse([{"textRun": {"content": "2026-09-04\n"}}])
    chip = _date()
    chip["dateElement"]["dateElementProperties"][key] = value
    assert not verify_planned_date_conversions(
        before, _parse([chip]), source_block_hashes={"b0": before.blocks[0].sha256}
    )


def test_conversion_rejects_suggestions_and_duplicate_chips() -> None:
    before = _parse([{"textRun": {"content": "2026-09-04\n"}}])
    planned = {"b0": before.blocks[0].sha256}
    chip = _date()
    chip["dateElement"]["suggestedInsertionIds"] = ["s1"]
    assert not verify_planned_date_conversions(before, _parse([chip]), source_block_hashes=planned)
    assert not verify_planned_date_conversions(
        before, _parse([_date(), _date()]), source_block_hashes=planned
    )


def test_conversion_rejects_changes_to_unplanned_content_and_tabs() -> None:
    before = _parse([{"textRun": {"content": "2026-09-04\n"}}])
    after = _parse([_date()])
    untouched = replace(before.blocks[0], id="b1", text="Keep this sentence.")
    before = replace(before, blocks=(*before.blocks, untouched))
    after = replace(after, blocks=(*after.blocks, untouched))
    planned = {"b0": before.blocks[0].sha256}
    assert verify_planned_date_conversions(before, after, source_block_hashes=planned)
    altered = replace(after, blocks=(after.blocks[0], replace(untouched, text="Changed.")))
    assert not verify_planned_date_conversions(before, altered, source_block_hashes=planned)
    assert not verify_planned_date_conversions(
        before, replace(after, tab_ids=()), source_block_hashes=planned
    )
    assert not verify_planned_date_conversions(
        before, after, source_block_hashes={**planned, "missing": "hash"}
    )


def test_rich_link_target_and_native_order_cannot_change_silently() -> None:
    link = {
        "richLink": {
            "richLinkProperties": {"title": "Plan", "uri": "https://docs.google.com/document/d/a"}
        }
    }
    changed = deepcopy(link)
    changed["richLink"]["richLinkProperties"]["uri"] = "https://docs.google.com/document/d/b"
    assert _parse([link]).semantic_sha256 != _parse([changed]).semantic_sha256
    assert _parse([link, _date()]).semantic_sha256 != _parse([_date(), link]).semantic_sha256


def test_typography_fallback_cannot_accept_same_label_different_native_identity() -> None:
    chip = _date()
    original = _parse([chip])
    chip["dateElement"]["dateElementProperties"]["timestamp"] = "2026-09-05T00:00:00Z"
    assert not google_gateway._typography_content_preserved(
        _parse([chip]), original, verify_brand=True
    )


def test_relocated_native_content_retains_provider_width_and_later_positions() -> None:
    document = _parse([_date()])
    block = document.blocks[0]
    assert block.source_range is not None
    source = block.source_range
    # A chip is one provider unit even when its visible label is ten characters.
    chip = replace(block, source_range=replace(source, end_index=source.start_index + 1))
    following = replace(
        block,
        id="b1",
        text="After",
        native_content=(),
        source_range=replace(source, start_index=3, end_index=8),
        editable=True,
    )
    document = replace(document, blocks=(chip, following))
    relocated = brand._post_edit_locations(document, document)
    assert relocated.blocks[0].native_content == chip.native_content
    assert relocated.blocks[0].source_range == chip.source_range
    assert relocated.blocks[1].source_range == following.source_range
    changed = replace(document, blocks=(replace(chip, text="Invented"), following))
    with pytest.raises(UnsupportedSourceError):
        brand._post_edit_locations(document, changed)
