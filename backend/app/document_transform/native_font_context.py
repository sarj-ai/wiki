from dataclasses import dataclass
from typing import Any

from app.document_transform.ir import MAX_DOC_TABS
from app.identifiers import DocsTabId


_NAMED_STYLES = frozenset(
    {"NORMAL_TEXT", "TITLE", "SUBTITLE", *(f"HEADING_{n}" for n in range(1, 7))}
)
_WEIGHTS = frozenset(range(100, 901, 100))


@dataclass(frozen=True)
class NativeFontValues:
    bold: bool | None
    weight: int | None


def resolve_native_font_family(
    document: dict[str, Any],
    tab_id: str,
    paragraph_style: dict[str, Any],
    text_style: dict[str, Any],
) -> str | None:
    try:
        parents = _parents(document, tab_id, paragraph_style)
    except AttributeError, KeyError, TypeError, ValueError:
        return None
    if parents is None:
        return None
    for style in [text_style, *parents]:
        family = style.get("weightedFontFamily", {})
        if not isinstance(family, dict):
            return None
        if "fontFamily" in family:
            value = family["fontFamily"]
            return value if isinstance(value, str) and value else None
    return None


def resolve_native_font_values(
    document: dict[str, Any],
    tab_id: DocsTabId | str,
    paragraph_style: dict[str, Any],
    text_style: dict[str, Any],
) -> NativeFontValues:
    try:
        parents = _parents(document, str(tab_id), paragraph_style)
    except AttributeError, KeyError, TypeError, ValueError:
        parents = None
    if parents is None:
        return NativeFontValues(None, None)
    bold: bool | None = None
    weight: int | None = None
    for style in [text_style, *parents]:
        if "bold" in style:
            value = style["bold"]
            bold = value if type(value) is bool else None
            break
    for style in [text_style, *parents]:
        family = style.get("weightedFontFamily", {})
        if not isinstance(family, dict) or not set(family) <= {"fontFamily", "weight"}:
            break
        if "weight" in family:
            value = family["weight"]
            weight = value if type(value) is int and value in _WEIGHTS else None
            break
        if "fontFamily" in family:
            break
    return NativeFontValues(bold, weight)


def native_normal_text_style(document: dict[str, Any], tab_id: str) -> dict[str, Any] | None:
    parents = _parents(document, tab_id, {"namedStyleType": "NORMAL_TEXT"})
    return parents[0] if parents else None


def _parents(
    document: dict[str, Any], tab_id: str, paragraph_style: dict[str, Any]
) -> list[dict[str, Any]] | None:
    pending = list(document.get("tabs", []))
    matches = []
    visited = 0
    while pending:
        visited += 1
        if visited > MAX_DOC_TABS:
            return None
        tab = pending.pop()
        if not isinstance(tab, dict):
            return None
        if tab.get("tabProperties", {}).get("tabId") == tab_id:
            matches.append(tab.get("documentTab", {}).get("namedStyles"))
        pending.extend(tab.get("childTabs", []))
        if len(pending) > MAX_DOC_TABS:
            return None
    if len(matches) != 1:
        return None
    root, local = document.get("namedStyles"), matches[0]
    if root is not None and local is not None and root != local:
        return None
    value = local if local is not None else root
    if value is None:
        return []
    if not isinstance(value, dict) or set(value) != {"styles"}:
        return None
    styles = value["styles"]
    if not isinstance(styles, list) or not 0 < len(styles) <= len(_NAMED_STYLES):
        return None
    by_name: dict[str, dict[str, Any]] = {}
    for style in styles:
        if not isinstance(style, dict):
            return None
        name = style.get("namedStyleType")
        text = style.get("textStyle", {})
        if (
            not isinstance(name, str)
            or name not in _NAMED_STYLES
            or name in by_name
            or not isinstance(text, dict)
        ):
            return None
        by_name[name] = text
    name = paragraph_style.get("namedStyleType", "NORMAL_TEXT")
    if not isinstance(name, str) or name not in _NAMED_STYLES:
        return None
    if name not in by_name or "NORMAL_TEXT" not in by_name:
        return None
    return [by_name[key] for key in dict.fromkeys((name, "NORMAL_TEXT")) if key in by_name]
