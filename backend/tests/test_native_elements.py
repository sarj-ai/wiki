from dataclasses import replace

import pytest

from app.document_transform.ir import BlockKind, DocumentIR, SourceRange, TextBlock, utf16_length
from app.document_transform.native_elements import (
    NativeElementKind,
    NativeElementPolicy,
    plan_native_elements,
)
from app.identifiers import DocsSegmentId, DocsTabId


def _document(*texts: str) -> DocumentIR:
    blocks = []
    start = 1
    for index, text in enumerate(texts):
        end = start + utf16_length(text)
        blocks.append(
            TextBlock(
                str(index),
                BlockKind.PARAGRAPH,
                text,
                source_range=SourceRange(start, end, DocsTabId("t1")),
            )
        )
        start = end + 1
    return DocumentIR("Test", tuple(blocks), "google_docs")


def test_date_request_preserves_iso_display_without_inventing_time() -> None:
    (proposal,) = plan_native_elements(_document("2026-09-04"))
    assert proposal.kind is NativeElementKind.DATE
    deletion, insertion = proposal.requests()
    assert deletion["deleteContentRange"]["range"] == {
        "startIndex": 1,
        "endIndex": 11,
        "tabId": "t1",
    }
    assert insertion["insertDate"]["dateElementProperties"] == {
        "timestamp": "2026-09-04T00:00:00Z",
        "timeZoneId": "Etc/UTC",
        "locale": "en",
        "dateFormat": "DATE_FORMAT_ISO8601",
        "timeFormat": "TIME_FORMAT_DISABLED",
    }


@pytest.mark.parametrize(
    ("text", "timestamp"),
    [
        ("September 4, 2026", "2026-09-04T00:00:00Z"),
        ("Sep 4 2026", "2026-09-04T00:00:00Z"),
        ("4 September 2026", "2026-09-04T00:00:00Z"),
    ],
)
def test_unambiguous_written_dates_use_native_date_elements(text: str, timestamp: str) -> None:
    (proposal,) = plan_native_elements(_document(text))
    assert proposal.original_text == text
    assert proposal.requests()[1]["insertDate"]["dateElementProperties"]["timestamp"] == timestamp


@pytest.mark.parametrize(
    "text",
    [
        "2026-02-30",
        "09/04/2026",
        "04/09/2026",
        "September 31, 2026",
        "tomorrow",
        "Due 2026-09-04",
        " 2026-09-04",
        "2026-09-04T12:00:00Z",
        "0000-01-01",
        "😀 2026-09-04",
    ],
)
def test_ambiguous_or_mixed_content_is_untouched(text: str) -> None:
    assert plan_native_elements(_document(text)) == ()


def test_person_and_resource_require_exact_verified_allowlists() -> None:
    uri = "https://docs.google.com/document/d/example/edit"
    document = _document("owner@example.com", uri)
    assert plan_native_elements(document) == ()
    policy = NativeElementPolicy(frozenset({uri}), frozenset({"owner@example.com"}))
    resource, person = plan_native_elements(document, policy=policy)
    assert resource.requests()[1]["insertRichLink"]["richLinkProperties"] == {"uri": uri}
    assert person.requests()[1]["insertPerson"]["personProperties"] == {
        "email": "owner@example.com"
    }


@pytest.mark.parametrize(
    "uri",
    [
        "http://docs.google.com/document/d/example",
        "https://docs.google.com.evil.test/document/d/example",
        "https://user@docs.google.com/document/d/example",
        "https://example.com/file/d/example",
        "https://drive.google.com/other",
        "https://[invalid/document/d/example",
        "https://docs.google.com/document/d/example\n",
    ],
)
def test_unsafe_resource_even_if_allowlisted_is_rejected(uri: str) -> None:
    assert plan_native_elements(_document(uri), policy=NativeElementPolicy(frozenset({uri}))) == ()


def test_protected_code_quote_header_and_missing_ranges_are_untouched() -> None:
    document = _document("2026-09-04")
    block = document.blocks[0]
    assert block.source_range is not None
    variants = [
        replace(block, editable=False),
        replace(block, kind=BlockKind.CODE),
        replace(block, kind=BlockKind.QUOTE),
        replace(block, source_range=None),
        replace(
            block, source_range=replace(block.source_range, segment_id=DocsSegmentId("header"))
        ),
        replace(block, source_range=replace(block.source_range, end_index=20)),
    ]
    for variant in variants:
        assert plan_native_elements(replace(document, blocks=(variant,))) == ()


def test_deterministic_bounded_descending_order() -> None:
    document = _document("2026-01-01", "2026-01-02", "2026-01-03")
    plan = plan_native_elements(document, policy=NativeElementPolicy(max_elements=2))
    assert [proposal.original_text for proposal in plan] == ["2026-01-02", "2026-01-01"]
    assert plan == plan_native_elements(document, policy=NativeElementPolicy(max_elements=2))
    assert plan[0].source_sha256 == document.blocks[1].sha256


@pytest.mark.parametrize("limit", [0, 101])
def test_limits_fail_closed(limit: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        NativeElementPolicy(max_elements=limit)
