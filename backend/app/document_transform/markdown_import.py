from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, NamedTuple

from pydantic import JsonValue

from app.document_transform.brand_manifest import (
    CURRENT_BRAND_MANIFEST,
    HEADER_TITLE_SEPARATOR,
    trusted_document_logo_description,
)
from app.document_transform.import_manifest import (
    MarkdownImportBlock,
    MarkdownImportInline,
    MarkdownImportManifest,
)
from app.document_transform.ir import utf16_length
from app.document_transform.text_direction import first_strong_is_rtl


_ORDERED_GLYPH_TYPES = frozenset(
    {
        "ALPHA",
        "DECIMAL",
        "ROMAN",
        "UPPER_ALPHA",
        "UPPER_ROMAN",
        "ZERO_DECIMAL",
    }
)
_CODE_FONTS = frozenset({"consolas", "courier new", "menlo", "monospace", "roboto mono"})
_TOLERANCE = 1e-6
_H2_LEVEL = 2
_MAX_HEADER_ELEMENTS = 4
_MAX_HEADER_NAMED_STYLES = 60
_NORMAL_FONT_WEIGHT = 400
_BOLD_FONT_WEIGHT = 700
_FONT_WEIGHT_STEP = 100
_HEADER_RUN_STYLE_FIELDS = frozenset(
    {
        "weightedFontFamily",
        "fontSize",
        "foregroundColor",
        "backgroundColor",
        "bold",
        "italic",
        "underline",
        "strikethrough",
        "smallCaps",
        "baselineOffset",
    }
)


@dataclass(frozen=True, slots=True)
class MarkdownImportVerification:
    brand_applied: bool
    content_preserved: bool
    structure_preserved: bool


@dataclass(frozen=True, slots=True)
class _RoleBrand:
    size: float
    color: str
    bold: bool
    italic: bool


class _ParagraphStructure(NamedTuple):
    kind: str
    level: int
    ordered: bool
    start: int
    list_group: int
    checked: bool | None


class _TextRun(NamedTuple):
    content: str
    style: dict[str, Any]


class _NamedStyle(NamedTuple):
    text: dict[str, Any]
    paragraph: dict[str, Any]


def verify_markdown_import(
    document: dict[str, Any],
    expected: MarkdownImportManifest,
) -> MarkdownImportVerification:
    observed = _observed_blocks(document, expected)
    return MarkdownImportVerification(
        content_preserved=_content_signature(observed) == _content_signature(expected.blocks),
        structure_preserved=(
            _topology_matches(document, expected) and _structure_matches(observed, expected.blocks)
        ),
        brand_applied=not expected.branded or _brand_matches(document, expected, observed),
    )


def _content_signature(
    blocks: Iterable[MarkdownImportBlock],
) -> tuple[tuple[str, object, tuple[tuple[str, str], ...]], ...]:
    return tuple(
        (
            "table" if block.kind == "table" else "text",
            block.rows if block.kind == "table" else block.text,
            _canonical_links(block),
        )
        for block in blocks
    )


def _structure_matches(
    observed: tuple[MarkdownImportBlock, ...],
    expected: tuple[MarkdownImportBlock, ...],
) -> bool:
    if len(observed) != len(expected):
        return False
    for actual, wanted in zip(observed, expected, strict=True):
        basic_structure = (actual.kind, actual.level, actual.ordered)
        expected_structure = (wanted.kind, wanted.level, wanted.ordered)
        ordered_structure_matches = not wanted.ordered or (
            actual.start == wanted.start and actual.list_group == wanted.list_group
        )
        if basic_structure != expected_structure or not ordered_structure_matches:
            return False
        if wanted.kind == "table" and tuple(map(len, actual.rows)) != tuple(map(len, wanted.rows)):
            return False
        if (
            actual.inline_signature != wanted.inline_signature
            or actual.cell_signatures != wanted.cell_signatures
            or actual.checked != wanted.checked
        ):
            return False
    return True


def _observed_blocks(
    document: dict[str, Any],
    expected: MarkdownImportManifest,
) -> tuple[MarkdownImportBlock, ...]:
    blocks: list[MarkdownImportBlock] = []
    list_groups: dict[tuple[str, int], int] = {}
    next_list_group = 1
    expected_title = (
        expected.blocks[0].text if expected.blocks and expected.blocks[0].kind == "title" else None
    )
    for document_tab in _document_tabs(document):
        lists = _list_definitions(document, document_tab)
        body = document_tab.get("body", {})
        content = body.get("content", []) if isinstance(body, dict) else []
        if not isinstance(content, list) or not all(isinstance(item, dict) for item in content):
            return ()
        for element in content:
            observed = _observed_body_element(
                element,
                lists=lists,
                expected_title=expected_title,
                is_first=not blocks,
                list_groups=list_groups,
                next_list_group=next_list_group,
            )
            if observed is None:
                continue
            if observed.list_group == next_list_group:
                next_list_group += 1
            blocks.append(observed)
    return tuple(blocks)


def _observed_body_element(
    element: dict[str, Any],
    *,
    lists: dict[str, Any],
    expected_title: str | None,
    is_first: bool,
    list_groups: dict[tuple[str, int], int],
    next_list_group: int,
) -> MarkdownImportBlock | None:
    table = element.get("table")
    if isinstance(table, dict):
        return _table_block(table)
    paragraph = element.get("paragraph")
    if not isinstance(paragraph, dict):
        return None
    return _observed_paragraph_block(
        paragraph,
        lists=lists,
        expected_title=expected_title,
        is_first=is_first,
        list_groups=list_groups,
        next_list_group=next_list_group,
    )


def _observed_paragraph_block(
    paragraph: dict[str, Any],
    *,
    lists: dict[str, Any],
    expected_title: str | None,
    is_first: bool,
    list_groups: dict[tuple[str, int], int],
    next_list_group: int,
) -> MarkdownImportBlock | None:
    runs = tuple(_text_runs(paragraph))
    text = "".join(content for content, _ in runs).removesuffix("\n")
    if not text and not _is_rule(paragraph):
        return None
    structure = _paragraph_structure(
        paragraph,
        text=text,
        runs=runs,
        lists=lists,
        expected_title=expected_title,
        is_first=is_first,
        list_groups=list_groups,
        next_list_group=next_list_group,
    )
    signature = _inline_signature(
        runs,
        ignore_bold=structure.kind in {"title", "heading"},
        ignore_italic=structure.kind == "quote",
    )
    return MarkdownImportBlock(
        kind=structure.kind,
        text=text,
        level=structure.level,
        ordered=structure.ordered,
        start=structure.start,
        list_group=structure.list_group,
        links=_styled_spans(runs, "link"),
        bold=_boolean_spans(runs, "bold"),
        italic=_boolean_spans(runs, "italic"),
        inline_code=tuple(
            content for content, style in runs if _font_family(style).casefold() in _CODE_FONTS
        ),
        inline_signature=signature,
        checked=structure.checked,
    )


def _paragraph_structure(
    paragraph: dict[str, Any],
    *,
    text: str,
    runs: tuple[_TextRun, ...],
    lists: dict[str, Any],
    expected_title: str | None,
    is_first: bool,
    list_groups: dict[tuple[str, int], int],
    next_list_group: int,
) -> _ParagraphStructure:
    if is_first and expected_title is not None and text == expected_title:
        return _ParagraphStructure(
            kind="title", level=1, ordered=False, start=1, list_group=0, checked=None
        )
    style = paragraph.get("paragraphStyle", {})
    style = style if isinstance(style, dict) else {}
    named_style = str(style.get("namedStyleType", "NORMAL_TEXT"))
    if named_style.startswith("HEADING_"):
        suffix = named_style.rsplit("_", maxsplit=1)[-1]
        return _ParagraphStructure(
            kind="heading",
            level=int(suffix) if suffix.isdigit() else 0,
            ordered=False,
            start=1,
            list_group=0,
            checked=None,
        )
    bullet = paragraph.get("bullet")
    if isinstance(bullet, dict):
        return _bullet_structure(
            bullet,
            lists=lists,
            list_groups=list_groups,
            next_list_group=next_list_group,
        )
    if _is_rule(paragraph):
        return _ParagraphStructure(
            kind="rule", level=0, ordered=False, start=1, list_group=0, checked=None
        )
    if runs and all(_font_family(run_style).casefold() in _CODE_FONTS for _, run_style in runs):
        return _ParagraphStructure(
            kind="code", level=0, ordered=False, start=1, list_group=0, checked=None
        )
    if _is_quote(style, runs):
        return _ParagraphStructure(
            kind="quote", level=0, ordered=False, start=1, list_group=0, checked=None
        )
    return _ParagraphStructure(
        kind="paragraph", level=0, ordered=False, start=1, list_group=0, checked=None
    )


def _bullet_structure(
    bullet: dict[str, Any],
    *,
    lists: dict[str, Any],
    list_groups: dict[tuple[str, int], int],
    next_list_group: int,
) -> _ParagraphStructure:
    list_id = bullet.get("listId")
    if not isinstance(list_id, str):
        return _ParagraphStructure(
            kind="invalid", level=0, ordered=False, start=1, list_group=0, checked=None
        )
    raw_level = bullet.get("nestingLevel", 0)
    level = raw_level if isinstance(raw_level, int) else 0
    properties = _nesting_properties(lists, list_id, level)
    symbol = str(properties.get("glyphSymbol", ""))
    ordered = str(properties.get("glyphType", "")).upper() in _ORDERED_GLYPH_TYPES
    list_key = (list_id, level)
    group = list_groups.get(list_key)
    start = 0
    if group is None:
        group = next_list_group
        list_groups[list_key] = group
        raw_start = properties.get("startNumber", 1)
        start = raw_start if isinstance(raw_start, int) else 1
    kind = "check" if symbol in {"☐", "☒"} else "list"
    checked = symbol == "☒" if kind == "check" else None
    return _ParagraphStructure(
        kind=kind,
        level=level,
        ordered=ordered,
        start=start,
        list_group=group if ordered else 0,
        checked=checked,
    )


def _document_tabs(document: dict[str, Any]) -> Iterable[dict[str, Any]]:
    tabs = document.get("tabs")
    if not isinstance(tabs, list):
        return ()
    found: list[dict[str, Any]] = []

    def walk(candidates: list[Any]) -> None:
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            document_tab = candidate.get("documentTab")
            if isinstance(document_tab, dict):
                found.append(document_tab)
            children = candidate.get("childTabs", [])
            if isinstance(children, list):
                walk(children)

    walk(tabs)
    return tuple(found)


def _topology_matches(document: dict[str, Any], expected: MarkdownImportManifest) -> bool:
    raw_tabs = document.get("tabs")
    if not isinstance(raw_tabs, list) or len(raw_tabs) != 1:
        return False
    raw_tab = raw_tabs[0]
    if not isinstance(raw_tab, dict) or raw_tab.get("childTabs") not in (None, []):
        return False
    tab = raw_tab.get("documentTab")
    if not isinstance(tab, dict) or tab.get("positionedObjects") not in (None, {}):
        return False
    return _tab_topology_matches(document, tab, expected)


def _tab_topology_matches(
    document: dict[str, Any], tab: dict[str, Any], expected: MarkdownImportManifest
) -> bool:
    headers = tab.get("headers")
    footers = tab.get("footers")
    if not expected.branded and (headers not in (None, {}) or footers not in (None, {})):
        return False
    logo_ids = _values_for_key(headers, "inlineObjectId") if expected.branded else ()
    if expected.branded and (len(logo_ids) != 1 or not isinstance(logo_ids[0], str)):
        return False
    inline_objects = tab.get("inlineObjects")
    if inline_objects is None:
        inline_objects = document.get("inlineObjects")
    if not isinstance(inline_objects, dict):
        inline_objects = {}
    if set(inline_objects) != set(logo_ids):
        return False
    body = tab.get("body", {})
    elements = body.get("content", []) if isinstance(body, dict) else None
    if not isinstance(elements, list):
        return False
    return _body_topology_matches(elements)


def _body_topology_matches(elements: list[Any]) -> bool:
    previous_was_table = False
    for index, element in enumerate(elements):
        if not isinstance(element, dict) or not _location_wrapper_matches(
            element, {"sectionBreak", "table", "paragraph"}
        ):
            return False
        if "sectionBreak" in element:
            if index != 0:
                return False
            previous_was_table = False
            continue
        if not _body_content_element_matches(element, previous_was_table=previous_was_table):
            return False
        previous_was_table = isinstance(element.get("table"), dict)
    return True


def _body_content_element_matches(element: dict[str, Any], *, previous_was_table: bool) -> bool:
    raw_table = element.get("table")
    if isinstance(raw_table, dict):
        table = {str(key): value for key, value in raw_table.items()}
        return _table_topology_matches(table)
    raw_paragraph = element.get("paragraph")
    if not isinstance(raw_paragraph, dict):
        return False
    paragraph = {str(key): value for key, value in raw_paragraph.items()}
    return _paragraph_topology_matches(paragraph) and bool(
        _paragraph_text(paragraph) or _is_rule(paragraph) or previous_was_table
    )


def _paragraph_topology_matches(paragraph: dict[Any, Any]) -> bool:
    elements = paragraph.get("elements", [])
    return isinstance(elements, list) and all(
        isinstance(element, dict)
        and _location_wrapper_matches(element, {"textRun"})
        and isinstance(element["textRun"], dict)
        and isinstance(element["textRun"].get("content"), str)
        and set(element["textRun"]) <= {"content", "textStyle"}
        and isinstance(element["textRun"].get("textStyle", {}), dict)
        for element in elements
    )


def _location_wrapper_matches(element: object, variants: set[str]) -> bool:
    if not isinstance(element, dict):
        return False
    fields = {key: value for key, value in element.items() if isinstance(key, str)}
    if len(fields) != len(element):
        return False
    locations = {"startIndex", "endIndex"}
    if len(set(fields) - locations) != 1 or not (set(fields) - locations) <= variants:
        return False
    for key in locations & fields.keys():
        value = fields[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return False
    start = fields.get("startIndex")
    end = fields.get("endIndex")
    return not (isinstance(start, int) and isinstance(end, int) and end <= start)


def _table_topology_matches(
    table: dict[str, Any],
) -> bool:
    rows = table.get("tableRows", [])
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict):
            return False
        cells = row.get("tableCells", [])
        if not isinstance(cells, list) or not all(
            _table_cell_topology_matches(cell) for cell in cells
        ):
            return False
    return True


def _table_cell_topology_matches(cell: object) -> bool:
    if not isinstance(cell, dict):
        return False
    # A merged cell can retain the same visible text and row count while
    # changing the table's meaning. Imported Markdown has no spans.
    if cell.get("rowSpan", 1) != 1 or cell.get("columnSpan", 1) != 1:
        return False
    content = cell.get("content", [])
    return isinstance(content, list) and all(
        _table_cell_element_matches(element) for element in content
    )


def _table_cell_element_matches(element: object) -> bool:
    if not isinstance(element, dict) or not _location_wrapper_matches(element, {"paragraph"}):
        return False
    paragraph = element.get("paragraph")
    return isinstance(paragraph, dict) and _paragraph_topology_matches(paragraph)


def _list_definitions(document: dict[str, Any], document_tab: dict[str, Any]) -> dict[str, Any]:
    local = document_tab.get("lists")
    if isinstance(local, dict):
        return local
    legacy = document.get("lists")
    return legacy if isinstance(legacy, dict) else {}


def _nesting_properties(lists: dict[str, Any], list_id: str, level: int) -> dict[str, Any]:
    definition = lists.get(list_id, {})
    properties = definition.get("listProperties", {}) if isinstance(definition, dict) else {}
    levels = properties.get("nestingLevels", []) if isinstance(properties, dict) else []
    if not isinstance(levels, list) or not 0 <= level < len(levels):
        return {}
    value = levels[level]
    return value if isinstance(value, dict) else {}


def _paragraph_text(paragraph: dict[str, Any]) -> str:
    return "".join(content for content, _ in _text_runs(paragraph)).removesuffix("\n")


def _text_runs(paragraph: dict[str, Any]) -> Iterable[_TextRun]:
    elements = paragraph.get("elements", [])
    if not isinstance(elements, list):
        return ()
    runs: list[_TextRun] = []
    for element in elements:
        text_run = element.get("textRun") if isinstance(element, dict) else None
        if not isinstance(text_run, dict) or not isinstance(text_run.get("content"), str):
            continue
        style = text_run.get("textStyle", {})
        runs.append(_TextRun(text_run["content"], style if isinstance(style, dict) else {}))
    for index in range(len(runs) - 1, -1, -1):
        content, style = runs[index]
        if content:
            runs[index] = _TextRun(content.removesuffix("\n"), style)
            break
    return tuple(run for run in runs if run.content)


def _inline_signature(
    runs: tuple[tuple[str, dict[str, Any]], ...],
    *,
    ignore_bold: bool = False,
    ignore_italic: bool = False,
) -> tuple[MarkdownImportInline, ...]:
    signature: list[MarkdownImportInline] = []
    for content, style in runs:
        link = style.get("link")
        url = (
            link.get("url") if isinstance(link, dict) and isinstance(link.get("url"), str) else None
        )
        value = MarkdownImportInline(
            text=content,
            bold=not ignore_bold and style.get("bold") is True,
            italic=not ignore_italic and style.get("italic") is True,
            code=_font_family(style).casefold() in _CODE_FONTS,
            url=url,
        )
        previous = signature[-1] if signature else None
        if previous is not None and (
            previous.bold,
            previous.italic,
            previous.code,
            previous.url,
        ) == (value.bold, value.italic, value.code, value.url):
            signature[-1] = MarkdownImportInline(
                text=previous.text + value.text,
                bold=value.bold,
                italic=value.italic,
                code=value.code,
                url=value.url,
            )
        else:
            signature.append(value)
    return tuple(signature)


def _canonical_links(block: MarkdownImportBlock) -> tuple[tuple[str, str], ...]:
    signatures = (
        tuple(cell for row in block.cell_signatures for cell in row)
        if block.kind == "table"
        else (block.inline_signature,)
    )
    links: list[tuple[str, str]] = []
    for signature in signatures:
        for inline in signature:
            if inline.url is None:
                continue
            if links and links[-1][1] == inline.url:
                links[-1] = (links[-1][0] + inline.text, inline.url)
            else:
                links.append((inline.text, inline.url))
    return tuple(links)


def _styled_spans(
    runs: tuple[tuple[str, dict[str, Any]], ...],
    field: str,
) -> tuple[tuple[str, str], ...]:
    spans: list[tuple[str, str]] = []
    for content, style in runs:
        value = style.get(field)
        if field == "link" and isinstance(value, dict) and isinstance(value.get("url"), str):
            spans.append((content, value["url"]))
    return tuple(spans)


def _boolean_spans(
    runs: tuple[tuple[str, dict[str, Any]], ...],
    field: str,
) -> tuple[str, ...]:
    return tuple(content for content, style in runs if style.get(field) is True)


def _font_family(style: dict[str, Any]) -> str:
    family = style.get("weightedFontFamily", {})
    value = family.get("fontFamily") if isinstance(family, dict) else None
    return value if isinstance(value, str) else ""


def _is_quote(
    paragraph_style: dict[str, Any],
    runs: tuple[tuple[str, dict[str, Any]], ...],
) -> bool:
    indent = paragraph_style.get("indentStart")
    return (
        _dimension_points(indent) > 0
        and bool(runs)
        and all(style.get("italic") is True for _, style in runs)
    )


def _is_rule(paragraph: dict[str, Any]) -> bool:
    style = paragraph.get("paragraphStyle", {})
    return isinstance(style, dict) and isinstance(style.get("borderBottom"), dict)


def _table_block(table: dict[str, Any]) -> MarkdownImportBlock:
    runs = tuple(run for paragraph in _table_paragraphs(table) for run in _text_runs(paragraph))
    cell_signatures = tuple(
        tuple(_cell_signatures(cell, ignore_bold=row_index == 0) for cell in _table_cells(row))
        for row_index, row in enumerate(_table_row_values(table))
    )
    return MarkdownImportBlock(
        kind="table",
        rows=_table_rows(table),
        links=_styled_spans(runs, "link"),
        bold=_boolean_spans(runs, "bold"),
        italic=_boolean_spans(runs, "italic"),
        inline_code=tuple(
            content for content, style in runs if _font_family(style).casefold() in _CODE_FONTS
        ),
        cell_signatures=cell_signatures,
    )


def _table_rows(table: dict[str, Any]) -> tuple[tuple[str, ...], ...]:
    rows = table.get("tableRows", [])
    if not isinstance(rows, list):
        return ()
    result: list[tuple[str, ...]] = []
    for row in rows:
        cells = row.get("tableCells", []) if isinstance(row, dict) else []
        if not isinstance(cells, list):
            return ()
        result.append(tuple(_cell_text(cell) for cell in cells))
    return tuple(result)


def _table_row_values(table: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    rows = table.get("tableRows", [])
    return tuple(row for row in rows if isinstance(row, dict)) if isinstance(rows, list) else ()


def _table_cells(row: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    cells = row.get("tableCells", [])
    return (
        tuple(cell for cell in cells if isinstance(cell, dict)) if isinstance(cells, list) else ()
    )


def _cell_signatures(
    cell: dict[str, Any],
    *,
    ignore_bold: bool,
) -> tuple[MarkdownImportInline, ...]:
    paragraphs = tuple(_cell_paragraphs(cell))
    combined: list[MarkdownImportInline] = []
    for index, paragraph in enumerate(paragraphs):
        if index:
            combined.append(MarkdownImportInline("\n"))
        combined.extend(_inline_signature(tuple(_text_runs(paragraph)), ignore_bold=ignore_bold))
    return _coalesce_signature(tuple(combined))


def _cell_paragraphs(cell: dict[str, Any]) -> Iterable[dict[str, Any]]:
    content = cell.get("content", [])
    if not isinstance(content, list):
        return ()
    return tuple(
        paragraph
        for element in content
        if isinstance(element, dict) and isinstance((paragraph := element.get("paragraph")), dict)
    )


def _coalesce_signature(
    values: tuple[MarkdownImportInline, ...],
) -> tuple[MarkdownImportInline, ...]:
    result: list[MarkdownImportInline] = []
    for value in values:
        previous = result[-1] if result else None
        if previous is not None and (
            previous.bold,
            previous.italic,
            previous.code,
            previous.url,
        ) == (value.bold, value.italic, value.code, value.url):
            result[-1] = MarkdownImportInline(
                previous.text + value.text,
                bold=value.bold,
                italic=value.italic,
                code=value.code,
                url=value.url,
            )
        else:
            result.append(value)
    return tuple(result)


def _table_paragraphs(
    table: dict[str, Any],
) -> Iterable[dict[Any, Any]]:
    rows = table.get("tableRows", [])
    if not isinstance(rows, list):
        return ()
    paragraphs: list[dict[Any, Any]] = []
    for row in rows:
        cells = row.get("tableCells", []) if isinstance(row, dict) else []
        if not isinstance(cells, list):
            return ()
        for cell in cells:
            cell_paragraphs = _table_cell_paragraphs(cell)
            if cell_paragraphs is None:
                return ()
            paragraphs.extend(cell_paragraphs)
    return tuple(paragraphs)


def _table_cell_paragraphs(cell: object) -> tuple[dict[Any, Any], ...] | None:
    content = cell.get("content", []) if isinstance(cell, dict) else []
    if not isinstance(content, list):
        return None
    return tuple(
        paragraph
        for element in content
        if isinstance(element, dict) and isinstance((paragraph := element.get("paragraph")), dict)
    )


def _cell_text(cell: object) -> str:
    content = cell.get("content", []) if isinstance(cell, dict) else []
    if not isinstance(content, list):
        return ""
    paragraphs: list[str] = []
    for element in content:
        if not isinstance(element, dict):
            continue
        raw_paragraph = element.get("paragraph")
        if not isinstance(raw_paragraph, dict):
            continue
        paragraph = {str(key): value for key, value in raw_paragraph.items()}
        text = _paragraph_text(paragraph)
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _brand_matches(
    document: dict[str, Any],
    expected: MarkdownImportManifest,
    observed: tuple[MarkdownImportBlock, ...],
) -> bool:
    tabs = tuple(_document_tabs(document))
    if len(tabs) != 1 or len(observed) != len(expected.blocks):
        return False
    tokens = CURRENT_BRAND_MANIFEST.document
    if not all(
        _document_style_matches(tab, tokens.horizontal_margin_pt, tokens.vertical_margin_pt)
        for tab in tabs
    ):
        return False
    if not all(_tab_has_logo(document, tab, header_title=expected.header_title) for tab in tabs):
        return False
    if not all(_segment_has_exact_footer(tab.get("footers")) for tab in tabs):
        return False
    return _body_brand_matches(document, tabs[0], expected)


def _document_style_matches(tab: dict[str, Any], horizontal: float, vertical: float) -> bool:
    style = tab.get("documentStyle", {})
    return isinstance(style, dict) and all(
        (
            abs(_dimension_points(style.get("marginLeft")) - horizontal) <= _TOLERANCE,
            abs(_dimension_points(style.get("marginRight")) - horizontal) <= _TOLERANCE,
            abs(_dimension_points(style.get("marginTop")) - vertical) <= _TOLERANCE,
            abs(_dimension_points(style.get("marginBottom")) - vertical) <= _TOLERANCE,
        )
    )


def markdown_import_brand_diagnostics(
    document: dict[str, Any], expected: MarkdownImportManifest
) -> dict[str, bool]:
    checks = {"import_brand_requested": expected.branded}
    if not expected.branded:
        return checks
    tabs = tuple(_document_tabs(document))
    observed = _observed_blocks(document, expected)
    checks.update(
        import_single_tab=len(tabs) == 1, import_block_count=len(observed) == len(expected.blocks)
    )
    if len(tabs) != 1 or not checks["import_block_count"]:
        return checks
    tab = tabs[0]
    tokens = CURRENT_BRAND_MANIFEST.document
    checks["import_margins"] = _document_style_matches(
        tab, tokens.horizontal_margin_pt, tokens.vertical_margin_pt
    )
    if not checks["import_margins"]:
        return checks
    checks["import_header_shape"] = (
        len(_exact_header_logo_ids(tab.get("headers"), header_title=expected.header_title)) == 1
    )
    embedded = _tab_logo_object(document, tab, header_title=expected.header_title)
    checks["import_logo_object"] = embedded is not None
    if embedded is None:
        return checks
    checks["import_logo_title_exact"] = embedded.get("title") == "Sarj logo"
    checks["import_logo_description_exact"] = (
        embedded.get("description") == trusted_document_logo_description()
    )
    image_properties = embedded.get("imageProperties", {})
    checks["import_logo_image_properties_valid"] = isinstance(image_properties, dict)
    if isinstance(image_properties, dict):
        checks["import_logo_source_uri_absent"] = image_properties.get("sourceUri") is None
        checks["import_logo_source_uri_exact"] = (
            image_properties.get("sourceUri") == CURRENT_BRAND_MANIFEST.assets.logo_uri
        )
    checks["import_logo_metadata"] = _logo_metadata_matches(embedded)
    if not checks["import_logo_metadata"]:
        return checks
    checks["import_logo_dimensions"] = _logo_dimensions_match(embedded)
    if not checks["import_logo_dimensions"]:
        return checks
    if expected.header_title is not None:
        checks["import_header_title_style"] = _header_title_brand_matches(document, tab)
        if not checks["import_header_title_style"]:
            checks.update(_header_title_style_diagnostics(document, tab))
            return checks
    checks["import_footer_shape"] = _segment_has_exact_footer(tab.get("footers"))
    if not checks["import_footer_shape"]:
        return checks
    checks["import_body_style"] = _body_brand_matches(document, tab, expected)
    return checks


def _dimension_points(value: object) -> float:
    if not isinstance(value, dict) or value.get("unit") != "PT":
        return 0
    magnitude = value.get("magnitude")
    return float(magnitude) if isinstance(magnitude, int | float) else 0


def _tab_has_logo(
    document: dict[str, Any], tab: dict[str, Any], *, header_title: str | None = None
) -> bool:
    embedded = _tab_logo_object(document, tab, header_title=header_title)
    return (
        embedded is not None
        and _logo_metadata_matches(embedded)
        and _logo_dimensions_match(embedded)
        and (header_title is None or _header_title_brand_matches(document, tab))
    )


def _header_title_brand_matches(document: dict[str, Any], tab: dict[str, Any]) -> bool:
    tokens = CURRENT_BRAND_MANIFEST.document.header
    segment = _only_segment(tab.get("headers"))
    if tokens is None or segment is None:
        return False
    content = segment.get("content")
    if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict):
        return False
    paragraph = content[0].get("paragraph")
    if not isinstance(paragraph, dict):
        return False
    style = paragraph.get("paragraphStyle")
    if not isinstance(style, dict) or style.get("namedStyleType", "NORMAL_TEXT") != "NORMAL_TEXT":
        return False
    inherited = _header_named_style(document, tab)
    if inherited is None:
        return False
    effective = inherited.paragraph | style
    if not (
        effective.get("alignment") == tokens.alignment
        and effective.get("lineSpacing") == tokens.line_spacing_percent
        and _header_dimension_exact(effective.get("spaceAbove"), tokens.space_above_pt)
        and _header_dimension_exact(effective.get("spaceBelow"), tokens.space_below_pt)
        and effective.get("keepWithNext") is False
    ):
        return False
    title_runs = []
    for element in paragraph.get("elements", []):
        if "inlineObjectElement" in element:
            break
        title_runs.append(element["textRun"].get("textStyle", {}))
    return bool(title_runs) and all(
        _header_run_style_matches(run, inherited.text) for run in title_runs
    )


def _header_title_style_diagnostics(
    document: dict[str, Any], tab: dict[str, Any]
) -> dict[str, bool]:
    # Called only after exact header shape and logo proof. Never inspect private
    # text or expose provider keys; downstream checks remain absent if unchecked.
    tokens = CURRENT_BRAND_MANIFEST.document.header
    segment = _only_segment(tab.get("headers"))
    if tokens is None or segment is None:
        return {}
    paragraph = segment["content"][0]["paragraph"]
    style = paragraph.get("paragraphStyle")
    checks = {"import_header_paragraph_style_known": isinstance(style, dict)}
    if not isinstance(style, dict):
        return checks
    checks["import_header_named_type_exact"] = (
        style.get("namedStyleType", "NORMAL_TEXT") == "NORMAL_TEXT"
    )
    if not checks["import_header_named_type_exact"]:
        return checks
    inherited = _header_named_style(document, tab)
    checks["import_header_named_identity_known"] = inherited is not None
    if inherited is None:
        return checks
    effective = inherited.paragraph | style
    checks.update(
        import_header_alignment_exact=effective.get("alignment") == tokens.alignment,
        import_header_line_spacing_exact=effective.get("lineSpacing")
        == tokens.line_spacing_percent,
        import_header_space_above_exact=_header_dimension_exact(
            effective.get("spaceAbove"), tokens.space_above_pt
        ),
        import_header_space_below_exact=_header_dimension_exact(
            effective.get("spaceBelow"), tokens.space_below_pt
        ),
        import_header_keep_next_exact=effective.get("keepWithNext") is False,
    )
    paragraph_matches = all(checks.values())
    checks.update(
        import_header_space_above_unit_only=effective.get("spaceAbove") == {"unit": "PT"},
        import_header_space_below_unit_only=effective.get("spaceBelow") == {"unit": "PT"},
        import_header_keep_next_absent="keepWithNext" not in effective,
    )
    if not paragraph_matches:
        return checks
    for element in paragraph["elements"]:
        if "inlineObjectElement" in element:
            break
        run = element["textRun"].get("textStyle", {})
        if not _header_run_style_matches(run, inherited.text):
            checks.update(_header_run_style_diagnostics(run, inherited.text))
            break
    return checks


def _header_run_style_diagnostics(style: object, inherited: dict[str, Any]) -> dict[str, bool]:
    checks = {"import_header_first_failed_run_only": True}
    tokens = CURRENT_BRAND_MANIFEST.document.header
    checks["import_header_run_style_known"] = isinstance(style, dict)
    if tokens is None or not isinstance(style, dict):
        return checks
    effective = inherited | style
    checks.update(
        import_header_run_fields_known=not bool(set(effective) - _HEADER_RUN_STYLE_FIELDS),
        import_header_run_font_exact=_font_family(effective) == tokens.font_family,
        import_header_run_size_exact=_header_dimension_exact(
            effective.get("fontSize"), tokens.size_pt
        ),
        import_header_run_bold_exact=effective.get("bold") is tokens.bold,
        import_header_run_effects_exact=all(
            key not in effective or effective[key] is False
            for key in ("italic", "underline", "strikethrough", "smallCaps")
        ),
        import_header_run_baseline_exact="baselineOffset" not in effective
        or effective["baselineOffset"] == "NONE",
        import_header_run_background_exact="backgroundColor" not in effective
        or effective["backgroundColor"] == {},
    )
    # The existing full matcher is the authority. Isolate color and weight by
    # supplying only fixed known-good unrelated properties, never provider text.
    canonical = {
        "weightedFontFamily": {"fontFamily": tokens.font_family},
        "fontSize": {"magnitude": tokens.size_pt, "unit": "PT"},
        "foregroundColor": {
            "color": {
                "rgbColor": dict(
                    zip(
                        ("red", "green", "blue"),
                        (int(tokens.color[index : index + 2], 16) / 255 for index in (1, 3, 5)),
                        strict=True,
                    )
                )
            }
        },
        "bold": tokens.bold,
    }
    checks["import_header_run_color_exact"] = _header_run_style_matches(
        canonical | {"foregroundColor": effective.get("foregroundColor")}, {}
    )
    checks["import_header_run_family_weight_exact"] = _header_run_style_matches(
        canonical | {"weightedFontFamily": effective.get("weightedFontFamily")}, {}
    )
    return checks


def _header_named_style(document: dict[str, Any], tab: dict[str, Any]) -> _NamedStyle | None:
    if "namedStyles" not in tab and "namedStyles" not in document:
        return _NamedStyle({}, {})
    named = tab.get("namedStyles", document.get("namedStyles"))
    styles = named.get("styles") if isinstance(named, dict) else None
    if not isinstance(styles, list) or len(styles) > _MAX_HEADER_NAMED_STYLES:
        return None
    matches = [
        style
        for style in styles
        if isinstance(style, dict) and style.get("namedStyleType") == "NORMAL_TEXT"
    ]
    if len(matches) != 1:
        return None
    text, paragraph = matches[0].get("textStyle", {}), matches[0].get("paragraphStyle", {})
    return (
        _NamedStyle(text, paragraph)
        if isinstance(text, dict) and isinstance(paragraph, dict)
        else None
    )


def _header_dimension_exact(value: object, expected: int) -> bool:
    # Docs emits explicit zero dimensions as {unit: PT}: ProtoJSON may omit a
    # default-valued scalar. Missing dimensions still mean unknown/inherited.
    if expected == 0 and value == {"unit": "PT"}:
        return True
    return (
        isinstance(value, dict)
        and set(value) == {"unit", "magnitude"}
        and value.get("unit") == "PT"
        and type(value.get("magnitude")) in {int, float}
        and value.get("magnitude") == expected
    )


def _header_run_style_matches(style: object, inherited: dict[str, Any]) -> bool:
    tokens = CURRENT_BRAND_MANIFEST.document.header
    if tokens is None or not isinstance(style, dict):
        return False
    effective = inherited | style
    if not _header_effective_style_fields_match(effective):
        return False
    family = effective.get("weightedFontFamily")
    if not isinstance(family, dict) or set(family) - {"fontFamily", "weight"}:
        return False
    weight = family.get("weight", _NORMAL_FONT_WEIGHT)
    if (
        type(weight) is not int
        or not _NORMAL_FONT_WEIGHT <= weight <= _BOLD_FONT_WEIGHT
        or weight % _FONT_WEIGHT_STEP
    ):
        return False
    color = effective.get("foregroundColor")
    if not _header_color_shape_matches(color):
        return False
    return (
        _font_family(effective) == tokens.font_family
        and _header_dimension_exact(effective.get("fontSize"), tokens.size_pt)
        and _color_matches(color, tokens.color)
        and effective.get("bold") is tokens.bold
    )


def _header_effective_style_fields_match(effective: dict[str, Any]) -> bool:
    if set(effective) - _HEADER_RUN_STYLE_FIELDS:
        return False
    if "backgroundColor" in effective and effective["backgroundColor"] != {}:
        return False
    if any(
        key in effective and effective[key] is not False
        for key in ("italic", "underline", "strikethrough", "smallCaps")
    ):
        return False
    return "baselineOffset" not in effective or effective["baselineOffset"] == "NONE"


def _header_color_shape_matches(color: object) -> bool:
    if not isinstance(color, dict) or set(color) != {"color"}:
        return False
    nested = color.get("color")
    if not isinstance(nested, dict) or set(nested) != {"rgbColor"}:
        return False
    rgb = nested.get("rgbColor")
    return (
        isinstance(rgb, dict)
        and not set(rgb) - {"red", "green", "blue"}
        and all(_header_color_channel_matches(value) for value in rgb.values())
    )


def _header_color_channel_matches(value: object) -> bool:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return False
    return 0 <= value <= 1


def _tab_logo_object(
    document: dict[str, Any], tab: dict[str, Any], *, header_title: str | None = None
) -> dict[str, Any] | None:
    object_ids = _exact_header_logo_ids(tab.get("headers"), header_title=header_title)
    if len(object_ids) != 1 or not isinstance(object_ids[0], str):
        return None
    inline_objects = tab.get("inlineObjects")
    if not isinstance(inline_objects, dict):
        inline_objects = document.get("inlineObjects")
    if not isinstance(inline_objects, dict):
        return None
    candidate = inline_objects.get(object_ids[0])
    properties = candidate.get("inlineObjectProperties") if isinstance(candidate, dict) else None
    embedded = properties.get("embeddedObject") if isinstance(properties, dict) else None
    if not isinstance(embedded, dict):
        return None
    return embedded


def _logo_metadata_matches(embedded: dict[str, Any]) -> bool:
    if (
        embedded.get("title") != "Sarj logo"
        or embedded.get("description") != trusted_document_logo_description()
    ):
        return False
    image_properties = embedded.get("imageProperties", {})
    if not isinstance(image_properties, dict):
        return False
    source_uri = image_properties.get("sourceUri")
    return source_uri is None or source_uri == CURRENT_BRAND_MANIFEST.assets.logo_uri


def _logo_dimensions_match(embedded: dict[str, Any]) -> bool:
    size = embedded.get("size", {})
    width = _dimension_points(size.get("width")) if isinstance(size, dict) else 0
    height = _dimension_points(size.get("height")) if isinstance(size, dict) else 0
    header = CURRENT_BRAND_MANIFEST.document.header
    asset_width = CURRENT_BRAND_MANIFEST.assets.logo_pixel_width
    asset_height = CURRENT_BRAND_MANIFEST.assets.logo_pixel_height
    if header is None or asset_width is None or asset_height is None:
        return False
    scale = min(header.logo_width_pt / asset_width, header.logo_height_pt / asset_height)
    return (
        abs(width - asset_width * scale) <= header.logo_size_tolerance_pt
        and abs(height - asset_height * scale) <= header.logo_size_tolerance_pt
    )


def _exact_header_logo_ids(
    segments: object, *, header_title: str | None = None
) -> tuple[object, ...]:
    segment = _only_segment(segments)
    if segment is None:
        return ()
    content = segment.get("content")
    if not isinstance(content, list) or len(content) != 1:
        return ()
    element = content[0]
    if not isinstance(element, dict):
        return ()
    if header_title is None and set(element) == {"inlineObjectId"}:
        return (element["inlineObjectId"],)
    if not _location_wrapper_matches(element, {"paragraph"}):
        return ()
    paragraph = element.get("paragraph")
    if not isinstance(paragraph, dict) or set(paragraph) - {"elements", "paragraphStyle"}:
        return ()
    elements = paragraph.get("elements", [])
    if not isinstance(elements, list):
        return ()
    identifiers = _values_for_key(elements, "inlineObjectId")
    return (
        identifiers
        if len(identifiers) == 1
        and _header_elements_are_exact(elements, outer=element, header_title=header_title)
        else ()
    )


def _header_elements_are_exact(
    elements: list[Any], *, outer: dict[str, Any] | None = None, header_title: str | None = None
) -> bool:
    if not elements or len(elements) > _MAX_HEADER_ELEMENTS:
        return False
    cursor: int | None = outer.get("startIndex") if outer is not None else None
    sequence = _HeaderSequence(before_logo="", seen_logo=False)
    for index, element in enumerate(elements):
        parsed = _header_element_value(element)
        if parsed is None:
            return False
        variant, value = parsed
        advanced = _advance_header_sequence(
            sequence,
            variant=variant,
            value=value,
            is_last=index == len(elements) - 1,
            header_title=header_title,
        )
        if advanced is None:
            return False
        sequence = advanced
        width = 1 if variant == "inlineObjectElement" else utf16_length(value)
        position = _segment_next_cursor(element, cursor, width)
        if position is None:
            return False
        cursor = position.value
    paragraph_end = outer.get("endIndex") if outer is not None else None
    return sequence.seen_logo and (
        paragraph_end is None or cursor is None or paragraph_end == cursor
    )


@dataclass(frozen=True, slots=True)
class _HeaderSequence:
    before_logo: str
    seen_logo: bool


def _advance_header_sequence(
    sequence: _HeaderSequence,
    *,
    variant: str,
    value: str,
    is_last: bool,
    header_title: str | None,
) -> _HeaderSequence | None:
    if variant == "inlineObjectElement":
        expected_prefix = header_title + HEADER_TITLE_SEPARATOR if header_title is not None else ""
        if sequence.seen_logo or sequence.before_logo != expected_prefix:
            return None
        return _HeaderSequence(before_logo=sequence.before_logo, seen_logo=True)
    if sequence.seen_logo:
        return sequence if value == "\n" and is_last else None
    return _HeaderSequence(before_logo=sequence.before_logo + value, seen_logo=False)


class _SegmentCursor(NamedTuple):
    value: int | None


def _header_element_value(element: object) -> tuple[str, str] | None:
    variant = (
        "inlineObjectElement"
        if isinstance(element, dict) and "inlineObjectElement" in element
        else "textRun"
    )
    if not isinstance(element, dict) or not _location_wrapper_matches(element, {variant}):
        return None
    payload = element.get(variant)
    field = "inlineObjectId" if variant == "inlineObjectElement" else "content"
    if not isinstance(payload, dict) or not _plain_segment_payload_known(payload, field):
        return None
    value = payload.get(field)
    return (variant, value) if isinstance(value, str) and value else None


def _segment_next_cursor(
    element: dict[str, Any], cursor: int | None, width: int
) -> _SegmentCursor | None:
    start, end = element.get("startIndex"), element.get("endIndex")
    if (start is not None and not isinstance(start, int)) or (
        end is not None and not isinstance(end, int)
    ):
        return None
    if start is not None and cursor is not None and start != cursor:
        return None
    effective_start = start if start is not None else cursor
    if end is not None and effective_start is not None and end != effective_start + width:
        return None
    next_cursor = (
        end if end is not None else effective_start + width if effective_start is not None else None
    )
    return _SegmentCursor(next_cursor)


def _segment_has_exact_footer(
    segments: object,
) -> bool:
    paragraph_parts = _footer_paragraph_elements(segments)
    if paragraph_parts is None:
        return False
    outer, elements = paragraph_parts
    tokens: list[tuple[str, str]] = []
    cursor: int | None = outer.get("startIndex")
    for index, element in enumerate(elements):
        token_parts = _footer_element_token(element)
        if token_parts is None:
            return False
        token, width = token_parts
        if token == ("newline", ""):
            if index != len(elements) - 1 or tokens != [
                ("text", "Sarj ·"),
                ("auto", "PAGE_NUMBER"),
            ]:
                return False
        else:
            tokens.append(token)
        position = _segment_next_cursor(element, cursor, width)
        if position is None:
            return False
        cursor = position.value
    paragraph_end = outer.get("endIndex")
    if paragraph_end is not None and cursor is not None and paragraph_end != cursor:
        return False
    return tokens == [("text", "Sarj ·"), ("auto", "PAGE_NUMBER")]


def _footer_paragraph_elements(segments: object) -> tuple[dict[str, Any], list[Any]] | None:
    segment = _only_segment(segments)
    if segment is None:
        return None
    content = segment.get("content")
    if not isinstance(content, list) or len(content) != 1:
        return None
    outer = content[0]
    if not _location_wrapper_matches(outer, {"paragraph"}):
        return None
    paragraph = outer.get("paragraph")
    if not isinstance(paragraph, dict) or set(paragraph) - {"elements", "paragraphStyle"}:
        return None
    elements = paragraph.get("elements", [])
    return (outer, elements) if isinstance(elements, list) else None


def _footer_element_token(element: object) -> tuple[tuple[str, str], int] | None:
    if not isinstance(element, dict) or not _location_wrapper_matches(
        element, {"textRun", "autoText"}
    ):
        return None
    text_run = element.get("textRun")
    if (
        isinstance(text_run, dict)
        and _plain_segment_payload_known(text_run, "content")
        and isinstance(value := text_run.get("content"), str)
    ):
        if value == "\n":
            return (("newline", ""), utf16_length(value))
        if any(character in value for character in "\n\r\v\f\u2028\u2029"):
            return None
        return (("text", " ".join(value.split())), utf16_length(value))
    auto_text = element.get("autoText")
    if (
        isinstance(auto_text, dict)
        and _plain_segment_payload_known(auto_text, "type")
        and auto_text.get("type") == "PAGE_NUMBER"
    ):
        return (("auto", "PAGE_NUMBER"), 1)
    return None


def _plain_segment_payload_known(payload: object, field: str) -> bool:
    if not isinstance(payload, dict) or set(payload) - {field, "textStyle"}:
        return False
    style = payload.get("textStyle", {})
    return isinstance(style, dict) and not set(style) - {
        "bold",
        "italic",
        "underline",
        "strikethrough",
        "smallCaps",
        "backgroundColor",
        "foregroundColor",
        "fontSize",
        "weightedFontFamily",
        "baselineOffset",
    }


def _only_segment(segments: object) -> dict[str, Any] | None:
    if not isinstance(segments, dict) or len(segments) != 1:
        return None
    segment = next(iter(segments.values()))
    return (
        {str(key): value for key, value in segment.items()} if isinstance(segment, dict) else None
    )


def _body_brand_matches(
    document: dict[str, Any],
    tab: dict[str, Any],
    expected: MarkdownImportManifest,
) -> bool:
    body = tab.get("body", {})
    elements = body.get("content", []) if isinstance(body, dict) else []
    block_index = 0
    for element in elements if isinstance(elements, list) else []:
        if not isinstance(element, dict) or "sectionBreak" in element:
            continue
        if not _is_observed_body_element(element):
            continue
        if block_index >= len(expected.blocks):
            return False
        if not _body_element_brand_matches(document, tab, element, expected.blocks[block_index]):
            return False
        block_index += 1
    return block_index == len(expected.blocks)


def _is_observed_body_element(element: dict[str, Any]) -> bool:
    if isinstance(element.get("table"), dict):
        return True
    paragraph = element.get("paragraph")
    return isinstance(paragraph, dict) and bool(_paragraph_text(paragraph) or _is_rule(paragraph))


def _body_element_brand_matches(
    document: dict[str, Any],
    tab: dict[str, Any],
    element: dict[str, Any],
    expected: MarkdownImportBlock,
) -> bool:
    table = element.get("table")
    if isinstance(table, dict):
        return expected.kind == "table" and _table_brand_matches(document, tab, table, expected)
    paragraph = element.get("paragraph")
    return isinstance(paragraph, dict) and _paragraph_brand_matches(
        document, tab, paragraph, expected
    )


def _table_brand_matches(
    document: dict[str, Any],
    tab: dict[str, Any],
    table: dict[str, Any],
    expected: MarkdownImportBlock,
) -> bool:
    rows = _table_row_values(table)
    if len(rows) != len(expected.cell_signatures):
        return False
    for row_index, (row, expected_row) in enumerate(
        zip(rows, expected.cell_signatures, strict=True)
    ):
        cells = _table_cells(row)
        if len(cells) != len(expected_row):
            return False
        for cell, expected_cell in zip(cells, expected_row, strict=True):
            paragraphs = tuple(_cell_paragraphs(cell))
            if len(paragraphs) != 1:
                return False
            pseudo_block = MarkdownImportBlock(
                kind="paragraph",
                text="".join(inline.text for inline in expected_cell),
                inline_signature=expected_cell,
            )
            if not _paragraph_brand_matches(
                document,
                tab,
                paragraphs[0],
                pseudo_block,
                force_bold=row_index == 0,
            ):
                return False
    return True


def _paragraph_brand_matches(
    document: dict[str, Any],
    tab: dict[str, Any],
    paragraph: dict[str, Any],
    expected: MarkdownImportBlock,
    *,
    force_bold: bool = False,
) -> bool:
    if expected.kind == "rule":
        return _rule_brand_matches(paragraph)
    paragraph_style = paragraph.get("paragraphStyle", {})
    if not isinstance(paragraph_style, dict):
        return False
    named_type = str(paragraph_style.get("namedStyleType", "NORMAL_TEXT"))
    named_text_style, named_paragraph_style = _named_style(document, tab, named_type)
    if not _paragraph_spacing_matches(paragraph_style, named_paragraph_style):
        return False
    runs = tuple(_text_runs(paragraph))
    role = _RoleBrand(
        size=_role_size(expected),
        color=_role_color(expected),
        bold=force_bold or expected.kind in {"title", "heading"},
        italic=expected.kind == "quote",
    )
    return bool(runs) and _runs_match_brand(runs, expected, named_text_style, role)


def _runs_match_brand(
    runs: tuple[tuple[str, dict[str, Any]], ...],
    expected: MarkdownImportBlock,
    inherited: dict[str, Any],
    role: _RoleBrand,
) -> bool:
    tokens = CURRENT_BRAND_MANIFEST.document
    character_offset = 0
    for content, style in runs:
        family = _effective_font_family(style, inherited)
        is_code = family.casefold() in _CODE_FONTS
        expected_code = _expected_code_for_range(
            expected,
            start=character_offset,
            end=character_offset + len(content),
        )
        character_offset += len(content)
        if expected_code is None or is_code != expected_code:
            return False
        if (
            not is_code
            and family != tokens.font_family
            and not (family == tokens.complex_script_font_family and first_strong_is_rtl(content))
        ):
            return False
        size = _effective_dimension_points(style, inherited, "fontSize")
        if abs(size - role.size) > _TOLERANCE:
            return False
        color = style.get("foregroundColor", inherited.get("foregroundColor"))
        run_color = CURRENT_BRAND_MANIFEST.colors.primary if _run_has_link(style) else role.color
        if not _color_matches(color, run_color):
            return False
        if role.bold and _effective_bool(style, inherited, "bold") is not True:
            return False
        if role.italic and _effective_bool(style, inherited, "italic") is not True:
            return False
    return True


def _expected_code_for_range(
    expected: MarkdownImportBlock,
    *,
    start: int,
    end: int,
) -> bool | None:
    if start < 0 or end <= start:
        return None
    offset = 0
    states: set[bool] = set()
    covered = 0
    for inline in expected.inline_signature:
        inline_end = offset + len(inline.text)
        overlap = max(0, min(end, inline_end) - max(start, offset))
        if overlap:
            states.add(inline.code)
            covered += overlap
        offset = inline_end
    return next(iter(states)) if covered == end - start and len(states) == 1 else None


def _role_size(block: MarkdownImportBlock) -> float:
    tokens = CURRENT_BRAND_MANIFEST.document
    if block.kind == "title" or (block.kind == "heading" and block.level == 1):
        return tokens.h1_size_pt
    if block.kind == "heading" and block.level == _H2_LEVEL:
        return tokens.h2_size_pt
    if block.kind == "heading":
        return tokens.h3_size_pt
    return tokens.body_size_pt


def _role_color(block: MarkdownImportBlock) -> str:
    if block.kind in {"title", "heading"}:
        return CURRENT_BRAND_MANIFEST.colors.primary
    if block.kind == "quote":
        return CURRENT_BRAND_MANIFEST.colors.muted
    return CURRENT_BRAND_MANIFEST.colors.ink


def _named_style(
    document: dict[str, Any],
    tab: dict[str, Any],
    named_type: str,
) -> _NamedStyle:
    named_styles = tab.get("namedStyles", document.get("namedStyles", {}))
    styles = named_styles.get("styles", []) if isinstance(named_styles, dict) else []
    if not isinstance(styles, list):
        return _NamedStyle({}, {})
    for style in styles:
        if not isinstance(style, dict) or style.get("namedStyleType") != named_type:
            continue
        text_style = style.get("textStyle", {})
        paragraph_style = style.get("paragraphStyle", {})
        return _NamedStyle(
            text_style if isinstance(text_style, dict) else {},
            paragraph_style if isinstance(paragraph_style, dict) else {},
        )
    return _NamedStyle({}, {})


def _paragraph_spacing_matches(
    style: dict[str, Any],
    inherited: dict[str, Any],
) -> bool:
    tokens = CURRENT_BRAND_MANIFEST.document
    above = _effective_dimension_points(style, inherited, "spaceAbove")
    below = _effective_dimension_points(style, inherited, "spaceBelow")
    line_spacing = style.get("lineSpacing", inherited.get("lineSpacing"))
    return (
        abs(above - tokens.paragraph_space_above_pt) <= _TOLERANCE
        and abs(below - tokens.paragraph_space_below_pt) <= _TOLERANCE
        and isinstance(line_spacing, int | float)
        and abs(float(line_spacing) - tokens.line_spacing_percent) <= _TOLERANCE
    )


def _run_has_link(style: dict[str, Any]) -> bool:
    link = style.get("link")
    return isinstance(link, dict) and isinstance(link.get("url"), str)


def _effective_dimension_points(
    style: dict[str, Any],
    inherited: dict[str, Any],
    key: str,
) -> float:
    value = style[key] if key in style else inherited.get(key)
    return _dimension_points(value)


def _effective_font_family(style: dict[str, Any], inherited: dict[str, Any]) -> str:
    return _font_family(style) if "weightedFontFamily" in style else _font_family(inherited)


def _effective_bool(
    style: dict[str, Any],
    inherited: dict[str, Any],
    key: str,
) -> bool | None:
    value = style.get(key, inherited.get(key))
    return value if isinstance(value, bool) else None


def _color_matches(value: object, expected_hex: str) -> bool:
    if not isinstance(value, dict):
        return False
    color = value.get("color", value)
    rgb = color.get("rgbColor") if isinstance(color, dict) else None
    if not isinstance(rgb, dict):
        return False
    expected = tuple(int(expected_hex[index : index + 2], 16) / 255 for index in (1, 3, 5))
    actual_values: list[float] = []
    for channel in ("red", "green", "blue"):
        raw = rgb.get(channel, 0)
        actual_values.append(float(raw) if isinstance(raw, int | float) else -1)
    actual = tuple(actual_values)
    return all(abs(left - right) <= 1 / 255 for left, right in zip(actual, expected, strict=True))


def _rule_brand_matches(paragraph: dict[str, JsonValue]) -> bool:
    style = paragraph.get("paragraphStyle", {})
    border = style.get("borderBottom", {}) if isinstance(style, dict) else {}
    color = border.get("color") if isinstance(border, dict) else None
    return _color_matches(color, CURRENT_BRAND_MANIFEST.colors.border)


def _values_for_key(value: object, target: str) -> tuple[object, ...]:
    found: list[object] = []
    match value:
        case dict() as mapping:
            for key, child in mapping.items():
                if key == target:
                    found.append(child)
                found.extend(_values_for_key(child, target))
        case list() as values:
            for child in values:
                found.extend(_values_for_key(child, target))
        case _:
            pass
    return tuple(found)
