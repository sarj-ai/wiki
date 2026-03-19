"""Paragraph transformation."""

from __future__ import annotations

from app.docs_transformer.constants import _ALIGNMENT_MAP, _ORDERED_GLYPHS
from app.docs_transformer.helpers import _dimension_pt
from app.docs_transformer.runs import _extract_runs


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
        checklist = _is_checklist(lists_map, list_id, nesting)

        block = {
            "type": "paragraph",
            "runs": runs,
            "_list_item": True,
            "_list_id": list_id,
            "_list_ordered": ordered,
            "_list_nesting": nesting,
            "_list_checklist": checklist,
        }
        if para_style:
            block["style"] = para_style
        return block

    block = {"type": "paragraph", "runs": runs}
    if para_style:
        block["style"] = para_style
    return block


_CHECKBOX_SYMBOLS = frozenset({
    "\u2610",  # ☐ BALLOT BOX
    "\u2611",  # ☑ BALLOT BOX WITH CHECK
    "\u2612",  # ☒ BALLOT BOX WITH X
    "\u2713",  # ✓ CHECK MARK
    "\u2714",  # ✔ HEAVY CHECK MARK
    "\u2717",  # ✗ BALLOT X
    "\u2718",  # ✘ HEAVY BALLOT X
})


def _is_checklist(lists_map: dict, list_id: str, nesting: int) -> bool:
    """Detect checklist items by glyphSymbol or Google's CHECKLIST preset.

    Google Docs checklists use GLYPH_TYPE_UNSPECIFIED with no glyphSymbol.
    """
    list_def = lists_map.get(list_id)
    if not list_def:
        return False
    nesting_levels = (
        list_def.get("listProperties", {}).get("nestingLevels", [])
    )
    if nesting < len(nesting_levels):
        level = nesting_levels[nesting]
        # Checkbox glyph symbols
        glyph_symbol = level.get("glyphSymbol", "")
        if glyph_symbol in _CHECKBOX_SYMBOLS:
            return True
        # Google Docs native checklists: GLYPH_TYPE_UNSPECIFIED + no glyphSymbol
        glyph_type = level.get("glyphType", "")
        if glyph_type == "GLYPH_TYPE_UNSPECIFIED" and not glyph_symbol:
            return True
    return False


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
