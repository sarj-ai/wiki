from __future__ import annotations

import pytest

from app.document_transform.brand import (
    SarjBrandProfile,
    build_copy_mutations,
    build_markdown_mutations,
)
from app.document_transform.gateway import TabHeader
from app.document_transform.ir import BlockKind, DocumentIR, SourceRange, TextBlock
from app.identifiers import DocsSegmentId, DocsTabId


@pytest.mark.parametrize(
    "kind",
    [BlockKind.PARAGRAPH, BlockKind.HEADING, BlockKind.LIST_ITEM, BlockKind.CHECKLIST],
)
@pytest.mark.parametrize("source_format", ["google_doc", "markdown"])
def test_brand_text_overrides_follow_named_styles_in_body_and_header(
    kind: BlockKind,
    source_format: str,
) -> None:
    tab_id = DocsTabId("tab-a")
    header_id = DocsSegmentId("header-a")
    profile = SarjBrandProfile()
    document = DocumentIR(
        title="Operations",
        blocks=(
            TextBlock(
                id="body-a",
                kind=kind,
                text="Priorities",
                level=1 if kind == BlockKind.HEADING else None,
                source_range=SourceRange(1, 11, tab_id),
            ),
        ),
        source_format=source_format,
    )
    headers = (TabHeader(tab_id=tab_id, header_id=header_id),)
    if source_format == "google_doc":
        requests = build_copy_mutations(
            document,
            document,
            profile=profile,
            headers=headers,
            logo_uri="https://wiki.sarj.ai/brand/sarj-logo-light.png",
        )
    else:
        requests = build_markdown_mutations(
            document,
            tab_id=tab_id,
            profile=profile,
            headers=headers,
            logo_uri="https://wiki.sarj.ai/brand/sarj-logo-light.png",
        )

    for segment_id, font_family in (
        (None, profile.font_family),
        (header_id, profile.header_font_family),
    ):
        style_requests = [
            request
            for request in requests
            if ("updateParagraphStyle" in request or "updateTextStyle" in request)
            and next(iter(request.values()))["range"].get("segmentId") == segment_id
        ]
        assert [next(iter(request)) for request in style_requests] == [
            "updateParagraphStyle",
            "updateTextStyle",
        ]
        paragraph_update = style_requests[0]["updateParagraphStyle"]
        text_update = style_requests[1]["updateTextStyle"]
        assert "namedStyleType" in paragraph_update["fields"].split(",")
        assert text_update["textStyle"]["weightedFontFamily"]["fontFamily"] == font_family
        assert text_update["range"]["tabId"] == tab_id
        assert text_update["range"]["startIndex"] == paragraph_update["range"]["startIndex"]


def test_unbranded_markdown_does_not_add_brand_text_overrides() -> None:
    document = DocumentIR(
        title="Operations",
        blocks=(TextBlock(id="body-a", kind=BlockKind.HEADING, text="Priorities", level=1),),
        source_format="markdown",
    )

    requests = build_markdown_mutations(
        document,
        tab_id=DocsTabId("tab-a"),
        profile=SarjBrandProfile(),
        headers=(),
        logo_uri="",
        branded=False,
    )

    assert [next(iter(request)) for request in requests] == ["insertText", "updateParagraphStyle"]
    assert requests[1]["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"] == "HEADING_1"
