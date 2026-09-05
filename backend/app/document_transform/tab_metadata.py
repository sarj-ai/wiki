from __future__ import annotations

import hashlib
import json
from typing import Any

from app.document_transform.tab_names import NativeTabProperties
from app.identifiers import DocsTabId


def native_tab_properties(document: dict[str, Any]) -> tuple[NativeTabProperties, ...]:
    result: list[NativeTabProperties] = []

    def visit(tabs: list[dict[str, Any]], parent: DocsTabId | None) -> None:
        for index, tab in enumerate(tabs):
            properties = tab.get("tabProperties", {})
            tab_id = properties.get("tabId")
            if not isinstance(tab_id, str) or not tab_id:
                continue
            result.append(
                NativeTabProperties(
                    tab_id=DocsTabId(tab_id),
                    title=properties.get("title", ""),
                    parent_tab_id=properties.get("parentTabId") or parent,
                    index=properties.get("index", index),
                )
            )
            visit(tab.get("childTabs", []), DocsTabId(tab_id))

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
                    if key == "revisionId" or (tab_properties and key == "title"):
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
