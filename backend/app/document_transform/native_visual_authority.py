from dataclasses import replace
import math
from typing import Any, TypeIs

from app.document_transform.errors import InPlaceNeedsReviewError
from app.document_transform.ir import MAX_DOC_TABS, BlockKind, DocumentIR
from app.document_transform.native_font_context import native_normal_text_style


def visual_authority_block_ids(
    document: dict[str, Any] | None, original: DocumentIR
) -> tuple[str, ...]:
    try:
        return _visual_authority_block_ids(document, original)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as error:
        raise InPlaceNeedsReviewError from error


def _visual_authority_block_ids(
    document: dict[str, Any] | None, original: DocumentIR
) -> tuple[str, ...]:
    if document is None:
        return ()
    tabs: dict[str, Any] = {}
    pending = list(document.get("tabs", []))
    visited = 0
    while pending:
        visited += 1
        if visited > MAX_DOC_TABS:
            raise ValueError
        tab = pending.pop()
        tab_id = tab["tabProperties"]["tabId"]
        if tab_id in tabs:
            raise ValueError
        tabs[tab_id] = tab["documentTab"]
        pending.extend(tab.get("childTabs", []))
    selected = []
    for block in original.blocks:
        source = block.source_range
        if not block.editable or block.kind is not BlockKind.PARAGRAPH or source is None:
            continue
        elements = tabs[source.tab_id].get("body", {}).get("content", [])
        matches = [
            element for element in elements if element.get("startIndex") == source.start_index
        ]
        if len(matches) != 1:
            raise ValueError
        paragraph = matches[0].get("paragraph", {})
        if (
            paragraph.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT")
            != "NORMAL_TEXT"
        ):
            continue
        visible_styles = [
            part["textRun"].get("textStyle", {})
            for part in paragraph.get("elements", [])
            if part.get("textRun", {}).get("content", "").removesuffix("\n")
        ]
        if not any("fontSize" in style or "foregroundColor" in style for style in visible_styles):
            continue
        parent = native_normal_text_style(document, source.tab_id)
        if parent is None or any(_differs(style, parent) for style in visible_styles):
            selected.append(block.id)
    return tuple(selected)


def _differs(style: dict[str, Any], parent: dict[str, Any]) -> bool:
    for key in ("fontSize", "foregroundColor"):
        if key in style:
            actual, inherited = _presentation(key, style[key]), _presentation(key, parent.get(key))
            if actual is None or inherited is None or actual != inherited:
                return True
    return False


def _presentation(key: str, value: object) -> tuple[float, ...] | None:
    if not isinstance(value, dict):
        return None
    if key == "fontSize":
        magnitude = value.get("magnitude")
        if (
            set(value) == {"magnitude", "unit"}
            and value.get("unit") == "PT"
            and _finite(magnitude)
            and magnitude > 0
        ):
            return (float(magnitude),)
        return None
    color = value.get("color")
    if set(value) != {"color"} or not isinstance(color, dict) or set(color) != {"rgbColor"}:
        return None
    rgb = color.get("rgbColor")
    if not isinstance(rgb, dict) or not set(rgb) <= {"red", "green", "blue"}:
        return None
    result = []
    for name in ("red", "green", "blue"):
        component = rgb.get(name, 0)
        if not _finite(component) or not 0 <= component <= 1:
            return None
        result.append(float(component))
    return tuple(result)


def _finite(value: object) -> TypeIs[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def apply_visual_authority(
    document: DocumentIR, block_ids: tuple[str, ...], *, planner: bool = False
) -> DocumentIR:
    selected = set(block_ids)
    if len(selected) != len(block_ids) or not selected <= {
        block.id for block in document.blocks if block.kind is BlockKind.PARAGRAPH
    }:
        raise ValueError
    return replace(
        document,
        blocks=tuple(
            replace(
                block,
                preserved_visual_authority=block.id in selected,
                editable=False if planner and block.id in selected else block.editable,
            )
            for block in document.blocks
        ),
    )
