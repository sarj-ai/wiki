from copy import deepcopy
from dataclasses import replace
import json
from typing import Any

from app.document_transform.ir import DocumentIR


def align_verified_copy_list_metadata(actual: DocumentIR, expected: DocumentIR) -> DocumentIR:
    # Call only after the complete protected inventory proves a bijection. This
    # does not prove list equivalence by itself; it aligns redundant IR metadata.
    if len(actual.blocks) != len(expected.blocks):
        return actual
    blocks = []
    for observed, planned in zip(actual.blocks, expected.blocks, strict=True):
        if observed.native_bullet_json is None or planned.native_bullet_json is None:
            blocks.append(observed)
            continue
        observed_bullet = json.loads(observed.native_bullet_json)
        planned_bullet = json.loads(planned.native_bullet_json)
        observed_bullet.pop("listId", None)
        planned_bullet.pop("listId", None)
        blocks.append(
            replace(observed, native_bullet_json=planned.native_bullet_json)
            if observed_bullet == planned_bullet
            else observed
        )
    return replace(actual, blocks=tuple(blocks))


def normalize_copy_list_identity(document: dict[str, Any]) -> dict[str, Any] | None:
    # COPY-only alpha equivalence: native list names are local references, not
    # content. Never use this projection for in-place revisions or checkpoints.
    normalized = deepcopy(document)
    tabs = normalized.get("tabs")
    if isinstance(tabs, list):
        if not _normalize_tabs(tabs):
            return None
    elif not _normalize_tab_lists(normalized):
        return None
    return normalized


def _normalize_tabs(tabs: list[Any]) -> bool:
    for tab in tabs:
        if not isinstance(tab, dict):
            return False
        document_tab = tab.get("documentTab")
        if isinstance(document_tab, dict) and not _normalize_tab_lists(document_tab):
            return False
        children = tab.get("childTabs")
        if isinstance(children, list) and not _normalize_tabs(children):
            return False
    return True


def _normalize_tab_lists(tab: dict[str, Any]) -> bool:
    definitions = tab.get("lists", {})
    if not isinstance(definitions, dict):
        return False
    identities: dict[str, str] = {}
    # Semantic traversal order, never provider map-key order, establishes the
    # bijection. One source identity cannot become two lists or vice versa.
    for field in ("body", "footnotes", "footers", "headers"):
        _collect_list_references(tab.get(field), identities)
    if any(identifier not in definitions for identifier in identities):
        return False
    # Unreferenced definitions are intentionally retained byte-for-byte. They
    # cannot silently disappear or acquire a different identity in this proof.
    if any(identifier not in identities for identifier in definitions):
        return False
    if any(
        not isinstance(definition, dict) or definition.get("listId", identifier) != identifier
        for identifier, definition in definitions.items()
    ):
        return False
    if not identities:
        return True
    for field in ("body", "footnotes", "footers", "headers"):
        if field in tab:
            tab[field] = _replace_list_references(tab[field], identities)
    tab["lists"] = {
        identities[identifier]: _replace_list_references(definition, identities)
        for identifier, definition in definitions.items()
    }
    return True


def _collect_list_references(value: object, identities: dict[str, str]) -> None:
    match value:
        case dict():
            identifier = value.get("listId")
            if isinstance(identifier, str) and identifier not in identities:
                identities[identifier] = f"copy-list-{len(identities)}"
            fields: dict[str, object] = {
                key: child for key, child in value.items() if isinstance(key, str)
            }
            for key in sorted(fields):
                _collect_list_references(fields[key], identities)
        case list():
            for child in value:
                _collect_list_references(child, identities)
        case _:
            pass


def _replace_list_references(value: object, identities: dict[str, str]) -> object:
    match value:
        case dict():
            return {
                key: identities.get(child, child)
                if key == "listId" and isinstance(child, str)
                else _replace_list_references(child, identities)
                for key, child in value.items()
            }
        case list():
            return [_replace_list_references(child, identities) for child in value]
        case _:
            return value
