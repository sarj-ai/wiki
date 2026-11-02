from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, NamedTuple

from pydantic import JsonValue
import pytest

from app.document_transform import google_gateway as gateway
from app.document_transform.brand import (
    SarjBrandProfile,
    build_copy_mutations,
    paragraph_keep_lines_together,
)
from app.document_transform.ir import (
    BlockKind,
    DocumentIR,
    SourceRange,
    TextBlock,
    parse_google_document,
    utf16_length,
)
from app.identifiers import DocsTabId


if TYPE_CHECKING:
    from app.document_transform.brand import DocsRequest


class _NativeHeading(NamedTuple):
    document: dict[str, JsonValue]
    paragraph: dict[str, JsonValue]
    style: dict[str, JsonValue]


def _heading() -> TextBlock:
    text = "Migration evidence and the limits of recovered nightingale tracking devices"
    return TextBlock(
        id="heading",
        kind=BlockKind.HEADING,
        text=text,
        level=2,
        source_range=SourceRange(1, 1 + utf16_length(text), DocsTabId("tab")),
    )


def _requests(block: TextBlock) -> list[DocsRequest]:
    document = DocumentIR(
        title="Nightingale evidence",
        blocks=(block,),
        source_format="google_doc",
        tab_ids=(DocsTabId("tab"),),
    )
    return build_copy_mutations(
        document,
        document,
        profile=SarjBrandProfile(),
        headers=(),
        logo_uri="",
    )


def _native_heading() -> _NativeHeading:
    heading = _heading()
    source = heading.source_range
    assert source is not None
    requests = _requests(heading)
    paragraph_style: dict[str, JsonValue] = next(
        request["updateParagraphStyle"]["paragraphStyle"]
        for request in requests
        if "updateParagraphStyle" in request
    )
    text_style: dict[str, JsonValue] = next(
        request["updateTextStyle"]["textStyle"]
        for request in requests
        if "updateTextStyle" in request
    )
    paragraph: dict[str, JsonValue] = {
        "paragraphStyle": paragraph_style,
        "elements": [
            {
                "startIndex": source.start_index,
                "endIndex": source.end_index + 1,
                "textRun": {"content": heading.text + "\n", "textStyle": text_style},
            }
        ],
    }
    document: dict[str, JsonValue] = {
        "revisionId": "heading-revision",
        "tabs": [
            {
                "tabProperties": {"tabId": source.tab_id},
                "documentTab": {
                    "body": {
                        "content": [
                            {
                                "startIndex": source.start_index,
                                "endIndex": source.end_index + 1,
                                "paragraph": paragraph,
                            }
                        ]
                    }
                },
            }
        ],
    }
    return _NativeHeading(document, paragraph, paragraph_style)


@pytest.mark.parametrize("level", [1, 2, 3, 4, 5, 6])
def test_wrapped_heading_keeps_its_lines_and_following_paragraph(level: int) -> None:
    heading = replace(_heading(), level=level)

    paragraph = next(
        request["updateParagraphStyle"]
        for request in _requests(heading)
        if "updateParagraphStyle" in request
    )

    assert paragraph["paragraphStyle"]["namedStyleType"] == f"HEADING_{level}"
    assert paragraph["paragraphStyle"]["keepLinesTogether"] is True
    assert paragraph["paragraphStyle"]["keepWithNext"] is True
    assert "keepLinesTogether" in paragraph["fields"].split(",")


@pytest.mark.parametrize("native_style", ["TITLE", "SUBTITLE"])
def test_native_title_roles_keep_wrapped_lines_together(native_style: str) -> None:
    heading = replace(_heading(), native_named_style=native_style)

    paragraph = next(
        request["updateParagraphStyle"]
        for request in _requests(heading)
        if "updateParagraphStyle" in request
    )

    assert paragraph["paragraphStyle"]["namedStyleType"] == native_style
    assert paragraph["paragraphStyle"]["keepLinesTogether"] is True


@pytest.mark.parametrize("keep_with_next", [False, True])
def test_explicit_body_pagination_retains_line_continuity(keep_with_next: bool) -> None:
    paragraph_block = replace(
        _heading(), kind=BlockKind.PARAGRAPH, level=None, pagination_keep_with_next=keep_with_next
    )

    paragraph = next(
        request["updateParagraphStyle"]
        for request in _requests(paragraph_block)
        if "updateParagraphStyle" in request
    )

    assert paragraph["paragraphStyle"]["keepWithNext"] is keep_with_next
    assert paragraph["paragraphStyle"]["keepLinesTogether"] is True


@pytest.mark.parametrize("emphasis", [(), (("bold", True),)])
def test_ordinary_and_authored_lead_in_prose_do_not_gain_pagination(
    emphasis: tuple[tuple[str, bool], ...],
) -> None:
    paragraph_block = replace(
        _heading(), kind=BlockKind.PARAGRAPH, level=None, authored_emphasis=emphasis
    )

    paragraph = next(
        request["updateParagraphStyle"]
        for request in _requests(paragraph_block)
        if "updateParagraphStyle" in request
    )

    assert not paragraph_keep_lines_together(paragraph_block)
    assert paragraph["paragraphStyle"]["keepWithNext"] is False
    assert "keepLinesTogether" not in paragraph["paragraphStyle"]
    assert "keepLinesTogether" not in paragraph["fields"].split(",")


@pytest.mark.parametrize("guard", ["visual", "layout", "font_only"])
def test_heading_continuity_preserves_authored_formatting_guards(guard: str) -> None:
    heading = _heading()
    match guard:
        case "visual":
            heading = replace(heading, preserved_visual_authority=True)
        case "layout":
            heading = replace(heading, preserved_paragraph_style_sha256="a" * 64)
        case _:
            assert heading.source_range is not None
            heading = replace(heading, editable=False, font_style_ranges=(heading.source_range,))

    assert paragraph_keep_lines_together(heading)
    assert not any("updateParagraphStyle" in request for request in _requests(heading))


@pytest.mark.parametrize("keep_lines_together", [None, False, True])
def test_body_heading_verification_requires_complete_line_continuity(
    keep_lines_together: bool | None,
) -> None:
    fixture = _native_heading()
    if keep_lines_together is None:
        fixture.style.pop("keepLinesTogether")
    else:
        fixture.style["keepLinesTogether"] = keep_lines_together
    parsed = parse_google_document(fixture.document, title="Nightingale evidence")
    assert parsed.blocks[0].kind is BlockKind.HEADING

    verified = gateway.verify_brand_application(
        fixture.document,
        parsed,
        preserved_furniture_tabs=frozenset({DocsTabId("tab")}),
        preserved_header_tabs=frozenset({DocsTabId("tab")}),
    )
    diagnostics = gateway._paragraph_brand_diagnostics(fixture.paragraph, parsed.blocks[0])

    assert verified is (keep_lines_together is True)
    assert diagnostics["layout"] is (keep_lines_together is True)
    assert diagnostics["named_style"] is True
    assert diagnostics["text_style"] is True


def test_heading_verification_retains_explicit_visual_preservation() -> None:
    fixture = _native_heading()
    fixture.style.pop("keepLinesTogether")
    parsed = parse_google_document(fixture.document, title="Nightingale evidence")
    block = replace(parsed.blocks[0], preserved_visual_authority=True)

    assert not gateway._paragraph_has_brand(fixture.paragraph, block)
    assert gateway._paragraph_has_brand(
        fixture.paragraph, block, allow_preserved_visual_authority=True
    )
    assert gateway._paragraph_brand_diagnostics(
        fixture.paragraph, block, allow_preserved_visual_authority=True
    )["layout"]
