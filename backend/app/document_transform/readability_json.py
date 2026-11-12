from collections.abc import Iterator
from copy import deepcopy
import hashlib
import json
from typing import Literal

from pydantic import JsonValue

from app.document_transform.ir import MAX_DOC_TABS, utf16_length
from app.document_transform.native_payload import (
    canonical_style as canonical_style,
    stable_native_payload,
)
from app.identifiers import DocsTabId


def obj(value: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        msg = "Expected a document object"
        raise TypeError(msg)
    return value


def array(value: JsonValue) -> list[JsonValue]:
    if not isinstance(value, list):
        msg = "Expected a document array"
        raise TypeError(msg)
    return value


def integer(value: JsonValue) -> int:
    if type(value) is not int:
        msg = "Expected a document index"
        raise ValueError(msg)
    return value


def bodies(document: dict[str, JsonValue]) -> Iterator[tuple[DocsTabId, dict[str, JsonValue]]]:
    pending = array(document.get("tabs", []))[:]
    seen = set()
    while pending:
        tab = obj(pending.pop(0))
        identifier = obj(tab["tabProperties"])["tabId"]
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            msg = "Invalid document tab"
            raise ValueError(msg)
        seen.add(identifier)
        if len(seen) > MAX_DOC_TABS:
            msg = "Too many document tabs"
            raise ValueError(msg)
        yield DocsTabId(identifier), obj(tab["documentTab"])
        pending.extend(array(tab.get("childTabs", [])))


def content(tab: dict[str, JsonValue]) -> list[JsonValue]:
    return array(obj(tab["body"])["content"])


def plain_text(element: dict[str, JsonValue]) -> str:
    paragraph = obj(element["paragraph"])
    text = []
    cursor = integer(element["startIndex"])
    if set(element) - {"startIndex", "endIndex", "paragraph"} or set(paragraph) - {
        "elements",
        "paragraphStyle",
    }:
        msg = "Paragraph has protected properties"
        raise ValueError(msg)
    for value in array(paragraph["elements"]):
        part = obj(value)
        if set(part) - {"startIndex", "endIndex", "textRun"}:
            msg = "Paragraph has protected content"
            raise ValueError(msg)
        run = obj(part["textRun"])
        if set(run) - {"content", "textStyle"} or not isinstance(run.get("content"), str):
            msg = "Paragraph has protected text"
            raise ValueError(msg)
        if part.get("startIndex") != cursor or part.get("endIndex") != cursor + utf16_length(
            str(run["content"])
        ):
            raise ValueError
        cursor = integer(part["endIndex"])
        text.append(str(run["content"]))
    if cursor != element["endIndex"]:
        raise ValueError
    return "".join(text)


def digest(value: JsonValue) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def document_digest(document: dict[str, JsonValue]) -> str:
    result = deepcopy(document)
    result.pop("revisionId", None)
    return digest(stable_native_payload(result))


def effective_text_style(
    document: dict[str, JsonValue],
    tab: dict[str, JsonValue],
    paragraph_style: dict[str, JsonValue],
    style: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    return _effective_style(document, tab, paragraph_style, style, field="textStyle")


def effective_paragraph_style(
    document: dict[str, JsonValue], tab: dict[str, JsonValue], style: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    return _effective_style(document, tab, style, style, field="paragraphStyle")


def _effective_style(
    document: dict[str, JsonValue],
    tab: dict[str, JsonValue],
    paragraph_style: dict[str, JsonValue],
    style: dict[str, JsonValue],
    *,
    field: Literal["textStyle", "paragraphStyle"],
) -> dict[str, JsonValue]:
    named = obj(tab.get("namedStyles", document.get("namedStyles", {})))
    by_name = {
        str(obj(item)["namedStyleType"]): obj(obj(item).get(field, {}))
        for item in array(named.get("styles", []))
    }
    result: dict[str, JsonValue] = {}
    for value in (
        by_name.get("NORMAL_TEXT", {}),
        by_name.get(str(paragraph_style.get("namedStyleType", "NORMAL_TEXT")), {}),
        style,
    ):
        family = {
            **obj(result.get("weightedFontFamily", {})),
            **obj(value.get("weightedFontFamily", {})),
        }
        result.update(value)
        if field == "textStyle" and family:
            result["weightedFontFamily"] = family
    return result


def rgb_color(value: str) -> dict[str, JsonValue]:
    color = value.removeprefix("#")
    return {
        key: int(color[index : index + 2], 16) / 255
        for index, key in zip((0, 2, 4), ("red", "green", "blue"), strict=True)
    }
