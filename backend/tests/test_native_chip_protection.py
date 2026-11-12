import uuid

from pydantic import JsonValue
import pytest

from app.document_transform.ir import utf16_length
from app.document_transform.native_chip_flow import plan_native_chips


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
