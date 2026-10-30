from __future__ import annotations

import hashlib
import json
from typing import Any

from app.document_transform.errors import UnsupportedTabMetadataError
from app.document_transform.naming import supported_tab_icon
from app.document_transform.tab_names import NativeTabProperties
from app.identifiers import DocsTabId


_MAX_TAB_DEPTH = 2


def native_tab_properties(document: dict[str, Any]) -> tuple[NativeTabProperties, ...]:
    result: list[NativeTabProperties] = []

    def visit(tabs: object, parent: DocsTabId | None, depth: int = 0) -> None:
        if not isinstance(tabs, list) or depth > _MAX_TAB_DEPTH:
            raise UnsupportedTabMetadataError
        for index, tab in enumerate(tabs):
            if not isinstance(tab, dict):
                raise UnsupportedTabMetadataError
            raw_properties: Any = tab.get("tabProperties", {})
            if not isinstance(raw_properties, dict) or set(raw_properties) - {
                "tabId",
                "title",
                "parentTabId",
                "index",
                "nestingLevel",
                "iconEmoji",
            }:
                raise UnsupportedTabMetadataError
            properties: dict[str, Any] = dict(raw_properties)
            if "iconEmoji" in properties and not supported_tab_icon(properties["iconEmoji"]):
                raise UnsupportedTabMetadataError
            tab_id = properties.get("tabId")
            if not isinstance(tab_id, str) or not tab_id:
                raise UnsupportedTabMetadataError
            if not isinstance(properties.get("title", ""), str):
                raise UnsupportedTabMetadataError
            for field, default in (("index", index), ("nestingLevel", depth)):
                value = properties.get(field, default)
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise UnsupportedTabMetadataError
            if properties.get("nestingLevel", depth) != depth:
                raise UnsupportedTabMetadataError
            explicit_parent = properties.get("parentTabId", parent)
            if explicit_parent != parent and not (
                parent is None and isinstance(explicit_parent, str) and not explicit_parent
            ):
                raise UnsupportedTabMetadataError
            result.append(
                NativeTabProperties(
                    tab_id=DocsTabId(tab_id),
                    title=properties.get("title", ""),
                    parent_tab_id=properties.get("parentTabId") or parent,
                    index=properties.get("index", index),
                    icon_emoji=properties.get("iconEmoji"),
                )
            )
            visit(tab.get("childTabs", []), DocsTabId(tab_id), depth + 1)

    visit(document.get("tabs", []), None)
    return tuple(result)


def tab_naming_invariant_sha256(document: dict[str, Any], marker_name: str = "") -> str:
    def scrub(value: object, *, tab_properties: bool = False) -> object:
        match value:
            case list():
                return [scrub(item) for item in value]
            case dict():
                result: dict[str, object] = {}
                for key, item in value.items():
                    child = item
                    if key == "revisionId" or (tab_properties and key in {"title", "iconEmoji"}):
                        continue
                    if key == "namedRanges" and isinstance(item, dict):
                        child = {
                            name: ranges for name, ranges in item.items() if name != marker_name
                        }
                        if not child:
                            continue
                    result[str(key)] = scrub(child, tab_properties=key == "tabProperties")
                return result
            case _:
                return value

    encoded = json.dumps(scrub(document), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
