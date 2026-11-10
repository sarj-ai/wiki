from io import BytesIO

from docx import Document as WordDocument
from docx.document import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.styles.style import ParagraphStyle
import pytest

from app.document_transform.docx_renderer import render_markdown_docx


def _style(document: Document, name: str) -> ParagraphStyle:
    style = document.styles[name]
    assert isinstance(style, ParagraphStyle)
    return style


@pytest.mark.parametrize("depth", range(1, 6))
@pytest.mark.parametrize("marker", ["-", "1.", "- [ ]", "- [x]"])
@pytest.mark.parametrize("unit", ["ns", "ms", "GB", "%", "Mbps"])
def test_100_document_layouts_have_clear_hierarchy_justified_body_and_readable_tables(
    depth: int, marker: str, unit: str
) -> None:
    headings = "\n\n".join("#" * level + " Latency reference" for level in range(1, depth + 1))
    prose = "The team compares latency estimates before choosing storage. العربية مع English."
    source = f"{headings}\n\n{prose}\n\n{marker} Review the estimates\n\n> Keep this quotation.\n\n```text\nkeep   spaces\n```\n\n| Operation | Value |\n| --- | --- |\n| Cache | 1 {unit} |\n| Disk | 100 {unit} |\n"
    document = WordDocument(BytesIO(render_markdown_docx(source, title="Latency reference")))
    normal = _style(document, "Normal").paragraph_format
    assert normal.alignment == WD_ALIGN_PARAGRAPH.JUSTIFY
    assert normal.widow_control
    assert any(paragraph.text == prose for paragraph in document.paragraphs)
    for level in range(1, depth + 1):
        heading = _style(document, f"Heading {level}").paragraph_format
        assert heading.alignment == WD_ALIGN_PARAGRAPH.LEFT
        assert heading.keep_with_next
        assert heading.keep_together
        assert heading.space_before is not None
        assert heading.space_before.pt >= 6
    for style_name in ("Sarj List", "Sarj Quote", "Sarj Code", "Sarj Table Text"):
        assert _style(document, style_name).paragraph_format.alignment == WD_ALIGN_PARAGRAPH.LEFT
    list_format = _style(document, "Sarj List").paragraph_format
    assert list_format.space_after is not None
    assert list_format.space_after.pt == 2
    assert list_format.line_spacing == 1
    table_size = _style(document, "Sarj Table Text").font.size
    assert table_size is not None
    assert table_size.pt == 10
    table = document.tables[0]
    assert [[cell.text for cell in row.cells] for row in table.rows] == [
        ["Operation", "Value"],
        ["Cache", f"1 {unit}"],
        ["Disk", f"100 {unit}"],
    ]
    assert table.rows[0]._tr.find(qn("w:trPr")).find(qn("w:tblHeader")) is not None
    assert all(row._tr.find(qn("w:trPr")).find(qn("w:cantSplit")) is not None for row in table.rows)
    for row in table.rows:
        assert row.cells[1].paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.RIGHT
    assert table.cell(0, 0)._tc.find(qn("w:tcPr")).find(qn("w:shd")).get(qn("w:fill")) == "EEECFA"
    assert table.cell(2, 0)._tc.find(qn("w:tcPr")).find(qn("w:shd")).get(qn("w:fill")) == "F5F5F5"
    footer = document.sections[0].footer.paragraphs[0]
    assert footer.alignment == WD_ALIGN_PARAGRAPH.RIGHT
    assert footer.paragraph_format.line_spacing == 1
    assert footer.paragraph_format.space_after is not None
    assert footer.paragraph_format.space_after.pt == 0
    assert " PAGE " in footer._p.xml
    assert any(paragraph.text == "keep   spaces" for paragraph in document.paragraphs)
