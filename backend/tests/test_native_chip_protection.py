import uuid

from pydantic import JsonValue
import pytest

from app.document_transform.ir import utf16_length
from app.document_transform.native_chip_flow import plan_native_chips
from app.document_transform.native_quote_spans import (
    InsideCode,
    OutsideCode,
    advance_code,
    native_inline_protected_ranges,
)


def _document(texts: tuple[str, ...]) -> dict[str, JsonValue]:
    content: list[JsonValue] = []
    start = 1
    for text in texts:
        content_text = text + "\n"
        end = start + utf16_length(content_text)
        content.append(
            {
                "startIndex": start,
                "endIndex": end,
                "paragraph": {
                    "paragraphStyle": {},
                    "elements": [
                        {
                            "startIndex": start,
                            "endIndex": end,
                            "textRun": {"content": content_text, "textStyle": {}},
                        }
                    ],
                },
            }
        )
        start = end
    return {
        "documentId": "quote-canary",
        "revisionId": "r1",
        "title": "NYC 2026",
        "tabs": [
            {"tabProperties": {"tabId": "t.0"}, "documentTab": {"body": {"content": content}}}
        ],
    }


@pytest.mark.parametrize(
    "marks",
    [
        pytest.param(('"', '"'), id="straight-double"),
        pytest.param(("“", "”"), id="curly-double"),
        pytest.param(("'", "'"), id="straight-single"),
        pytest.param(("‘", "’"), id="curly-single"),
        pytest.param(("`", "`"), id="inline-code"),
    ],
)
def test_inline_protected_date_and_unicode_preserve_the_eligible_neighbor(
    marks: tuple[str, str],
) -> None:
    left, right = marks
    text = f"😀 Keep {left}November 14, 2026{right}; visit November 15, 2026."
    plan = plan_native_chips(_document((text,)), operation_id=uuid.UUID(int=3))
    assert plan is not None
    assert [span.original_text for span in plan.spans] == ["November 15, 2026"]
    assert plan.spans[0].start == 1 + utf16_length(text[: text.index("November 15")])
    assert len(plan.skipped) == 1
    assert plan.skipped[0].reason == ("protected_code" if left == "`" else "protected_quotation")


def test_quote_continued_across_native_paragraphs_is_preserved() -> None:
    plan = plan_native_chips(
        _document(
            (
                "“The exhibition opens",
                "on November 14, 2026.”",
                "Visit November 15, 2026.",
            )
        ),
        operation_id=uuid.UUID(int=4),
    )
    assert plan is not None
    assert [span.original_text for span in plan.spans] == ["November 15, 2026"]
    assert [item.reason for item in plan.skipped] == ["protected_quotation"]


@pytest.mark.parametrize(
    ("opening", "reason"), [("“", "unclosed_quotation"), ("`", "unclosed_code")]
)
def test_many_unmatched_openers_and_date_candidates_are_preserved(
    opening: str, reason: str
) -> None:
    text = "😀 " + opening * 16_000 + " November 14, 2026;" * 100
    plan = plan_native_chips(_document((text,)), operation_id=uuid.UUID(int=5))
    assert plan is not None
    assert not plan.spans
    assert len(plan.skipped) == 100
    assert all(item.reason == reason for item in plan.skipped)


def test_code_delimiters_require_an_exact_close_and_preserve_unfinished_text() -> None:
    opened = advance_code(OutsideCode(), "``", 2, 4)
    ranges = opened.ranges
    assert opened.state == InsideCode("``", 2)
    assert not ranges
    still_open = advance_code(opened.state, "`", 8, 9)
    ranges = still_open.ranges
    assert still_open == opened
    assert not ranges
    closed = advance_code(still_open.state, "``", 12, 14)
    ranges = closed.ranges
    assert isinstance(closed.state, OutsideCode)
    assert [(item.start, item.end, item.reason) for item in ranges] == [(2, 14, "protected_code")]
    assert [
        (item.start, item.end, item.reason) for item in native_inline_protected_ranges("😀 `` x `")
    ] == [(3, 9, "unclosed_code")]


@pytest.mark.parametrize(
    ("mark", "start", "end"), [("", 0, 1), ("quote", 0, 5), ("`", -1, 0), ("`", 2, 2)]
)
def test_invalid_code_events_are_refused(mark: str, start: int, end: int) -> None:
    with pytest.raises(ValueError, match="Invalid inline code token"):
        advance_code(OutsideCode(), mark, start, end)
