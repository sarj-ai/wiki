from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import re
from typing import TYPE_CHECKING

from pydantic import JsonValue

from app.document_transform.code_syntax import is_code_font_family
from app.identifiers import DocsTabId


if TYPE_CHECKING:
    from app.document_transform.ir import DocumentIR


type NativeCodeRecord = dict[str, JsonValue]
_FENCE = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})[ \t]*(?P<hint>[A-Za-z0-9_+.-]*)[ \t]*\n$")
_PARAGRAPH_FIELDS = frozenset({"elements", "paragraphStyle"})
_PARAGRAPH_STYLE_FIELDS = frozenset(
    {
        "namedStyleType",
        "alignment",
        "lineSpacing",
        "direction",
        "spacingMode",
        "spaceAbove",
        "spaceBelow",
        "borderBetween",
        "borderTop",
        "borderBottom",
        "borderLeft",
        "borderRight",
        "indentFirstLine",
        "indentStart",
        "indentEnd",
        "tabStops",
        "keepLinesTogether",
        "keepWithNext",
        "avoidWidowAndOrphan",
        "shading",
        "pageBreakBefore",
    }
)
_TEXT_STYLE_FIELDS = frozenset(
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
_FENCED_PROSE_FONTS = frozenset({"arial", "calibri", "aptos", "nunito", "times new roman"})
_NESTED_STYLE_KEYS = frozenset(
    {
        "magnitude",
        "unit",
        "fontFamily",
        "weight",
        "color",
        "rgbColor",
        "red",
        "green",
        "blue",
        "backgroundColor",
        "width",
        "padding",
        "dashStyle",
        "offset",
        "alignment",
    }
)


@dataclass(frozen=True, slots=True)
class NativeCodeSourceParagraph:
    tab_id: DocsTabId
    ordinal: int
    element: NativeCodeRecord
    text: str | None


@dataclass(frozen=True, slots=True)
class NativeCodeSourceGroup:
    paragraphs: tuple[NativeCodeSourceParagraph, ...]
    language: str | None = None
    fenced: bool = False
    complete: bool = True


def protect_native_code(document: DocumentIR, raw: NativeCodeRecord) -> DocumentIR:
    selected = {
        (item.tab_id, code_index(item.element["startIndex"]), code_index(item.element["endIndex"]))
        for group in native_code_groups(raw)
        for item in group.paragraphs
    }
    return replace(
        document,
        blocks=tuple(
            replace(block, kind=type(block.kind).CODE, editable=False, font_style_ranges=())
            if block.source_range is not None
            and (
                block.source_range.tab_id,
                block.source_range.start_index,
                block.source_range.end_index + 1,
            )
            in selected
            else block
            for block in document.blocks
        ),
    )


def native_code_groups(raw: NativeCodeRecord) -> tuple[NativeCodeSourceGroup, ...]:
    groups = []
    for tab_id, body in code_bodies(raw).items():
        paragraphs = tuple(
            NativeCodeSourceParagraph(tab_id, i, code_object(value), _text(code_object(value)))
            for i, value in enumerate(body)
        )
        groups.extend(_body_groups(paragraphs))
    return tuple(groups)


def _body_groups(paragraphs: tuple[NativeCodeSourceParagraph, ...]) -> list[NativeCodeSourceGroup]:
    groups = []
    cursor = 0
    while cursor < len(paragraphs):
        item = paragraphs[cursor]
        if fence := _FENCE.fullmatch(item.text or ""):
            closing = _closing_fence(paragraphs, cursor, fence.group("marker"))
            stop = closing if closing is not None else len(paragraphs)
            groups.append(
                NativeCodeSourceGroup(
                    paragraphs=paragraphs[cursor:stop],
                    language=fence.group("hint") or None,
                    fenced=True,
                    complete=closing is not None,
                )
            )
            cursor = stop
        elif _mono(item):
            stop = _mono_stop(paragraphs, cursor)
            groups.append(NativeCodeSourceGroup(paragraphs[cursor:stop]))
            cursor = stop
        else:
            cursor += 1
    return groups


def _mono_stop(items: tuple[NativeCodeSourceParagraph, ...], start: int) -> int:
    stop = start + 1
    while (
        stop < len(items)
        and not _FENCE.fullmatch(items[stop].text or "")
        and (_mono(items[stop]) or items[stop].text == "\n")
    ):
        stop += 1
    while items[stop - 1].text == "\n":
        stop -= 1
    return stop


def _closing_fence(
    items: tuple[NativeCodeSourceParagraph, ...], start: int, marker: str
) -> int | None:
    return next(
        (index + 1 for index in range(start + 1, len(items)) if _is_closing(items[index], marker)),
        None,
    )


def _is_closing(item: NativeCodeSourceParagraph, marker: str) -> bool:
    found = _FENCE.fullmatch(item.text or "")
    return bool(
        found
        and not found.group("hint")
        and found.group("marker")[0] == marker[0]
        and len(found.group("marker")) >= len(marker)
    )


def _mono(item: NativeCodeSourceParagraph) -> bool:
    paragraph = item.element.get("paragraph")
    if not isinstance(paragraph, dict):
        return False
    parts = paragraph.get("elements")
    if not isinstance(parts, list):
        return False
    fonts = []
    for part in parts:
        if not isinstance(part, dict) or not isinstance(run := part.get("textRun"), dict):
            return False
        text = run.get("content")
        if not isinstance(text, str):
            return False
        if text.removesuffix("\n"):
            fonts.append(_font(run.get("textStyle", {})))
    return bool(fonts) and all(font is not None and is_code_font_family(font) for font in fonts)


def _font(value: JsonValue) -> str | None:
    if not isinstance(value, dict):
        return None
    family = value.get("weightedFontFamily", {})
    if not isinstance(family, dict) or set(family) - {"fontFamily", "weight"}:
        return None
    font = family.get("fontFamily", "")
    return font if isinstance(font, str) else None


def native_code_eligible(item: NativeCodeSourceParagraph, *, fenced: bool) -> bool:
    element = item.element
    if set(element) - {"startIndex", "endIndex", "paragraph"}:
        return False
    paragraph = element.get("paragraph")
    if not isinstance(paragraph, dict) or set(paragraph) - _PARAGRAPH_FIELDS:
        return False
    if not _paragraph_style_safe(paragraph.get("paragraphStyle", {})):
        return False
    start, end = element.get("startIndex"), element.get("endIndex")
    if type(start) is not int or type(end) is not int or start < 0 or end <= start:
        return False
    parts = paragraph.get("elements")
    return (
        isinstance(parts, list)
        and bool(parts)
        and _runs_safe(parts, start, end, fenced=fenced)
        and bool(item.text and item.text.endswith("\n"))
    )


def _paragraph_style_safe(value: JsonValue) -> bool:
    if not isinstance(value, dict) or set(value) - _PARAGRAPH_STYLE_FIELDS:
        return False
    if value.get("namedStyleType", "NORMAL_TEXT") != "NORMAL_TEXT" or not all(
        _known_nested(child) for child in value.values()
    ):
        return False
    shading = value.get("shading", {})
    return isinstance(shading, dict) and not set(shading) - {"backgroundColor"}


def _runs_safe(parts: list[JsonValue], start: int, end: int, *, fenced: bool) -> bool:
    cursor = start
    for value in parts:
        if not _safe_part(value, fenced=fenced):
            return False
        part = code_object(value)
        run = code_object(part["textRun"])
        text = code_string(run["content"])
        if part["startIndex"] != cursor or part["endIndex"] != cursor + code_units(text):
            return False
        cursor = code_index(part["endIndex"])
    return cursor == end


def _safe_part(value: JsonValue, *, fenced: bool) -> bool:
    if not isinstance(value, dict) or set(value) - {"startIndex", "endIndex", "textRun"}:
        return False
    if type(value.get("startIndex")) is not int or type(value.get("endIndex")) is not int:
        return False
    run = value.get("textRun")
    if not isinstance(run, dict) or set(run) - {"content", "textStyle"}:
        return False
    text = run.get("content")
    if not isinstance(text, str) or not text:
        return False
    style = run.get("textStyle", {})
    if not _text_style_safe(style):
        return False
    font = _font(style)
    if font is None:
        return False
    if text == "\n":
        return not font or is_code_font_family(font) or font.casefold() in _FENCED_PROSE_FONTS
    return is_code_font_family(font) or (fenced and font.casefold() in _FENCED_PROSE_FONTS)


def _text_style_safe(value: JsonValue) -> bool:
    return (
        isinstance(value, dict)
        and not set(value) - _TEXT_STYLE_FIELDS
        and value.get("baselineOffset", "NONE") == "NONE"
        and all(_known_nested(child) for child in value.values())
    )


def _text(element: NativeCodeRecord) -> str | None:
    paragraph = element.get("paragraph")
    if (
        not isinstance(paragraph, dict)
        or not isinstance(parts := paragraph.get("elements"), list)
        or not parts
    ):
        return None
    pieces = []
    for part in parts:
        if not isinstance(part, dict) or not isinstance(run := part.get("textRun"), dict):
            return None
        text = run.get("content")
        if not isinstance(text, str):
            return None
        pieces.append(text)
    return "".join(pieces)


def _known_nested(value: JsonValue) -> bool:
    match value:
        case dict():
            return not set(value) - _NESTED_STYLE_KEYS and all(
                _known_nested(child) for child in value.values()
            )
        case list():
            return all(_known_nested(child) for child in value)
        case _:
            return True


def code_bodies(raw: NativeCodeRecord) -> dict[DocsTabId, list[JsonValue]]:
    result: dict[DocsTabId, list[JsonValue]] = {}

    def visit(tabs: list[JsonValue]) -> None:
        for value in tabs:
            tab = code_object(value)
            properties = code_object(tab["tabProperties"])
            tab_id = DocsTabId(code_string(properties["tabId"]))
            if tab_id in result:
                msg = "Invalid native code tab identity"
                raise ValueError(msg)
            document_tab = code_object(tab["documentTab"])
            body = code_object(document_tab.get("body", {}))
            result[tab_id] = code_array(body.get("content", []))
            visit(code_array(tab.get("childTabs", [])))

    visit(code_array(raw.get("tabs", [])))
    return result


def code_parents(raw: NativeCodeRecord) -> str:
    values: list[JsonValue] = [raw.get("namedStyles")]

    def visit(tabs: list[JsonValue]) -> None:
        for value in tabs:
            tab = code_object(value)
            properties = code_object(tab["tabProperties"])
            document_tab = code_object(tab["documentTab"])
            values.append([properties["tabId"], document_tab.get("namedStyles")])
            visit(code_array(tab.get("childTabs", [])))

    visit(code_array(raw.get("tabs", [])))
    return hashlib.sha256(code_json(values).encode()).hexdigest()


def code_object(value: JsonValue) -> NativeCodeRecord:
    if not isinstance(value, dict):
        msg = "Expected native code JSON object"
        raise TypeError(msg)
    return value


def code_array(value: JsonValue) -> list[JsonValue]:
    if not isinstance(value, list):
        msg = "Expected native code JSON array"
        raise TypeError(msg)
    return value


def code_index(value: JsonValue) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        msg = "Expected native code integer position"
        raise TypeError(msg)
    return value


def code_string(value: JsonValue) -> str:
    if not isinstance(value, str):
        msg = "Expected native code text"
        raise TypeError(msg)
    return value


def code_color(hex_color: str) -> NativeCodeRecord:
    value = hex_color.removeprefix("#")
    rgb: NativeCodeRecord = {
        key: int(value[offset : offset + 2], 16) / 255
        for key, offset in (("red", 0), ("green", 2), ("blue", 4))
    }
    return {"color": {"rgbColor": rgb}}


def code_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def code_json(value: JsonValue) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
