from copy import deepcopy

from pydantic import JsonValue

from app.document_transform.brand import copy_style_locations, font_only_paragraph_alignment
from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.ir import DocumentIR, SourceRange, TextBlock, parse_google_document
from app.document_transform.native_font_context import (
    resolve_native_font_family,
    resolve_native_font_values,
)
from app.document_transform.readability_json import array, bodies, content, integer, obj


_REGULAR_WEIGHT = 400
# Google Docs requests are provider-defined, extensible JSON objects.
type NativeStyleRequest = dict[str, JsonValue]


def regular_prose_requests(block: TextBlock) -> list[NativeStyleRequest]:
    return [
        {
            "updateTextStyle": {
                "range": {
                    "tabId": span.tab_id,
                    "startIndex": span.start_index,
                    "endIndex": span.end_index,
                },
                "textStyle": {
                    "bold": False,
                    "weightedFontFamily": {
                        "fontFamily": CURRENT_BRAND_MANIFEST.document.font_family,
                        "weight": _REGULAR_WEIGHT,
                    },
                },
                "fields": "weightedFontFamily,bold",
            }
        }
        for span in block.font_style_ranges
        if span.end_index > span.start_index
    ]


def project_regular_prose(paragraph: dict[str, JsonValue], block: TextBlock) -> None:
    # Unchanged editable citation paragraphs also have strict native proofs.
    # Project only the exact planned visible font ranges, retaining the
    # terminator and every other native property as authored.
    parts: list[JsonValue] = []
    for value in array(paragraph["elements"]):
        part = obj(value)
        run = obj(part["textRun"])
        text = str(run["content"])
        start, end = integer(part["startIndex"]), integer(part["endIndex"])
        visible_end = end - int(text.endswith("\n"))
        if not any(
            span.start_index == start and span.end_index == visible_end
            for span in block.font_style_ranges
        ):
            parts.append(part)
            continue
        terminal = deepcopy(part) if text.endswith("\n") else None
        if terminal is not None:
            terminal["startIndex"] = visible_end
            obj(terminal["textRun"])["content"] = "\n"
            part["endIndex"] = visible_end
            run["content"] = text[:-1]
        style = obj(run.get("textStyle", {}))
        style["bold"] = False
        style["weightedFontFamily"] = {
            **obj(style.get("weightedFontFamily", {})),
            "weight": _REGULAR_WEIGHT,
        }
        run["textStyle"] = style
        parts.append(part)
        if terminal is not None:
            parts.append(terminal)
    paragraph["elements"] = parts


def verify_regular_copy_prose(
    source: dict[str, JsonValue], actual: dict[str, JsonValue], expected: DocumentIR
) -> bool:
    if not expected.copy_font_defended:
        return True
    located = copy_style_locations(parse_google_document(source, title=expected.title), expected)
    actual_blocks = {
        (block.source_range.tab_id, block.source_range.start_index): block
        for block in parse_google_document(actual, title=expected.title).blocks
        if block.source_range is not None
    }
    paragraphs = {
        (tab_id, integer(element["startIndex"])): obj(element["paragraph"])
        for tab_id, tab in bodies(actual)
        for value in content(tab)
        if "paragraph" in (element := obj(value))
    }
    for block in located.blocks:
        if font_only_paragraph_alignment(block) is None or block.source_range is None:
            continue
        location = (block.source_range.tab_id, block.source_range.start_index)
        if not _verify_prose_block(
            actual, paragraphs.get(location), block, actual_blocks.get(location)
        ):
            return False
    return True


def _verify_prose_block(
    document: dict[str, JsonValue],
    paragraph: dict[str, JsonValue] | None,
    expected: TextBlock,
    actual: TextBlock | None,
) -> bool:
    if paragraph is None:
        return False
    if expected.protected_suffix is not None and (
        actual is None
        or actual.protected_suffix != expected.protected_suffix
        or actual.protected_suffix_links_sha256 != expected.protected_suffix_links_sha256
    ):
        return False
    return all(_regular_range(document, paragraph, span) for span in expected.font_style_ranges)


def _regular_range(
    document: dict[str, JsonValue], paragraph: dict[str, JsonValue], span: SourceRange
) -> bool:
    cursor = span.start_index
    for value in array(paragraph["elements"]):
        part = obj(value)
        start = max(span.start_index, integer(part["startIndex"]))
        end = min(span.end_index, integer(part["endIndex"]))
        if end <= start:
            continue
        if start != cursor:
            return False
        style = obj(obj(part["textRun"]).get("textStyle", {}))
        parent = obj(paragraph.get("paragraphStyle", {}))
        values = resolve_native_font_values(document, span.tab_id, parent, style)
        if (
            values.bold is not False
            or values.weight != _REGULAR_WEIGHT
            or resolve_native_font_family(document, span.tab_id, parent, style)
            != CURRENT_BRAND_MANIFEST.document.font_family
        ):
            return False
        cursor = end
    return cursor == span.end_index
