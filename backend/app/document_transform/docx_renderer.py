from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
import re
from typing import Final, Literal
from urllib.parse import urlparse
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from docx import Document as WordDocument
from docx.document import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.oxml.xmlchemy import BaseOxmlElement
from docx.shared import Inches, Pt, RGBColor
from docx.styles.style import BaseStyle, ParagraphStyle
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from app.document_transform.errors import UnsupportedSourceError


_CONTENT_WIDTH_DXA: Final = 9_360
_SARJ_PURPLE: Final = "2B165E"
_SARJ_PURPLE_LIGHT: Final = "EDE9F6"
_INK: Final = "17151C"
_MUTED: Final = "66616F"
_BORDER: Final = "D9D5E2"
_CODE_FILL: Final = "F4F3F6"
_FIXED_TIMESTAMP: Final = datetime(2000, 1, 1, tzinfo=UTC)
_INLINE_PATTERN = re.compile(
    r"(`[^`\n]+`|\[([^\]]+)\]\(([^\s)]+)\)|\*\*([^*\n]+)\*\*|"
    r"__([^_\n]+)__|(?<!\*)\*([^*\n]+)\*(?!\*)|(?<!_)_([^_\n]+)_(?!_))"
)
_INVALID_XML_CHARACTER = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
_UNSUPPORTED_MARKDOWN = re.compile(
    r"!\[[^\]\n]*\]\([^\n)]*\)|<\/?[A-Za-z][^>\n]*>|\[\^[^\]\n]+\]|"
    r"^\s*\[\^[^\]\n]+\]:|^\s*~~~|^\s*>>|~~[^~\n]+~~",
    re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class _Block:
    kind: Literal["heading", "paragraph", "list", "check", "quote", "code", "table", "rule"]
    text: str = ""
    level: int = 0
    ordered: bool = False
    checked: bool = False
    rows: tuple[tuple[str, ...], ...] = ()
    has_header: bool = False


@dataclass(frozen=True, slots=True)
class _Inline:
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    url: str | None = None


@dataclass(frozen=True, slots=True)
class _NumberingIds:
    bullet: int
    ordered: int
    unchecked: int
    checked: int


def render_markdown_docx(
    markdown: str,
    *,
    title: str,
    logo_bytes: bytes | None = None,
) -> bytes:
    _validate_text(markdown, field="Markdown")
    _validate_text(title, field="Title")
    _validate_supported_markdown(markdown)

    document = WordDocument()
    _set_metadata(document, title)
    _configure_page(document)
    _configure_styles(document)
    numbering = _configure_numbering(document)
    if logo_bytes is not None:
        _configure_header(document, logo_bytes)
    _configure_footer(document)

    blocks = _without_duplicate_title(_parse_blocks(markdown), title)
    if title.strip():
        paragraph = document.add_paragraph(style="Sarj Title")
        _append_inlines(paragraph, title.strip())
    _append_blocks(document, blocks, numbering)

    output = BytesIO()
    document.save(output)
    return _canonicalize_archive(output.getvalue())


# Compatibility-friendly verb form for callers that describe conversion.
markdown_to_branded_docx = render_markdown_docx


def _validate_text(value: str, *, field: str) -> None:
    if _INVALID_XML_CHARACTER.search(value):
        raise UnsupportedSourceError from ValueError(
            f"{field} contains characters that XML cannot represent"
        )


def _validate_supported_markdown(markdown: str) -> None:
    if _UNSUPPORTED_MARKDOWN.search(markdown):
        raise UnsupportedSourceError


def _set_metadata(document: Document, title: str) -> None:
    properties = document.core_properties
    properties.title = title
    properties.author = "Sarj Wiki"
    properties.last_modified_by = "Sarj Wiki"
    properties.created = _FIXED_TIMESTAMP
    properties.modified = _FIXED_TIMESTAMP
    properties.revision = 1


def _configure_page(document: Document) -> None:
    section = document.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(1)
    section.right_margin = Inches(1)
    section.bottom_margin = Inches(1)
    section.left_margin = Inches(1)
    section.header_distance = Inches(0.35)
    section.footer_distance = Inches(0.35)


def _configure_styles(document: Document) -> None:
    styles = document.styles
    _set_style(
        _paragraph_style(styles["Normal"]),
        size=11,
        color=_INK,
        after=6,
        line_spacing=1.15,
    )
    _set_style(
        _paragraph_style(styles["Heading 1"]),
        size=22,
        color=_SARJ_PURPLE,
        bold=True,
        before=16,
        after=7,
        keep_with_next=True,
    )
    _set_style(
        _paragraph_style(styles["Heading 2"]),
        size=15,
        color=_INK,
        bold=True,
        before=13,
        after=6,
        keep_with_next=True,
    )
    _set_style(
        _paragraph_style(styles["Heading 3"]),
        size=12,
        color=_INK,
        bold=True,
        before=10,
        after=4,
        keep_with_next=True,
    )
    _set_style(
        _paragraph_style(styles.add_style("Sarj Title", WD_STYLE_TYPE.PARAGRAPH)),
        size=22,
        color=_SARJ_PURPLE,
        bold=True,
        after=8,
        keep_with_next=True,
    )
    _set_style(
        _paragraph_style(styles.add_style("Sarj List", WD_STYLE_TYPE.PARAGRAPH)),
        size=11,
        color=_INK,
        after=4,
        line_spacing=1.15,
    )
    quote = _paragraph_style(styles.add_style("Sarj Quote", WD_STYLE_TYPE.PARAGRAPH))
    _set_style(
        quote,
        size=11,
        color=_MUTED,
        italic=True,
        before=3,
        after=6,
        line_spacing=1.15,
    )
    quote.paragraph_format.left_indent = Inches(0.25)
    quote.paragraph_format.right_indent = Inches(0.125)
    code = _paragraph_style(styles.add_style("Sarj Code", WD_STYLE_TYPE.PARAGRAPH))
    _set_style(
        code,
        font="Courier New",
        size=10,
        color=_INK,
        bold=False,
        before=4,
        after=6,
        line_spacing=1.08,
    )
    code.paragraph_format.left_indent = Inches(0.125)
    code.paragraph_format.right_indent = Inches(0.125)
    _set_style(
        _paragraph_style(styles.add_style("Sarj Table Text", WD_STYLE_TYPE.PARAGRAPH)),
        size=10,
        color=_INK,
        after=2,
        line_spacing=1.08,
    )


def _paragraph_style(style: BaseStyle) -> ParagraphStyle:
    if not isinstance(style, ParagraphStyle):
        msg = f"Expected a paragraph style, got {style.type!r}"
        raise TypeError(msg)
    return style


def _set_style(
    style: ParagraphStyle,
    *,
    size: float,
    color: str,
    after: float,
    font: str = "Nunito",
    bold: bool | None = None,
    italic: bool | None = None,
    before: float = 0,
    line_spacing: float = 1.0,
    keep_with_next: bool = False,
) -> None:
    style.font.name = font
    style.font.size = Pt(size)
    style.font.color.rgb = RGBColor.from_string(color)
    style.font.bold = bold
    style.font.italic = italic
    style_element = style.element
    if style_element is None:
        msg = f"Paragraph style {style.name!r} is detached"
        raise ValueError(msg)
    style_element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), font)
    paragraph_format = style.paragraph_format
    paragraph_format.space_before = Pt(before)
    paragraph_format.space_after = Pt(after)
    paragraph_format.line_spacing = line_spacing
    paragraph_format.keep_with_next = keep_with_next
    paragraph_format.widow_control = True


def _configure_numbering(document: Document) -> _NumberingIds:
    numbering = document.part.numbering_part.element
    abstract_ids = [
        int(element.get(qn("w:abstractNumId")))
        for element in numbering.findall(qn("w:abstractNum"))
    ]
    num_ids = [int(element.get(qn("w:numId"))) for element in numbering.findall(qn("w:num"))]
    next_abstract = max(abstract_ids, default=-1) + 1
    next_num = max(num_ids, default=0) + 1
    values: list[int] = []
    schemes = (
        ("bullet", ("●", "○", "▪"), "Nunito"),
        ("decimal", ("%1.", "%2.", "%3."), "Nunito"),
        ("bullet", ("☐", "☐", "☐"), "DejaVu Sans"),
        ("bullet", ("☒", "☒", "☒"), "DejaVu Sans"),
    )
    for offset, (number_format, markers, marker_font) in enumerate(schemes):
        abstract_id = next_abstract + offset
        num_id = next_num + offset
        numbering.append(
            _abstract_numbering(
                abstract_id,
                number_format=number_format,
                markers=markers,
                marker_font=marker_font,
            )
        )
        concrete = OxmlElement("w:num")
        concrete.set(qn("w:numId"), str(num_id))
        reference = OxmlElement("w:abstractNumId")
        reference.set(qn("w:val"), str(abstract_id))
        concrete.append(reference)
        numbering.append(concrete)
        values.append(num_id)
    return _NumberingIds(*values)


def _abstract_numbering(
    abstract_id: int,
    *,
    number_format: str,
    markers: tuple[str, str, str],
    marker_font: str,
) -> BaseOxmlElement:
    abstract = OxmlElement("w:abstractNum")
    abstract.set(qn("w:abstractNumId"), str(abstract_id))
    multilevel = OxmlElement("w:multiLevelType")
    multilevel.set(qn("w:val"), "hybridMultilevel")
    abstract.append(multilevel)
    for level, marker in enumerate(markers):
        level_element = OxmlElement("w:lvl")
        level_element.set(qn("w:ilvl"), str(level))
        _append_value_element(level_element, "w:start", "1")
        _append_value_element(level_element, "w:numFmt", number_format)
        _append_value_element(level_element, "w:lvlText", marker)
        _append_value_element(level_element, "w:lvlJc", "left")
        paragraph_properties = OxmlElement("w:pPr")
        indentation = OxmlElement("w:ind")
        indentation.set(qn("w:left"), str(720 + level * 360))
        indentation.set(qn("w:hanging"), "360")
        paragraph_properties.append(indentation)
        level_element.append(paragraph_properties)
        run_properties = OxmlElement("w:rPr")
        fonts = OxmlElement("w:rFonts")
        fonts.set(qn("w:ascii"), marker_font)
        fonts.set(qn("w:hAnsi"), marker_font)
        run_properties.append(fonts)
        level_element.append(run_properties)
        abstract.append(level_element)
    return abstract


def _append_value_element(parent: BaseOxmlElement, tag: str, value: str) -> None:
    element = OxmlElement(tag)
    element.set(qn("w:val"), value)
    parent.append(element)


def _xml_member(owner: object, member_name: str) -> BaseOxmlElement:
    element = getattr(owner, member_name, None)
    if not isinstance(element, BaseOxmlElement):
        msg = f"python-docx {type(owner).__name__} has no usable {member_name} XML member"
        raise TypeError(msg)
    return element


def _properties_element(owner: object, member_name: str, tag: str) -> BaseOxmlElement:
    container = _xml_member(owner, member_name)
    properties = container.find(qn(tag))
    if isinstance(properties, BaseOxmlElement):
        return properties
    properties = OxmlElement(tag)
    container.insert(0, properties)
    return properties


def _configure_header(document: Document, logo_bytes: bytes) -> None:
    paragraph = document.sections[0].header.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.space_after = Pt(3)
    paragraph.add_run().add_picture(BytesIO(logo_bytes), width=Inches(1.3))
    drawing_properties = _xml_member(paragraph, "_p").xpath(".//wp:docPr")
    if drawing_properties:
        drawing_properties[0].set("name", "Sarj logo")
        drawing_properties[0].set("descr", "Sarj logo")


def _configure_footer(document: Document) -> None:
    paragraph = document.sections[0].footer.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    paragraph.paragraph_format.space_before = Pt(4)
    label = paragraph.add_run("Sarj  ·  ")
    _format_run(label, size=9, color=_MUTED)
    _append_page_field(paragraph)


def _append_page_field(paragraph: Paragraph) -> None:
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = " PAGE "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for element in (begin, instruction, separate):
        run = paragraph.add_run()
        _format_run(run, size=9, color=_MUTED)
        _xml_member(run, "_r").append(element)
    value = paragraph.add_run("1")
    _format_run(value, size=9, color=_MUTED)
    final = paragraph.add_run()
    _format_run(final, size=9, color=_MUTED)
    _xml_member(final, "_r").append(end)


def _append_blocks(
    document: Document,
    blocks: Iterable[_Block],
    numbering: _NumberingIds,
) -> None:
    for block in blocks:
        if block.kind == "heading":
            paragraph = document.add_paragraph(style=f"Heading {min(max(block.level, 1), 3)}")
            _append_inlines(paragraph, block.text)
        elif block.kind == "paragraph":
            paragraph = document.add_paragraph(style="Normal")
            _append_inlines(paragraph, block.text)
        elif block.kind == "list":
            paragraph = document.add_paragraph(style="Sarj List")
            _set_numbering(
                paragraph, numbering.ordered if block.ordered else numbering.bullet, block.level
            )
            _append_inlines(paragraph, block.text)
        elif block.kind == "check":
            paragraph = document.add_paragraph(style="Sarj List")
            _set_numbering(
                paragraph,
                numbering.checked if block.checked else numbering.unchecked,
                block.level,
            )
            _append_inlines(paragraph, block.text)
        elif block.kind == "quote":
            paragraph = document.add_paragraph(style="Sarj Quote")
            _set_paragraph_box(paragraph, fill=_SARJ_PURPLE_LIGHT, left_border=_SARJ_PURPLE)
            _append_inlines(paragraph, block.text)
        elif block.kind == "code":
            _append_code_block(document, block.text)
        elif block.kind == "table":
            _append_table(document, block)
        elif block.kind == "rule":
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.space_after = Pt(6)
            _set_paragraph_bottom_border(paragraph)


def _set_numbering(paragraph: Paragraph, num_id: int, level: int) -> None:
    properties = _properties_element(paragraph, "_p", "w:pPr")
    existing = properties.find(qn("w:numPr"))
    if existing is not None:
        properties.remove(existing)
    num_properties = OxmlElement("w:numPr")
    _append_value_element(num_properties, "w:ilvl", str(min(max(level, 0), 2)))
    _append_value_element(num_properties, "w:numId", str(num_id))
    properties.append(num_properties)


def _append_code_block(document: Document, text: str) -> None:
    paragraph = document.add_paragraph(style="Sarj Code")
    _set_paragraph_box(paragraph, fill=_CODE_FILL)
    for index, line in enumerate(text.split("\n")):
        if index:
            paragraph.add_run().add_break()
        run = paragraph.add_run(line or " ")
        _format_run(run, font="Courier New", size=10, color=_INK)


def _append_table(document: Document, block: _Block) -> None:
    if not block.rows:
        return
    column_count = len(block.rows[0])
    widths = _table_widths(block.rows, column_count)
    table = document.add_table(rows=len(block.rows), cols=column_count)
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.autofit = False
    _configure_table_geometry(table, widths)
    for row_index, (source_row, target_row) in enumerate(zip(block.rows, table.rows, strict=True)):
        if block.has_header and row_index == 0:
            repeat = OxmlElement("w:tblHeader")
            repeat.set(qn("w:val"), "true")
            _properties_element(target_row, "_tr", "w:trPr").append(repeat)
        for value, cell, width in zip(source_row, target_row.cells, widths, strict=True):
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            cell.width = Inches(width / 1_440)
            _set_cell_width(cell, width)
            if block.has_header and row_index == 0:
                _set_cell_fill(cell, _SARJ_PURPLE_LIGHT)
            paragraph = cell.paragraphs[0]
            paragraph.style = _paragraph_style(document.styles["Sarj Table Text"])
            _append_inlines(paragraph, value, force_bold=block.has_header and row_index == 0)
    spacer = document.add_paragraph()
    spacer.paragraph_format.space_after = Pt(4)


def _configure_table_geometry(table: Table, widths: tuple[int, ...]) -> None:
    table_xml = _xml_member(table, "_tbl")
    properties = _required_child(table_xml, "w:tblPr")
    table_width = properties.first_child_found_in("w:tblW")
    if table_width is None:
        table_width = OxmlElement("w:tblW")
        properties.insert(0, table_width)
    table_width.set(qn("w:w"), str(sum(widths)))
    table_width.set(qn("w:type"), "dxa")
    indentation = OxmlElement("w:tblInd")
    indentation.set(qn("w:w"), "0")
    indentation.set(qn("w:type"), "dxa")
    properties.append(indentation)
    existing_layout = properties.first_child_found_in("w:tblLayout")
    if existing_layout is not None:
        properties.remove(existing_layout)
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    properties.append(layout)
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        border = OxmlElement(f"w:{edge}")
        border.set(qn("w:val"), "single")
        border.set(qn("w:sz"), "4")
        border.set(qn("w:space"), "0")
        border.set(qn("w:color"), _BORDER)
        borders.append(border)
    properties.append(borders)
    margins = OxmlElement("w:tblCellMar")
    for edge, width in (("top", 100), ("left", 120), ("bottom", 100), ("right", 120)):
        margin = OxmlElement(f"w:{edge}")
        margin.set(qn("w:w"), str(width))
        margin.set(qn("w:type"), "dxa")
        margins.append(margin)
    properties.append(margins)
    grid_columns = _required_child(table_xml, "w:tblGrid").findall(qn("w:gridCol"))
    for grid_column, width in zip(grid_columns, widths, strict=True):
        # ``CT_TblGridCol.w`` treats a bare int as EMUs. Write the OOXML dxa
        # value directly so Google Docs does not collapse every column to a
        # handful of twips during import.
        grid_column.set(qn("w:w"), str(width))


def _required_child(parent: BaseOxmlElement, tag: str) -> BaseOxmlElement:
    child = parent.find(qn(tag))
    if not isinstance(child, BaseOxmlElement):
        msg = f"Required WordprocessingML child is missing: {tag}"
        raise TypeError(msg)
    return child


def _set_cell_width(cell: _Cell, width: int) -> None:
    properties = _properties_element(cell, "_tc", "w:tcPr")
    cell_width = properties.find(qn("w:tcW"))
    if cell_width is None:
        cell_width = OxmlElement("w:tcW")
        properties.insert(0, cell_width)
    cell_width.set(qn("w:w"), str(width))
    cell_width.set(qn("w:type"), "dxa")


def _set_cell_fill(cell: _Cell, color: str) -> None:
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), color)
    _properties_element(cell, "_tc", "w:tcPr").append(shading)


def _append_inlines(paragraph: Paragraph, text: str, *, force_bold: bool = False) -> None:
    for inline in _parse_inline(text):
        if inline.url is not None and _is_safe_link(inline.url):
            run = paragraph.add_run(inline.text)
            _format_inline_run(run, inline, force_bold=force_bold, hyperlink=True)
            _wrap_run_in_hyperlink(paragraph, run, inline.url)
        else:
            run = paragraph.add_run(inline.text)
            _format_inline_run(run, inline, force_bold=force_bold)


def _format_inline_run(
    run: Run,
    inline: _Inline,
    *,
    force_bold: bool,
    hyperlink: bool = False,
) -> None:
    run.bold = inline.bold or force_bold
    run.italic = inline.italic
    if inline.code:
        _format_run(run, font="Courier New", size=10, color=_INK)
        shading = OxmlElement("w:shd")
        shading.set(qn("w:val"), "clear")
        shading.set(qn("w:color"), "auto")
        shading.set(qn("w:fill"), _CODE_FILL)
        _properties_element(run, "_r", "w:rPr").append(shading)
    elif hyperlink:
        run.font.color.rgb = RGBColor.from_string(_SARJ_PURPLE)
        run.underline = True


def _format_run(run: Run, *, font: str = "Nunito", size: float, color: str) -> None:
    run.font.name = font
    run.font.size = Pt(size)
    run.font.color.rgb = RGBColor.from_string(color)
    properties = _properties_element(run, "_r", "w:rPr")
    fonts = properties.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        properties.insert(0, fonts)
    fonts.set(qn("w:eastAsia"), font)


def _wrap_run_in_hyperlink(paragraph: Paragraph, run: Run, url: str) -> None:
    relationship_id = paragraph.part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), relationship_id)
    hyperlink.set(qn("w:history"), "1")
    run_xml = _xml_member(run, "_r")
    parent = run_xml.getparent()
    if parent is None:
        msg = "Run is detached from its paragraph"
        raise ValueError(msg)
    parent.remove(run_xml)
    hyperlink.append(run_xml)
    _xml_member(paragraph, "_p").append(hyperlink)


def _is_safe_link(url: str) -> bool:
    return urlparse(url).scheme.casefold() in {"http", "https", "mailto"}


def _set_paragraph_box(
    paragraph: Paragraph,
    *,
    fill: str,
    left_border: str | None = None,
) -> None:
    properties = _properties_element(paragraph, "_p", "w:pPr")
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), fill)
    properties.append(shading)
    if left_border is not None:
        borders = OxmlElement("w:pBdr")
        border = OxmlElement("w:left")
        border.set(qn("w:val"), "single")
        border.set(qn("w:sz"), "18")
        border.set(qn("w:space"), "8")
        border.set(qn("w:color"), left_border)
        borders.append(border)
        properties.append(borders)


def _set_paragraph_bottom_border(paragraph: Paragraph) -> None:
    borders = OxmlElement("w:pBdr")
    border = OxmlElement("w:bottom")
    border.set(qn("w:val"), "single")
    border.set(qn("w:sz"), "8")
    border.set(qn("w:space"), "1")
    border.set(qn("w:color"), _BORDER)
    borders.append(border)
    _properties_element(paragraph, "_p", "w:pPr").append(borders)


def _table_widths(rows: tuple[tuple[str, ...], ...], columns: int) -> tuple[int, ...]:
    weights = [max(4.0, max(len(row[index]) for row in rows) ** 0.5) for index in range(columns)]
    minimum = _CONTENT_WIDTH_DXA * 0.14
    raw = [max(minimum, _CONTENT_WIDTH_DXA * weight / sum(weights)) for weight in weights]
    scale = _CONTENT_WIDTH_DXA / sum(raw)
    widths = [int(value * scale) for value in raw]
    widths[-1] += _CONTENT_WIDTH_DXA - sum(widths)
    return tuple(widths)


def _parse_inline(text: str) -> tuple[_Inline, ...]:
    result: list[_Inline] = []
    cursor = 0
    for match in _INLINE_PATTERN.finditer(text):
        if match.start() > cursor:
            result.append(_Inline(text[cursor : match.start()]))
        token = match.group(0)
        if token.startswith("`"):
            result.append(_Inline(token[1:-1], code=True))
        elif token.startswith("["):
            result.append(_Inline(match.group(2), url=match.group(3)))
        elif token.startswith(("**", "__")):
            result.append(_Inline(match.group(4) or match.group(5), bold=True))
        else:
            result.append(_Inline(match.group(6) or match.group(7), italic=True))
        cursor = match.end()
    if cursor < len(text):
        result.append(_Inline(text[cursor:]))
    return tuple(result) or (_Inline(""),)


def _without_duplicate_title(blocks: tuple[_Block, ...], title: str) -> tuple[_Block, ...]:
    if not blocks:
        return blocks
    first = blocks[0]
    normalized_title = " ".join(title.split()).casefold()
    normalized_heading = " ".join(first.text.split()).casefold()
    if first.kind == "heading" and first.level == 1 and normalized_heading == normalized_title:
        return blocks[1:]
    return blocks


def _parse_blocks(markdown: str) -> tuple[_Block, ...]:
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[_Block] = []
    index = 0
    paragraph_lines: list[str] = []

    def flush_paragraph() -> None:
        if paragraph_lines:
            blocks.append(
                _Block("paragraph", text=" ".join(line.strip() for line in paragraph_lines))
            )
            paragraph_lines.clear()

    while index < len(lines):
        line = lines[index]
        if fence := re.match(r"^\s*(```+|~~~+)(.*)$", line):
            flush_paragraph()
            marker = fence.group(1)
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not re.match(
                rf"^\s*{re.escape(marker[0])}{{{len(marker)},}}\s*$", lines[index]
            ):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            blocks.append(_Block("code", text="\n".join(code_lines)))
            continue
        if heading := re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line):
            flush_paragraph()
            blocks.append(_Block("heading", text=heading.group(2), level=len(heading.group(1))))
            index += 1
            continue
        if re.match(r"^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$", line):
            flush_paragraph()
            blocks.append(_Block("rule"))
            index += 1
            continue
        list_block = _parse_list_block(line)
        if list_block is not None:
            flush_paragraph()
            blocks.append(list_block)
            index += 1
            continue
        if line.lstrip().startswith(">"):
            flush_paragraph()
            blocks.append(_Block("quote", text=re.sub(r"^\s*>\s?", "", line)))
            index += 1
            continue
        if (
            _looks_like_table_row(line)
            and index + 1 < len(lines)
            and _is_table_separator(lines[index + 1])
        ):
            flush_paragraph()
            rows = [_split_table_row(line)]
            index += 2
            while index < len(lines) and _looks_like_table_row(lines[index]):
                rows.append(_split_table_row(lines[index]))
                index += 1
            width = max(len(row) for row in rows)
            normalized = tuple(row + ("",) * (width - len(row)) for row in rows)
            blocks.append(_Block("table", rows=normalized, has_header=True))
            continue
        if not line.strip():
            flush_paragraph()
            index += 1
            continue
        paragraph_lines.append(line)
        index += 1
    flush_paragraph()
    return tuple(blocks)


def _looks_like_table_row(line: str) -> bool:
    return "|" in line and bool(line.strip().strip("| "))


def _parse_list_block(line: str) -> _Block | None:
    match = re.match(r"^(\s*)([-+*]|\d+[.)])\s+(.+)$", line)
    if match is None:
        return None
    marker, value = match.group(2), match.group(3)
    level = min(len(match.group(1).expandtabs(4)) // 2, 2)
    if (checklist := re.match(r"^\[([ xX])\]\s+(.*)$", value)) is not None:
        return _Block(
            "check",
            text=checklist.group(2),
            level=level,
            checked=checklist.group(1).casefold() == "x",
        )
    return _Block("list", text=value, level=level, ordered=marker[0].isdigit())


def _is_table_separator(line: str) -> bool:
    cells = _split_table_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def _split_table_row(line: str) -> tuple[str, ...]:
    value = line.strip().removeprefix("|")
    if value.endswith("|") and not value.endswith(r"\|"):
        value = value[:-1]
    cells = re.split(r"(?<!\\)\|", value)
    return tuple(cell.strip().replace(r"\|", "|") for cell in cells)


def _canonicalize_archive(raw: bytes) -> bytes:
    output = BytesIO()
    with (
        ZipFile(BytesIO(raw)) as source,
        ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as target,
    ):
        for name in sorted(source.namelist()):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            target.writestr(info, source.read(name))
    return output.getvalue()
