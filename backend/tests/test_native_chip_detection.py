from dataclasses import replace

import pytest

from app.document_transform.ir import utf16_length
from app.document_transform.native_chip_detection import (
    NativeChipContext,
    detect_address_spans,
    detect_date_spans,
    native_chip_context,
)


_NYC = NativeChipContext(city="New York City", time_zone="America/New_York", year=2026)


def test_51_two_individual_address_spans_retain_and_and_exhibition() -> None:
    text = "533 W. 19th St. and 537 W. 20th St. Exhibition"
    spans = detect_address_spans(text, context=_NYC)
    assert [span.text for span in spans] == ["533 W. 19th St.", "537 W. 20th St."]
    assert text[spans[0].end : spans[1].start] == " and "
    assert text[spans[1].end :] == " Exhibition"
    assert all(span.skip_reason is None for span in spans)


def test_52_conflicting_or_missing_city_context_does_not_guess_a_location() -> None:
    conflict = native_chip_context("New York City 2026", "NYC and London galleries")
    assert conflict.conflicting_city
    assert (
        detect_address_spans("533 W. 19th St.", context=conflict)[0].skip_reason
        == "conflicting_city_context"
    )
    assert (
        detect_address_spans("10 Main Street", context=NativeChipContext())[0].skip_reason
        == "missing_city_context"
    )


@pytest.mark.parametrize("prefix", ["- Visit ", "| Place | ", "😀 Visit "])
def test_53_inline_repeated_addresses_have_independent_utf16_spans(prefix: str) -> None:
    address = "533 W. 19th St."
    spans = detect_address_spans(prefix + address + " and " + address + " |", context=_NYC)
    assert len(spans) == 2
    assert spans[0].start == utf16_length(prefix)
    assert spans[0].end < spans[1].start
    assert all(span.text == address for span in spans)


def test_54_explicit_date_and_both_time_endpoints_keep_new_york_meaning() -> None:
    text = "Saturday, November 14, 2026, 7:30–9:00 pm"
    spans = detect_date_spans(text, context=_NYC)
    assert len(spans) == 2
    assert [span.date_value.timestamp for span in spans if span.date_value] == [
        "2026-11-15T00:30:00Z",
        "2026-11-15T02:00:00Z",
    ]
    assert all(
        span.date_value
        and span.date_value.time_zone == "America/New_York"
        and span.date_value.has_time
        for span in spans
    )
    assert text[spans[0].end : spans[1].start] == "–"
    assert text[: spans[0].start] == "Saturday, "


def test_55_explicit_overnight_range_retains_the_next_local_day() -> None:
    spans = detect_date_spans("November 14, 2026, 11 pm–1 am", context=_NYC)
    assert [span.date_value.timestamp for span in spans if span.date_value] == [
        "2026-11-15T04:00:00Z",
        "2026-11-15T06:00:00Z",
    ]
    ambiguous = detect_date_spans("November 14, 2026, 11–1 am", context=_NYC)
    assert ambiguous[0].date_value is None
    assert ambiguous[0].skip_reason == "ambiguous_range_meridiem"


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("March 8, 2026, 2:30 am", "nonexistent_local_time"),
        ("November 1, 2026, 1:30 am", "ambiguous_local_time"),
    ],
)
def test_56_dst_gap_and_fold_are_not_silently_assigned_an_instant(text: str, reason: str) -> None:
    (span,) = detect_date_spans(text, context=_NYC)
    assert span.skip_reason == reason
    assert span.date_value is None


def test_56_numeric_dates_require_unambiguous_values_or_explicit_locale() -> None:
    ambiguous = detect_date_spans("04/09/2026", context=_NYC)
    assert ambiguous[0].skip_reason == "ambiguous_or_invalid_date"
    us = detect_date_spans("04/09/2026", context=replace(_NYC, locale="en-US"))
    gb = detect_date_spans("04/09/2026", context=replace(_NYC, locale="en-GB"))
    assert us[0].date_value
    assert us[0].date_value.timestamp.startswith("2026-04-09")
    assert gb[0].date_value
    assert gb[0].date_value.timestamp.startswith("2026-09-04")


@pytest.mark.parametrize(
    "text", ["7 pm", "Opening hours: Tue–Sat 10 am–6 pm", "Every Saturday 7:30–9:00 pm"]
)
def test_57_bare_times_and_recurring_hours_do_not_invent_dates(text: str) -> None:
    assert detect_date_spans(text, context=_NYC) == ()


def test_date_only_meaning_has_no_invented_time_and_keeps_the_local_date() -> None:
    (span,) = detect_date_spans("Due November 14, 2026.", context=_NYC)
    assert span.date_value
    assert not span.date_value.has_time
    assert span.date_value.timestamp == "2026-11-14T05:00:00Z"
    assert span.date_value.properties()["timeFormat"] == "TIME_FORMAT_DISABLED"
    assert span.date_value.properties()["timestamp"] == "2026-11-14T00:00:00Z"
    assert "timeZoneId" not in span.date_value.properties()


def test_a_year_can_only_come_from_explicit_context() -> None:
    (anchored,) = detect_date_spans("November 14", context=_NYC)
    assert anchored.date_value
    (unanchored,) = detect_date_spans("November 14", context=NativeChipContext())
    assert unanchored.skip_reason == "ambiguous_or_invalid_date"


def test_weekday_date_conflict_is_preserved_for_review() -> None:
    (span,) = detect_date_spans("Sunday, November 14, 2026, 7 pm", context=_NYC)
    assert span.skip_reason == "weekday_date_conflict"
    assert span.date_value is None


@pytest.mark.parametrize(
    ("zones", "text", "reason"),
    [
        pytest.param(
            "America/New_York and Europe/London",
            "November 14, 2026, 7 pm",
            "conflicting_time_zone_context",
            id="conflicting-zones",
        ),
        pytest.param(
            "America/Invalid",
            "November 14, 2026",
            "unsupported_time_zone",
            id="unsupported-zone-date-only",
        ),
    ],
)
def test_uncertain_explicit_time_zone_preserves_original_text(
    zones: str, text: str, reason: str
) -> None:
    context = native_chip_context("NYC 2026", zones)
    (span,) = detect_date_spans(text, context=context)
    assert span.date_value is None
    assert span.skip_reason == reason


def test_unique_explicit_document_year_anchors_dates_but_multiple_years_do_not() -> None:
    context = native_chip_context("NYC weekend", "Trip year: 2026")
    (span,) = detect_date_spans("November 14", context=context)
    assert span.date_value is not None
    assert span.date_value.timestamp == "2026-11-14T05:00:00Z"
    conflict = native_chip_context("NYC weekend", "Compare 2025 and 2026")
    assert detect_date_spans("November 14", context=conflict)[0].date_value is None


@pytest.mark.parametrize(
    "text",
    ["November 15, 2026, 7 pm.", "November 14, 2026, 11 pm–1 am.", "November 15, 2026, 7 p.m."],
)
def test_time_chips_preserve_terminal_sentence_punctuation(text: str) -> None:
    spans = detect_date_spans(text, context=_NYC)
    assert spans[-1].end == utf16_length(text) - 1
    assert text[spans[-1].end :] == "."


def test_explicit_single_date_in_title_anchors_inline_times_without_using_today() -> None:
    context = native_chip_context("NYC • Saturday, November 14, 2026", "Dinner at 7:30–9 pm.")
    spans = detect_date_spans("Dinner at 7:30–9 pm.", context=context)
    assert [span.date_value.timestamp for span in spans if span.date_value] == [
        "2026-11-15T00:30:00Z",
        "2026-11-15T02:00:00Z",
    ]
    assert all(span.date_value and span.date_value.has_time for span in spans)


@pytest.mark.parametrize(
    "title",
    [
        "NYC weekend 2026",
        "NYC November 14, 2026 and November 15, 2026",
        "NYC capability canary 2026-11-12",
        "NYC November 14–15, 2026",
    ],
)
def test_missing_or_multiple_title_dates_leave_bare_times_unanchored(title: str) -> None:
    context = native_chip_context(title, "Dinner at 7 pm.")
    assert detect_date_spans("Dinner at 7 pm.", context=context) == ()


def test_recurring_hours_do_not_inherit_an_explicit_title_date() -> None:
    context = native_chip_context("NYC • November 14, 2026", "Opening hours: Tue–Sat 10 am–6 pm.")
    assert detect_date_spans("Opening hours: Tue–Sat 10 am–6 pm.", context=context) == ()
