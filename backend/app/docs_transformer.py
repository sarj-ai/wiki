"""Transform Google Docs API response into simplified content blocks.

Covers all structural and inline element types from the Docs API v1.
"""

from __future__ import annotations

import re
from datetime import datetime


_ORDERED_GLYPHS = frozenset({
    "DECIMAL",
    "ZERO_DECIMAL",
    "UPPER_ALPHA",
    "LOWER_ALPHA",
    "UPPER_ROMAN",
    "LOWER_ROMAN",
})

_MONOSPACE_FONTS = frozenset({
    "courier new", "courier", "consolas", "monaco", "menlo",
    "source code pro", "roboto mono", "fira code", "fira mono",
    "ibm plex mono", "jetbrains mono", "noto sans mono",
    "ubuntu mono", "dejavu sans mono", "liberation mono",
    "sf mono", "andale mono", "lucida console",
})

_ALIGNMENT_MAP = {
    "CENTER": "center",
    "END": "right",
    "JUSTIFIED": "justify",
}


def transform_doc_with_tabs(doc: dict) -> list[dict]:
    """Transform a Google Docs document with tabs support.

    Returns a list of tab dicts: [{"title": str, "blocks": [...]}, ...]
    For single-tab docs, returns one tab.  Falls back to document.body
    if no tabs are present (pre-tabs API responses or cached data).
    """
    tabs = doc.get("tabs", [])
    if not tabs:
        # Fallback: no tabs data — use document.body directly
        return [{"title": "", "blocks": transform_doc(doc)}]

    result: list[dict] = []
    _collect_tabs(tabs, result)
    return result


def _collect_tabs(tabs: list[dict], result: list[dict], depth: int = 0) -> None:
    """Recursively collect tab content (handles nested child tabs)."""
    for tab in tabs:
        props = tab.get("tabProperties", {})
        title = props.get("title", "")
        tab_id = props.get("tabId", "")
        doc_tab = tab.get("documentTab", {})

        # Build a pseudo-document dict that transform_doc() can consume
        tab_doc = {
            "body": doc_tab.get("body", {}),
            "inlineObjects": doc_tab.get("inlineObjects", {}),
            "positionedObjects": doc_tab.get("positionedObjects", {}),
            "lists": doc_tab.get("lists", {}),
            "footnotes": doc_tab.get("footnotes", {}),
        }
        blocks = transform_doc(tab_doc)
        entry: dict = {"title": title, "blocks": blocks}
        if tab_id:
            entry["tabId"] = tab_id
        if depth > 0:
            entry["depth"] = depth
        result.append(entry)

        child_tabs = tab.get("childTabs", [])
        if child_tabs:
            _collect_tabs(child_tabs, result, depth + 1)


def transform_doc(doc: dict) -> list[dict]:
    """Transform a Google Docs API document into a list of content blocks."""
    body = doc.get("body", {})
    content = body.get("content", [])
    inline_objects = doc.get("inlineObjects", {})
    positioned_objects = doc.get("positionedObjects", {})
    lists_map = doc.get("lists", {})
    footnotes = doc.get("footnotes", {})

    blocks: list[dict] = []
    current_list: dict | None = None
    footnote_index: dict[str, int] = {}  # footnoteId -> number

    for element in content:
        if "paragraph" in element:
            para = element["paragraph"]

            # Collect positioned (floating) objects anchored to this paragraph
            for obj_id in para.get("positionedObjectIds", []):
                img_block = _extract_positioned_object(
                    obj_id, positioned_objects
                )
                if img_block:
                    _flush_list(blocks, current_list)
                    current_list = None
                    blocks.append(img_block)

            block = _transform_paragraph(
                para, inline_objects, lists_map, footnotes, footnote_index
            )
            if block is None:
                continue

            # Group consecutive list items
            if block.get("_list_item"):
                list_id = block.pop("_list_id", None)
                ordered = block.pop("_list_ordered", False)
                nesting = block.pop("_list_nesting", 0)
                del block["_list_item"]

                item = {"runs": block["runs"], "nesting": nesting}

                if current_list and current_list.get("_list_id") == list_id:
                    current_list["items"].append(item)
                else:
                    _flush_list(blocks, current_list)
                    current_list = {
                        "type": "list",
                        "ordered": ordered,
                        "items": [item],
                        "_list_id": list_id,
                    }
            else:
                _flush_list(blocks, current_list)
                current_list = None
                blocks.append(block)

        elif "table" in element:
            _flush_list(blocks, current_list)
            current_list = None
            blocks.append(
                _transform_table(element["table"], inline_objects, lists_map)
            )

        elif "tableOfContents" in element:
            _flush_list(blocks, current_list)
            current_list = None
            blocks.append(_transform_toc(element["tableOfContents"]))

        elif "sectionBreak" in element:
            pass  # skip

    _flush_list(blocks, current_list)

    # Post-process: detect monospace paragraphs and group into code blocks
    blocks = _group_code_blocks(blocks)

    # Append footnote definitions if any were referenced
    if footnote_index:
        fn_blocks = _build_footnote_section(
            footnotes, footnote_index, inline_objects, lists_map
        )
        if fn_blocks:
            blocks.append({"type": "horizontal_rule"})
            blocks.extend(fn_blocks)

    return blocks


def enrich_chips_from_html(blocks: list[dict], html: str) -> list[dict]:
    """Replace chip_placeholder runs with real values extracted from the HTML export.

    Google's HTML export renders smart chips (Person, Status, File, Date, etc.)
    as actual text, while the Docs API v1 returns only opaque Unicode placeholders.

    This function parses the HTML tables and merges the real values back in.
    """
    from html.parser import HTMLParser

    # -----------------------------------------------------------------------
    # Step 1: Parse HTML tables → list of tables, each a list of rows,
    #         each a list of cell texts.
    # -----------------------------------------------------------------------

    class _TableParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tables: list[list[list[str]]] = []
            self._in_table = False
            self._in_cell = False
            self._current_table: list[list[str]] = []
            self._current_row: list[str] = []
            self._current_cell: list[str] = []

        def handle_starttag(self, tag, attrs):
            if tag == "table":
                self._in_table = True
                self._current_table = []
            elif tag == "tr" and self._in_table:
                self._current_row = []
            elif tag in ("td", "th") and self._in_table:
                self._in_cell = True
                self._current_cell = []

        def handle_endtag(self, tag):
            if tag in ("td", "th") and self._in_cell:
                self._in_cell = False
                text = "".join(self._current_cell).strip()
                self._current_row.append(text)
            elif tag == "tr" and self._in_table:
                if self._current_row:
                    self._current_table.append(self._current_row)
            elif tag == "table" and self._in_table:
                self._in_table = False
                if self._current_table:
                    self.tables.append(self._current_table)

        def handle_data(self, data):
            if self._in_cell:
                self._current_cell.append(data)

    parser = _TableParser()
    parser.feed(html)
    html_tables = parser.tables

    # -----------------------------------------------------------------------
    # Step 2: Walk through blocks, match each "table" block to an HTML table
    #         by order, and replace chip_placeholder runs with real text.
    # -----------------------------------------------------------------------

    html_table_idx = 0
    for block in blocks:
        if block.get("type") != "table" or not block.get("hasChips"):
            if block.get("type") == "table":
                html_table_idx += 1
            continue

        if html_table_idx >= len(html_tables):
            html_table_idx += 1
            continue

        html_rows = html_tables[html_table_idx]
        block_rows = block.get("rows", [])

        # If a caption row was extracted from the block, the HTML still has it
        # as the first row.  Skip it so row indices align.
        if block.get("caption") and html_rows:
            html_rows = html_rows[1:]

        for ri, row_cells in enumerate(block_rows):
            if ri >= len(html_rows):
                break
            html_row = html_rows[ri]

            for ci, cell in enumerate(row_cells):
                if ci >= len(html_row):
                    break
                html_text = html_row[ci]
                if not html_text:
                    continue

                # Check if this cell has any chip_placeholder runs
                for para in cell.get("content", []):
                    runs = para.get("runs", [])
                    has_chip = any(r.get("type") == "chip_placeholder" for r in runs)
                    if has_chip and len(runs) == 1 and runs[0].get("type") == "chip_placeholder":
                        # Entire cell is a single chip — replace with HTML text
                        runs[0] = {"text": html_text}

        html_table_idx += 1

    return blocks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_mono_paragraph(block: dict) -> bool:
    """Check if a paragraph block is entirely monospace (code)."""
    if block.get("type") != "paragraph":
        return False
    runs = block.get("runs", [])
    if not runs:
        return False
    # All text-bearing runs must be monospace
    return all(r.get("_mono") for r in runs if r.get("text", "").strip())


def _group_code_blocks(blocks: list[dict]) -> list[dict]:
    """Group consecutive monospace paragraphs into code_block blocks.

    Empty paragraphs between monospace paragraphs are treated as blank
    lines within the same code block (not as a break).

    Also converts isolated monospace runs within normal paragraphs into
    inline code (type: "code").
    """
    result: list[dict] = []
    code_lines: list[str] = []
    # Buffer for empty paragraphs that might sit between code lines
    pending_empties: int = 0

    def flush_code():
        nonlocal pending_empties
        if code_lines:
            # Strip trailing blank lines
            while code_lines and code_lines[-1] == "":
                code_lines.pop()
            result.append({"type": "code_block", "code": "\n".join(code_lines)})
            code_lines.clear()
        pending_empties = 0

    def is_empty_paragraph(block: dict) -> bool:
        return (
            block.get("type") == "paragraph"
            and (not block.get("runs") or all(not r.get("text", "").strip() for r in block["runs"]))
        )

    for block in blocks:
        if _is_mono_paragraph(block):
            # Commit any pending empty lines as blank lines in the code
            for _ in range(pending_empties):
                code_lines.append("")
            pending_empties = 0
            line = "".join(r.get("text", "") for r in block.get("runs", []))
            code_lines.append(line)
        elif is_empty_paragraph(block) and code_lines:
            # Potentially a blank line inside a code block — buffer it
            pending_empties += 1
        else:
            flush_code()
            # Re-emit any buffered empties as normal spacer paragraphs
            for _ in range(pending_empties):
                result.append({"type": "paragraph", "runs": []})
            pending_empties = 0
            # Check for inline code: mixed paragraphs with some mono runs
            if block.get("type") == "paragraph" and block.get("runs"):
                for run in block["runs"]:
                    if run.get("_mono") and run.get("text", "").strip():
                        run["type"] = "code"
                    run.pop("_mono", None)
            result.append(block)

    flush_code()
    return result


def _flush_list(blocks: list[dict], current_list: dict | None) -> None:
    if current_list:
        clean = {k: v for k, v in current_list.items() if not k.startswith("_")}
        blocks.append(clean)


def _color_to_hex(color_obj: dict | None) -> str | None:
    """Convert Docs API OptionalColor → CSS hex string.

    Skips black (default) and white/near-white (designed for dark
    backgrounds in Google Docs that won't work in our theme).
    """
    if not color_obj:
        return None
    c = color_obj.get("color", {}).get("rgbColor")
    if not c:
        return None
    r = int(c.get("red", 0) * 255)
    g = int(c.get("green", 0) * 255)
    b = int(c.get("blue", 0) * 255)
    # Skip pure black (often default, not explicitly set)
    if r == 0 and g == 0 and b == 0:
        return None
    # Skip white / near-white (e.g. white text on colored cell headers)
    if r > 240 and g > 240 and b > 240:
        return None
    return f"#{r:02x}{g:02x}{b:02x}"


def _color_to_hex_all(color_obj: dict | None) -> str | None:
    """Like _color_to_hex but also returns black (#000000)."""
    if not color_obj:
        return None
    c = color_obj.get("color", {}).get("rgbColor")
    if not c:
        return None
    r = int(c.get("red", 0) * 255)
    g = int(c.get("green", 0) * 255)
    b = int(c.get("blue", 0) * 255)
    return f"#{r:02x}{g:02x}{b:02x}"


def _dimension_pt(dim: dict | None) -> float | None:
    if not dim:
        return None
    return dim.get("magnitude")


# ---------------------------------------------------------------------------
# Paragraph transform
# ---------------------------------------------------------------------------


def _transform_paragraph(
    para: dict,
    inline_objects: dict,
    lists_map: dict,
    footnotes: dict,
    footnote_index: dict[str, int],
) -> dict | None:
    style = para.get("paragraphStyle", {})
    named_style = style.get("namedStyleType", "NORMAL_TEXT")
    bullet = para.get("bullet")

    elements = para.get("elements", [])
    runs = _extract_runs(elements, inline_objects, footnotes, footnote_index)

    # Paragraph-level styling
    para_style: dict = {}
    alignment = style.get("alignment")
    if alignment and alignment != "START" and alignment in _ALIGNMENT_MAP:
        para_style["alignment"] = _ALIGNMENT_MAP[alignment]

    direction = style.get("direction")
    if direction == "RIGHT_TO_LEFT":
        para_style["direction"] = "rtl"

    # Skip paragraph shading — Google Docs colors break in dark mode.

    indent_start = _dimension_pt(style.get("indentStart"))
    if indent_start and indent_start > 0 and not bullet:
        para_style["indentStart"] = indent_start

    # Check for horizontal rule
    for el in elements:
        if "horizontalRule" in el:
            return {"type": "horizontal_rule"}

    # Skip empty paragraphs (just newline) — keep as spacer
    is_empty = not runs or (
        len(runs) == 1
        and runs[0].get("text", "").strip() == ""
        and runs[0].get("type") is None
    )

    if is_empty:
        if named_style == "NORMAL_TEXT" and not bullet:
            return {"type": "paragraph", "runs": []}
        return None

    # Title / Subtitle
    if named_style == "TITLE":
        block: dict = {"type": "heading", "level": 1, "runs": runs, "variant": "title"}
        if para_style:
            block["style"] = para_style
        return block

    if named_style == "SUBTITLE":
        block = {"type": "heading", "level": 2, "runs": runs, "variant": "subtitle"}
        if para_style:
            block["style"] = para_style
        return block

    # Headings
    heading_map = {
        "HEADING_1": 1,
        "HEADING_2": 2,
        "HEADING_3": 3,
        "HEADING_4": 4,
        "HEADING_5": 5,
        "HEADING_6": 6,
    }
    if named_style in heading_map:
        block = {"type": "heading", "level": heading_map[named_style], "runs": runs}
        heading_id = style.get("headingId")
        if heading_id:
            block["headingId"] = heading_id
        if para_style:
            block["style"] = para_style
        return block

    # List items
    if bullet:
        list_id = bullet.get("listId", "")
        nesting = bullet.get("nestingLevel", 0)
        ordered = _is_ordered_list(lists_map, list_id, nesting)

        block = {
            "type": "paragraph",
            "runs": runs,
            "_list_item": True,
            "_list_id": list_id,
            "_list_ordered": ordered,
            "_list_nesting": nesting,
        }
        if para_style:
            block["style"] = para_style
        return block

    block = {"type": "paragraph", "runs": runs}
    if para_style:
        block["style"] = para_style
    return block


def _is_ordered_list(lists_map: dict, list_id: str, nesting: int) -> bool:
    """Determine ordered vs unordered using the document's lists definition."""
    list_def = lists_map.get(list_id)
    if not list_def:
        return False
    nesting_levels = (
        list_def.get("listProperties", {}).get("nestingLevels", [])
    )
    if nesting < len(nesting_levels):
        level = nesting_levels[nesting]
        glyph = level.get("glyphType", "")
        if glyph in _ORDERED_GLYPHS:
            return True
        # If glyphSymbol is set, it's a custom bullet (unordered)
        if level.get("glyphSymbol"):
            return False
    return False


# ---------------------------------------------------------------------------
# Run extraction
# ---------------------------------------------------------------------------


def _extract_runs(
    elements: list[dict],
    inline_objects: dict,
    footnotes: dict | None = None,
    footnote_index: dict[str, int] | None = None,
) -> list[dict]:
    """Extract text and inline element runs from paragraph elements."""
    runs: list[dict] = []

    for el in elements:
        if "textRun" in el:
            run = _extract_text_run(el["textRun"])
            if run:
                runs.append(run)

        elif "inlineObjectElement" in el:
            run = _extract_inline_object(el["inlineObjectElement"], inline_objects)
            if run:
                runs.append(run)

        elif "person" in el:
            person = el["person"]
            props = person.get("personProperties", {})
            name = props.get("name", "")
            email = props.get("email", "")
            runs.append({
                "type": "person",
                "text": name or email,
                "email": email,
            })

        elif "richLink" in el:
            rich_link = el["richLink"]
            props = rich_link.get("richLinkProperties", {})
            title = props.get("title", "")
            uri = props.get("uri", "")
            runs.append({
                "type": "rich_link",
                "text": title or uri,
                "link": uri,
                "mimeType": props.get("mimeType"),
            })

        elif "footnoteReference" in el:
            fn_id = el["footnoteReference"].get("footnoteId", "")
            fn_num = el["footnoteReference"].get("footnoteNumber", "")
            if footnote_index is not None and fn_id:
                if fn_id not in footnote_index:
                    footnote_index[fn_id] = int(fn_num) if fn_num else len(footnote_index) + 1
            runs.append({
                "type": "footnote_ref",
                "footnoteId": fn_id,
                "number": fn_num or str(len(footnote_index or {})),
            })

        elif "equation" in el:
            runs.append({"type": "equation", "text": "[equation]"})

        elif "dateElement" in el:
            date_el = el["dateElement"]
            props = date_el.get("dateElementProperties", {})
            timestamp = props.get("timestamp", "")
            if timestamp:
                try:
                    dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    text = dt.strftime("%b %d, %Y %I:%M %p")
                except (ValueError, AttributeError):
                    text = timestamp
            else:
                text = "Date"
            runs.append({"type": "date", "text": text})

        elif "autoText" in el:
            auto_type = el["autoText"].get("type", "")
            runs.append({"type": "auto_text", "text": auto_type.replace("_", " ").title()})

        # pageBreak, columnBreak — skip silently

    return runs


def _extract_text_run(text_run: dict) -> dict | None:
    content = text_run.get("content", "")
    style = text_run.get("textStyle", {})

    # Strip trailing newline
    if content.endswith("\n"):
        content = content[:-1]

    # Strip private-use Unicode chars (Google Docs chip placeholders like \ue907).
    # These represent "building block" smart chips (Person, Status, File, Date, etc.)
    # whose actual values are NOT exposed by the Docs API v1.
    if re.search(r"[\ue000-\uf8ff]", content):
        stripped = re.sub(r"[\ue000-\uf8ff]", "", content).strip()
        if not stripped:
            # Cell contains only chip placeholders — mark as chip so frontend
            # can render a subtle placeholder instead of noisy "Smart chip" pills.
            return {"type": "chip_placeholder"}
        # Otherwise just strip the private-use chars from the text
        content = re.sub(r"[\ue000-\uf8ff]", "", content)

    if not content:
        return None

    run: dict = {"text": content}

    if style.get("bold"):
        run["bold"] = True
    if style.get("italic"):
        run["italic"] = True
    if style.get("underline"):
        # Don't mark underline if it's just a link (Google Docs auto-underlines links)
        if not style.get("link"):
            run["underline"] = True
    if style.get("strikethrough"):
        run["strikethrough"] = True
    if style.get("smallCaps"):
        run["smallCaps"] = True
    if style.get("baselineOffset") == "SUPERSCRIPT":
        run["superscript"] = True
    if style.get("baselineOffset") == "SUBSCRIPT":
        run["subscript"] = True

    link = style.get("link")
    if link:
        url = link.get("url")
        if url:
            run["link"] = url

    # Mark monospace font for code detection
    font = (
        style.get("weightedFontFamily", {}).get("fontFamily", "")
        or style.get("fontFamily", "")
    )
    if font and font.lower() in _MONOSPACE_FONTS:
        run["_mono"] = True

    return run


def _extract_inline_object(
    el: dict, inline_objects: dict
) -> dict | None:
    obj_id = el.get("inlineObjectId")
    if not obj_id or obj_id not in inline_objects:
        return None

    obj = inline_objects[obj_id]
    embedded = (
        obj.get("inlineObjectProperties", {}).get("embeddedObject", {})
    )

    # Image
    image_props = embedded.get("imageProperties", {})
    content_uri = image_props.get("contentUri")
    if content_uri:
        size = embedded.get("size", {})
        width_pt = size.get("width", {}).get("magnitude", 0)
        height_pt = size.get("height", {}).get("magnitude", 0)

        # Skip tiny images (< 24pt) — these are Google Docs internal column-type
        # icons (person, status, text, date icons) that aren't publicly accessible.
        if width_pt > 0 and height_pt > 0 and width_pt < 24 and height_pt < 24:
            return None

        run: dict = {"type": "image", "url": content_uri}
        title = embedded.get("title")
        desc = embedded.get("description")
        if desc:
            run["alt"] = desc
        elif title:
            run["alt"] = title

        if width_pt:
            run["width"] = width_pt
        if height_pt:
            run["height"] = height_pt
        return run

    # Embedded drawing (opaque — just note it exists)
    if embedded.get("embeddedDrawingProperties") is not None:
        return {"type": "drawing", "text": "[Drawing]"}

    # Linked content (Sheets chart)
    linked = embedded.get("linkedContentReference", {})
    chart_ref = linked.get("sheetsChartReference")
    if chart_ref:
        return {
            "type": "chart",
            "text": "[Embedded Chart]",
            "spreadsheetId": chart_ref.get("spreadsheetId"),
            "chartId": chart_ref.get("chartId"),
        }

    return None


def _extract_positioned_object(
    obj_id: str, positioned_objects: dict
) -> dict | None:
    obj = positioned_objects.get(obj_id)
    if not obj:
        return None

    embedded = (
        obj.get("positionedObjectProperties", {}).get("embeddedObject", {})
    )
    image_props = embedded.get("imageProperties", {})
    content_uri = image_props.get("contentUri")
    if content_uri:
        block: dict = {
            "type": "image",
            "url": content_uri,
        }
        title = embedded.get("title")
        desc = embedded.get("description")
        if desc:
            block["alt"] = desc
        elif title:
            block["alt"] = title

        size = embedded.get("size", {})
        width_pt = size.get("width", {}).get("magnitude")
        height_pt = size.get("height", {}).get("magnitude")
        if width_pt:
            block["width"] = width_pt
        if height_pt:
            block["height"] = height_pt
        return block

    return None


# ---------------------------------------------------------------------------
# Table transform
# ---------------------------------------------------------------------------


def _transform_table(
    table: dict, inline_objects: dict, lists_map: dict
) -> dict:
    rows: list[list[dict]] = []
    has_header = False
    has_chips = False

    for ri, row in enumerate(table.get("tableRows", [])):
        row_style = row.get("tableRowStyle", {})
        if ri == 0 and row_style.get("tableHeader"):
            has_header = True

        cells: list[dict] = []
        for cell in row.get("tableCells", []):
            cell_style = cell.get("tableCellStyle", {})
            row_span = cell_style.get("rowSpan", 1)
            col_span = cell_style.get("columnSpan", 1)

            # Extract cell content (can be multiple paragraphs)
            cell_content: list[dict] = []
            for content_el in cell.get("content", []):
                if "paragraph" in content_el:
                    para_runs = _extract_runs(
                        content_el["paragraph"].get("elements", []),
                        inline_objects,
                    )
                    para_style = content_el["paragraph"].get("paragraphStyle", {})
                    alignment = para_style.get("alignment")
                    if para_runs:
                        for run in para_runs:
                            if run.get("type") == "chip_placeholder":
                                has_chips = True
                        para_block: dict = {"runs": para_runs}
                        if alignment and alignment != "START" and alignment in _ALIGNMENT_MAP:
                            para_block["alignment"] = _ALIGNMENT_MAP[alignment]
                        cell_content.append(para_block)

            cell_data: dict = {
                "content": cell_content if cell_content else [{"runs": []}],
            }

            if row_span > 1:
                cell_data["rowSpan"] = row_span
            if col_span > 1:
                cell_data["colSpan"] = col_span

            # Skip cell backgroundColor — Google Docs colors break in dark mode.

            v_align = cell_style.get("contentAlignment")
            if v_align and v_align != "TOP":
                cell_data["verticalAlign"] = v_align.lower()

            cells.append(cell_data)
        rows.append(cells)

    # Detect caption row: if the first row is marked as header and has a single
    # cell spanning all columns (e.g. "PRODUCT ROADMAP"), treat it as a caption
    # and promote the second row to be the header row.
    caption = None
    num_cols = max((len(r) for r in rows), default=0) if rows else 0
    if has_header and len(rows) >= 2 and len(rows[0]) == 1:
        first_cell = rows[0][0]
        cell_span = first_cell.get("colSpan", 1)
        if cell_span >= num_cols or num_cols <= 1:
            # Extract caption text
            caption_parts = []
            for para in first_cell.get("content", []):
                for run in para.get("runs", []):
                    if run.get("text"):
                        caption_parts.append(run["text"])
            if caption_parts:
                caption = " ".join(caption_parts).strip()
            # Remove caption row and promote next row as header
            rows = rows[1:]

    result: dict = {"type": "table", "rows": rows}
    if has_header:
        result["hasHeader"] = True
    if caption:
        result["caption"] = caption
    if has_chips:
        result["hasChips"] = True

    col_props = table.get("tableColumnProperties", [])
    if col_props:
        widths = [_dimension_pt(cp.get("width")) for cp in col_props]
        if any(w is not None for w in widths):
            result["columnWidths"] = widths

    return result


# ---------------------------------------------------------------------------
# Table of Contents
# ---------------------------------------------------------------------------


def _transform_toc(toc: dict) -> dict:
    items: list[dict] = []
    for content_el in toc.get("content", []):
        if "paragraph" not in content_el:
            continue
        para = content_el["paragraph"]
        style = para.get("paragraphStyle", {})
        indent = _dimension_pt(style.get("indentStart"))
        level = 0
        if indent:
            level = max(0, int(indent / 36))  # ~36pt per indent level

        runs = _extract_runs(para.get("elements", []), {})
        text = "".join(r.get("text", "") for r in runs)

        # Extract link: TOC entries use headingId (not url) for internal links
        link = None
        for el in para.get("elements", []):
            text_style = el.get("textRun", {}).get("textStyle", {})
            link_obj = text_style.get("link")
            if link_obj:
                heading_id = link_obj.get("headingId")
                if heading_id:
                    link = f"#heading={heading_id}"
                elif link_obj.get("url"):
                    link = link_obj["url"]
                break

        if text.strip():
            items.append({"text": text.strip(), "level": level, "link": link})

    return {"type": "table_of_contents", "tocItems": items}


# ---------------------------------------------------------------------------
# Footnotes
# ---------------------------------------------------------------------------


def _build_footnote_section(
    footnotes: dict,
    footnote_index: dict[str, int],
    inline_objects: dict,
    lists_map: dict,
) -> list[dict]:
    blocks: list[dict] = []
    blocks.append({"type": "heading", "level": 4, "runs": [{"text": "Footnotes"}]})

    for fn_id, fn_num in sorted(footnote_index.items(), key=lambda x: x[1]):
        fn = footnotes.get(fn_id)
        if not fn:
            continue
        fn_content = fn.get("content", [])
        fn_runs: list[dict] = [{"text": f"{fn_num}. ", "bold": True}]
        for content_el in fn_content:
            if "paragraph" in content_el:
                para_runs = _extract_runs(
                    content_el["paragraph"].get("elements", []),
                    inline_objects,
                )
                fn_runs.extend(para_runs)

        blocks.append({"type": "paragraph", "runs": fn_runs})

    return blocks
