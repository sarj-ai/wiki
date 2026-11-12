from dataclasses import replace
from typing import Any

from pydantic import JsonValue

from app.document_transform.ir import (
    MAX_DOC_TABS,
    DocumentIR,
    TextBlock,
    _font_run_is_safe,
    utf16_length,
)
from app.document_transform.native_font_context import resolve_native_font_values
from app.document_transform.native_font_proof import (
    MAX_NATIVE_FONT_INTERVALS,
    _position_deltas,
    native_font_target_interval_count,
    native_paragraph_font_sha256,
)
from app.document_transform.native_prose import _Element
from app.document_transform.readability_json import obj
from app.identifiers import DocsTabId


def preserved_native_font_blocks(
    document: dict[str, Any] | None, original: DocumentIR, transformed: DocumentIR
) -> tuple[str, ...]:
    result = []
    interval_count = 0
    try:
        _position_deltas(original, transformed)
        can_relocate = True
    except ValueError:
        can_relocate = False
    for block in original.blocks:
        if block.editable or not block.font_style_ranges:
            continue
        count = native_font_target_interval_count(document, block) if document is not None else None
        if (
            not can_relocate
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
    requests: list[dict[str, JsonValue]],
    *,
    document: dict[str, JsonValue] | None,
    original: DocumentIR,
    transformed: DocumentIR | None = None,
) -> list[dict[str, JsonValue]]:
    blocks = tuple(
        block
        for block in original.blocks
        if not block.editable and block.font_style_ranges and not block.preserved_font_family
    )
    if not blocks:
        return requests
    deltas = _position_deltas(original, transformed)
    replacements: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
    for block in blocks:
        ranges = _defended_ranges(document, block)
        if ranges is None:
            raise ValueError
        delta = deltas.get(block.id, 0)
        for source, defended in zip(block.font_style_ranges, ranges, strict=True):
            replacements[source.tab_id, source.start_index + delta, source.end_index + delta] = [
                {
                    **item,
                    "range": {
                        **item["range"],
                        "startIndex": item["range"]["startIndex"] + delta,
                        "endIndex": item["range"]["endIndex"] + delta,
                    },
                }
                for item in defended
            ]
    result: list[dict[str, JsonValue]] = []
    for request in requests:
        update = obj(request.get("updateTextStyle", {}))
        key = _font_request_key(update)
        if key is None or key not in replacements:
            result.append(request)
            continue
        family = obj(obj(update["textStyle"])["weightedFontFamily"])["fontFamily"]
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


def _font_request_key(update: dict[str, JsonValue]) -> tuple[str, int, int] | None:
    location = obj(update.get("range", {}))
    if update.get("fields") != "weightedFontFamily.fontFamily" or location.get("segmentId"):
        return None
    tab_id, start, end = (location.get(key) for key in ("tabId", "startIndex", "endIndex"))
    if not isinstance(tab_id, str) or type(start) is not int or type(end) is not int:
        return None
    return tab_id, start, end


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
        item = _defended_font_run(
            document, source.tab_id, paragraph.get("paragraphStyle", {}), part
        )
        text = part["textRun"]["content"]
        if part["startIndex"] != cursor:
            raise ValueError
        cursor = part["endIndex"]
        if text == "\n":
            terminal = item
            continue
        if not _font_run_is_safe(part, part["textRun"]):
            continue
        end = part["startIndex"] + utf16_length(text.removesuffix("\n"))
        expected = block.font_style_ranges[len(result)]
        if (expected.tab_id, expected.start_index, expected.end_index, expected.segment_id) != (
            source.tab_id,
            part["startIndex"],
            end,
            None,
        ):
            raise ValueError
        result.append([item])
    if cursor != source.end_index + 1 or len(result) != len(block.font_style_ranges):
        raise ValueError
    if terminal is not None:
        result[-1].append(terminal)
    return result


def _defended_font_run(
    document: dict[str, JsonValue],
    tab_id: DocsTabId,
    paragraph_style: dict[str, JsonValue],
    part: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    record = _Element.model_validate(part)
    family = record.text_run.text_style.weighted_font_family
    if family is not None and not family.font_family:
        raise ValueError
    values = resolve_native_font_values(
        document,
        tab_id,
        paragraph_style,
        record.text_run.text_style.model_dump(by_alias=True, exclude_unset=True),
    )
    if values.bold is None or values.weight is None:
        raise ValueError
    return {
        "range": {"tabId": tab_id, "startIndex": record.start_index, "endIndex": record.end_index},
        "bold": values.bold,
        "weight": values.weight,
    }


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
