from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from defusedxml.ElementTree import fromstring
from docx import Document as WordDocument
import pytest

from app.document_transform.docx_renderer import render_markdown_docx
from app.document_transform.errors import UnsupportedSourceError


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
LOGO_BYTES = (Path(__file__).parents[2] / "frontend/public/brand/sarj-logo-light.png").read_bytes()

SOURCE = """# Executive summary

This is **important**, *careful*, and uses `inline_code` plus [Sarj](https://sarj.ai).

1. First numbered item
2. Second numbered item
   - Nested bullet
- [ ] Pending review
- [x] Approved

> Preserve this quoted decision.

| Owner | Status | Detail |
| --- | --- | --- |
| Platform | Ready | Native structures |
| Docs | Review | Long values wrap rather than clip |

```python
def hello(name: str) -> str:
    return f"Hello, {name}"
```
"""


@pytest.fixture
def rendered() -> bytes:
    return render_markdown_docx(SOURCE, title="Technology review", logo_bytes=LOGO_BYTES)


def test_render_is_valid_loadable_and_byte_deterministic(rendered: bytes) -> None:
    assert rendered == render_markdown_docx(
        SOURCE,
        title="Technology review",
        logo_bytes=LOGO_BYTES,
    )
    document = WordDocument(BytesIO(rendered))
    assert document.core_properties.title == "Technology review"
    assert document.core_properties.author == "Sarj Wiki"
    with ZipFile(BytesIO(rendered)) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == sorted(archive.namelist())
        assert all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in archive.infolist())


def test_render_preserves_native_structure_and_inline_formatting(rendered: bytes) -> None:
    with ZipFile(BytesIO(rendered)) as archive:
        document = fromstring(archive.read("word/document.xml"))
        relationships = fromstring(archive.read("word/_rels/document.xml.rels"))
        numbering = fromstring(archive.read("word/numbering.xml"))

    assert document.find(f".//{W}pStyle[@{W}val='Heading1']") is not None
    assert len(document.findall(f".//{W}numPr")) == 5
    assert document.find(f".//{W}tbl") is not None
    raw_grid_widths = [cell.get(f"{W}w") for cell in document.findall(f".//{W}gridCol")]
    assert all(width is not None for width in raw_grid_widths)
    grid_widths = [int(width) for width in raw_grid_widths if width is not None]
    assert len(grid_widths) == 3
    assert sum(grid_widths) == 9_360
    assert min(grid_widths) >= 1_300
    assert all(cell.get(f"{W}type") == "dxa" for cell in document.findall(f".//{W}tcW"))
    assert document.find(f".//{W}tblHeader") is not None
    assert document.find(f".//{W}b") is not None
    assert document.find(f".//{W}i") is not None
    assert document.find(f".//{W}hyperlink") is not None
    assert document.find(f".//{W}shd[@{W}fill='F4F3F6']") is not None
    targets = [item.attrib.get("Target") for item in relationships]
    assert "https://sarj.ai" in targets
    checklist_markers = [node.get(f"{W}val") for node in numbering.findall(f".//{W}lvlText")]
    assert "☐" in checklist_markers
    assert "☒" in checklist_markers


def test_sarj_brand_styles_header_logo_and_footer_page_number(rendered: bytes) -> None:
    with ZipFile(BytesIO(rendered)) as archive:
        styles = fromstring(archive.read("word/styles.xml"))
        document = fromstring(archive.read("word/document.xml"))
        header = fromstring(archive.read("word/header1.xml"))
        footer = fromstring(archive.read("word/footer1.xml"))

    title_style = styles.find(f".//{W}style[@{W}styleId='SarjTitle']")
    assert title_style is not None
    assert title_style.find(f".//{W}pBdr") is None
    title_fonts = title_style.find(f".//{W}rFonts")
    assert title_fonts is not None
    assert title_fonts.get(f"{W}ascii") == "Nunito"
    assert styles.find(f".//{W}color[@{W}val='2B165E']") is not None
    assert header.find(".//{http://schemas.openxmlformats.org/drawingml/2006/main}blip") is not None
    header_alignment = header.find(f".//{W}jc")
    assert header_alignment is not None
    assert header_alignment.get(f"{W}val") == "center"
    page_field = footer.find(f".//{W}instrText")
    assert page_field is not None
    assert page_field.text is not None
    assert page_field.text.strip() == "PAGE"
    margins = document.find(f".//{W}pgMar")
    assert margins is not None
    assert margins.get(f"{W}left") == margins.get(f"{W}right") == "1440"


def test_custom_logo_is_a_native_header_picture() -> None:
    rendered = render_markdown_docx("Body", title="Custom", logo_bytes=LOGO_BYTES)
    with ZipFile(BytesIO(rendered)) as archive:
        assert archive.read("word/media/image1.png") == LOGO_BYTES


def test_missing_logo_does_not_fabricate_brand_artwork() -> None:
    rendered = render_markdown_docx("Body", title="No logo")
    with ZipFile(BytesIO(rendered)) as archive:
        assert not any(name.startswith("word/media/") for name in archive.namelist())


@pytest.mark.parametrize(
    "markdown",
    [
        "![architecture](https://example.com/diagram.png)",
        "<aside>Decision</aside>",
        "A claim with a footnote[^1].",
        "~~obsolete~~",
        ">> Nested quotation",
        "~~~python\nprint('unsafe')\n~~~",
    ],
)
def test_rejects_markdown_constructs_that_cannot_be_preserved(markdown: str) -> None:
    with pytest.raises(UnsupportedSourceError):
        render_markdown_docx(markdown, title="Unsupported")


def test_rejects_xml_control_characters_and_unsafe_links() -> None:
    with pytest.raises(UnsupportedSourceError):
        render_markdown_docx("bad\x00text", title="Bad")

    rendered = render_markdown_docx("[click](javascript:alert)", title="Safe")
    with ZipFile(BytesIO(rendered)) as archive:
        relationships = fromstring(archive.read("word/_rels/document.xml.rels"))
    assert all(
        item.attrib.get("TargetMode") != "External"
        for item in relationships
        if item.attrib.get("Type", "").endswith("/hyperlink")
    )


def test_matching_leading_h1_does_not_duplicate_visible_title() -> None:
    rendered = render_markdown_docx(
        "# Technology review\n\nBody",
        title="Technology review",
    )
    document = WordDocument(BytesIO(rendered))
    assert [paragraph.text for paragraph in document.paragraphs].count("Technology review") == 1
