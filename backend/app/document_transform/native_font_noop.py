from typing import Any

from app.document_transform.ir import MAX_DOC_TABS, DocumentIR, TextBlock, utf16_length


_MIN_FONT_WEIGHT = 100
_MAX_FONT_WEIGHT = 900


def filter_redundant_native_fonts(
    requests: list[dict[str, Any]],
    *,
    document: dict[str, Any] | None,
    original: DocumentIR,
    transformed: DocumentIR,
    font_family: str,
) -> list[dict[str, Any]]:
    if document is None or original.blocks != transformed.blocks:
        return requests
    try:
        redundant = _redundant_fonts(document, original, transformed, font_family)
        return [request for request in requests if not _redundant_request(request, redundant)]
    except KeyError, TypeError, ValueError, AttributeError:
        return requests


def _redundant_fonts(
    document: dict[str, Any], original: DocumentIR, transformed: DocumentIR, font_family: str
) -> list[dict[str, Any]]:
    paragraphs = _paragraphs(document)
    after = {block.id: block for block in transformed.blocks}
    redundant: list[dict[str, Any]] = []
    for block in original.blocks:
        if block != after.get(block.id) or block.editable or block.source_range is None:
            continue
        source = block.source_range
        matches = [
            element
            for element in paragraphs.get(source.tab_id, [])
            if element.get("startIndex") == source.start_index
            and element.get("endIndex") == source.end_index + 1
        ]
        if len(matches) != 1 or not _explicit_family(matches[0], block, font_family):
            continue
        redundant.extend(
            {
                "updateTextStyle": {
                    "range": {
                        "startIndex": span.start_index,
                        "endIndex": span.end_index,
                        "tabId": span.tab_id,
                    },
                    "textStyle": {"weightedFontFamily": {"fontFamily": font_family}},
                    "fields": "weightedFontFamily.fontFamily",
                }
            }
            for span in block.font_style_ranges
        )
    return redundant


def _redundant_request(request: dict[str, Any], redundant: list[dict[str, Any]]) -> bool:
    location = request.get("updateTextStyle", {}).get("range", {})
    return (
        type(location.get("startIndex")) is int
        and type(location.get("endIndex")) is int
        and request in redundant
    )


def _paragraphs(document: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    pending = list(document["tabs"])
    result: dict[str, list[dict[str, Any]]] = {}
    while pending:
        tab = pending.pop()
        tab_id = tab["tabProperties"]["tabId"]
        if (
            not isinstance(tab_id, str)
            or not tab_id
            or tab_id in result
            or len(result) >= MAX_DOC_TABS
        ):
            raise ValueError
        content = tab["documentTab"]["body"]["content"]
        if not isinstance(content, list):
            raise TypeError
        result[tab_id] = content
        pending.extend(tab.get("childTabs", []))
    return result


def _explicit_family(element: dict[str, Any], block: TextBlock, family: str) -> bool:
    source = block.source_range
    if source is None or source.segment_id or not block.font_style_ranges or source.start_index < 0:
        return False
    if type(element.get("startIndex")) is not int or type(element.get("endIndex")) is not int:
        return False
    cursor = source.start_index
    for span in block.font_style_ranges:
        if span.tab_id != source.tab_id or span.segment_id or span.start_index != cursor:
            return False
        if span.end_index <= span.start_index:
            return False
        cursor = span.end_index
    if cursor != source.end_index or set(element) != {"startIndex", "endIndex", "paragraph"}:
        return False
    paragraph = element["paragraph"]
    if set(paragraph) - {"elements", "paragraphStyle", "bullet"}:
        return False
    cursor = source.start_index
    contents = []
    for part in paragraph["elements"]:
        if set(part) != {"startIndex", "endIndex", "textRun"}:
            return False
        run = part["textRun"]
        if set(run) - {"content", "textStyle"}:
            return False
        text = run["content"]
        if not isinstance(text, str) or not text:
            return False
        start, end = part["startIndex"], part["endIndex"]
        if (
            type(start) is not int
            or type(end) is not int
            or start != cursor
            or end != start + utf16_length(text)
        ):
            return False
        style = run.get("textStyle", {})
        if not isinstance(style, dict) or set(style) - {
            "weightedFontFamily",
            "bold",
            "italic",
            "underline",
            "strikethrough",
            "smallCaps",
            "baselineOffset",
            "fontSize",
            "foregroundColor",
            "backgroundColor",
            "link",
        }:
            return False
        if not _same_family(style.get("weightedFontFamily"), family):
            return False
        contents.append(text)
        cursor = end
    return cursor == source.end_index + 1 and "".join(contents) == block.text + "\n"


def _same_family(value: object, family: str) -> bool:
    if not isinstance(value, dict) or set(value) - {"fontFamily", "weight"}:
        return False
    if value.get("fontFamily") != family:
        return False
    weight = value.get("weight")
    return "weight" not in value or (
        type(weight) is int
        and _MIN_FONT_WEIGHT <= weight <= _MAX_FONT_WEIGHT
        and weight % _MIN_FONT_WEIGHT == 0
    )
