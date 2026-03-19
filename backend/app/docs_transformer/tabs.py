"""Tab-aware document transformation."""

from __future__ import annotations

from app.docs_transformer.transform import transform_doc


def transform_doc_with_tabs(doc: dict) -> list[dict]:
    """Transform a Google Docs document with tabs support.

    Returns a list of tab dicts: [{"title": str, "blocks": [...]}, ...]
    For single-tab docs, returns one tab.  Falls back to document.body
    if no tabs are present (pre-tabs API responses or cached data).
    """
    tabs = doc.get("tabs", [])
    if not tabs:
        # Fallback: no tabs data — use document.body directly
        return [{"title": "", "blocks": transform_doc(doc)}]

    result: list[dict] = []
    _collect_tabs(tabs, result)
    return result


_MAX_TAB_DEPTH = 20


def _collect_tabs(tabs: list[dict], result: list[dict], depth: int = 0) -> None:
    """Recursively collect tab content (handles nested child tabs)."""
    if depth >= _MAX_TAB_DEPTH:
        return
    for tab in tabs:
        props = tab.get("tabProperties", {})
        title = props.get("title", "")
        tab_id = props.get("tabId", "")
        doc_tab = tab.get("documentTab", {})

        # Build a pseudo-document dict that transform_doc() can consume
        tab_doc = {
            "body": doc_tab.get("body", {}),
            "inlineObjects": doc_tab.get("inlineObjects", {}),
            "positionedObjects": doc_tab.get("positionedObjects", {}),
            "lists": doc_tab.get("lists", {}),
            "footnotes": doc_tab.get("footnotes", {}),
        }
        blocks = transform_doc(tab_doc)
        entry: dict = {"title": title, "blocks": blocks}
        if tab_id:
            entry["tabId"] = tab_id
        if depth > 0:
            entry["depth"] = depth
        result.append(entry)

        child_tabs = tab.get("childTabs", [])
        if child_tabs:
            _collect_tabs(child_tabs, result, depth + 1)
