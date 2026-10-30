from dataclasses import replace
from typing import Any

from app.document_transform.ir import MAX_DOC_TABS, DocumentIR, TextBlock, utf16_length
from app.document_transform.native_font_context import resolve_native_font_values
from app.document_transform.native_font_proof import (
    MAX_NATIVE_FONT_INTERVALS,
    native_font_target_interval_count,
    native_paragraph_font_sha256,
)


def preserved_native_font_blocks(
    document: dict[str, Any] | None, original: DocumentIR, transformed: DocumentIR
) -> tuple[str, ...]:
    result = []
    interval_count = 0
    for block in original.blocks:
        if block.editable or not block.font_style_ranges:
            continue
        count = native_font_target_interval_count(document, block) if document is not None else None
        if (
            original.blocks != transformed.blocks
            or count is None
            or interval_count + count > MAX_NATIVE_FONT_INTERVALS
            or _defended_ranges(document, block) is None
        ):
            result.append(block.id)
        else:
            interval_count += count
    return tuple(result)


def apply_native_font_preservation(document: DocumentIR, block_ids: tuple[str, ...]) -> DocumentIR:
    selected = set(block_ids)
    eligible = {
        block.id
        for block in document.blocks
        if (not block.editable and block.font_style_ranges) or block.preserved_visual_authority
    }
    if len(selected) != len(block_ids) or not selected <= eligible:
        raise ValueError
    return replace(
        document,
        blocks=tuple(
            replace(block, preserved_font_family=block.id in selected) for block in document.blocks
        ),
    )


def defend_native_font_requests(
    requests: list[dict[str, Any]], *, document: dict[str, Any] | None, original: DocumentIR
) -> list[dict[str, Any]]:
    replacements: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
    for block in original.blocks:
        if block.editable or not block.font_style_ranges or block.preserved_font_family:
            continue
        ranges = _defended_ranges(document, block)
        if ranges is None:
            raise ValueError
        for source, defended in zip(block.font_style_ranges, ranges, strict=True):
            replacements[source.tab_id, source.start_index, source.end_index] = defended
    result = []
    for request in requests:
        update = request.get("updateTextStyle", {})
        location = update.get("range", {})
        key = (location.get("tabId"), location.get("startIndex"), location.get("endIndex"))
        if (
            update.get("fields") != "weightedFontFamily.fontFamily"
            or location.get("segmentId")
            or key not in replacements
        ):
            result.append(request)
            continue
        family = update["textStyle"]["weightedFontFamily"]["fontFamily"]
        result.extend(
            {
                "updateTextStyle": {
                    "range": item["range"],
                    "textStyle": {
                        "bold": item["bold"],
                        "weightedFontFamily": {"fontFamily": family, "weight": item["weight"]},
                    },
                    "fields": "weightedFontFamily,bold",
                }
            }
            for item in replacements[key]
        )
    return result


def _defended_ranges(
    document: dict[str, Any] | None, block: TextBlock
) -> list[list[dict[str, Any]]] | None:
    try:
        return _known_ranges(document, block)
    except KeyError, IndexError, TypeError, ValueError, AttributeError:
        return None


def _known_ranges(document: dict[str, Any] | None, block: TextBlock) -> list[list[dict[str, Any]]]:
    source = block.source_range
    if document is None or source is None:
        raise ValueError
    paragraph = _native_paragraph(document, block)
    parts = paragraph["elements"]
    if "".join(part["textRun"]["content"] for part in parts) != block.text + "\n":
        raise ValueError
    cursor = source.start_index
    result: list[list[dict[str, Any]]] = []
    terminal: dict[str, Any] | None = None
    for part in parts:
        if set(part) != {"startIndex", "endIndex", "textRun"} or set(part["textRun"]) - {
            "content",
            "textStyle",
        }:
            raise ValueError
        family = part["textRun"].get("textStyle", {}).get("weightedFontFamily", {})
        if not isinstance(family, dict) or set(family) - {"fontFamily", "weight"}:
            raise ValueError
        if "fontFamily" in family and (
            not isinstance(family["fontFamily"], str) or not family["fontFamily"]
        ):
            raise ValueError
        values = resolve_native_font_values(
            document,
            source.tab_id,
            paragraph.get("paragraphStyle", {}),
            part["textRun"].get("textStyle", {}),
        )
        if values.bold is None or values.weight is None:
            raise ValueError
        item = {
            "range": {
                "tabId": source.tab_id,
                "startIndex": part["startIndex"],
                "endIndex": part["endIndex"],
            },
            "bold": values.bold,
            "weight": values.weight,
        }
        text = part["textRun"]["content"]
        if text == "\n":
            terminal = item
            continue
        if part["startIndex"] != cursor:
            raise ValueError
        end = part["startIndex"] + utf16_length(text.removesuffix("\n"))
        expected = block.font_style_ranges[len(result)]
        if (expected.tab_id, expected.start_index, expected.end_index, expected.segment_id) != (
            source.tab_id,
            cursor,
            end,
            None,
        ):
            raise ValueError
        result.append([item])
        cursor = end
    if cursor != source.end_index or len(result) != len(block.font_style_ranges):
        raise ValueError
    if terminal is not None:
        result[-1].append(terminal)
    return result


def _native_paragraph(document: dict[str, Any], block: TextBlock) -> dict[str, Any]:
    source = block.source_range
    if source is None or source.segment_id:
        raise ValueError
    tabs: dict[str, dict[str, Any]] = {}
    pending = list(document["tabs"])
    while pending:
        tab = pending.pop()
        identifier = tab["tabProperties"]["tabId"]
        if identifier in tabs or len(tabs) >= MAX_DOC_TABS:
            raise ValueError
        tabs[identifier] = tab["documentTab"]
        pending.extend(tab.get("childTabs", []))
    matches = [
        element
        for element in tabs[source.tab_id]["body"]["content"]
        if element.get("startIndex") == source.start_index
        and element.get("endIndex") == source.end_index + 1
    ]
    if len(matches) != 1:
        raise ValueError
    element = matches[0]
    native_paragraph_font_sha256(element)
    paragraph: dict[str, Any] = element["paragraph"]
    if set(paragraph) - {"elements", "paragraphStyle"}:
        raise ValueError
    return paragraph
