from copy import deepcopy
from dataclasses import replace
import json
from typing import Any

from app.document_transform.brand import SarjBrandProfile, build_copy_mutations
from app.document_transform.ir import DocumentIR, parse_google_document


def align_branded_bullet_metadata(actual: DocumentIR, expected: DocumentIR) -> DocumentIR:
    if len(actual.blocks) != len(expected.blocks):
        return actual
    if all(
        observed.native_bullet_json == planned.native_bullet_json
        for observed, planned in zip(actual.blocks, expected.blocks, strict=True)
    ):
        return actual
    requests = build_copy_mutations(
        expected, expected, profile=SarjBrandProfile(), headers=(), logo_uri=""
    )
    styles: dict[tuple[str, int, int], dict[str, Any]] = {}
    for request in requests:
        update = request.get("updateTextStyle", {})
        scope = update.get("range", {})
        if set(scope) == {"tabId", "startIndex", "endIndex"}:
            key = (scope["tabId"], scope["startIndex"], scope["endIndex"])
            styles.setdefault(key, {}).update(update.get("textStyle", {}))
    blocks = []
    for observed, planned in zip(actual.blocks, expected.blocks, strict=True):
        source = planned.source_range
        if (
            source is None
            or not planned.editable
            or observed.native_bullet_json is None
            or planned.native_bullet_json is None
        ):
            blocks.append(observed)
            continue
        permitted = styles.get((source.tab_id, source.start_index, source.end_index), {})
        original = json.loads(planned.native_bullet_json)
        current = json.loads(observed.native_bullet_json)
        if _only_planned_style_changes(original, current, permitted):
            if "textStyle" in original:
                current["textStyle"] = original["textStyle"]
            else:
                current.pop("textStyle", None)
            restored = json.dumps(current, sort_keys=True)
        else:
            restored = observed.native_bullet_json
        blocks.append(replace(observed, native_bullet_json=restored))
    return replace(actual, blocks=tuple(blocks))


def project_branded_bullet_styles(document: dict[str, Any], expected: DocumentIR) -> dict[str, Any]:
    actual = parse_google_document(document, title=expected.title)
    aligned = align_branded_bullet_metadata(actual, expected)
    replacements: dict[tuple[str, int], Any] = {
        (block.source_range.tab_id, block.source_range.start_index): json.loads(
            block.native_bullet_json
        )
        for block, original in zip(aligned.blocks, actual.blocks, strict=True)
        if block.source_range is not None
        and block.native_bullet_json is not None
        and block.native_bullet_json != original.native_bullet_json
    }
    if not replacements:
        return document
    projected = deepcopy(document)
    _project_tabs(projected.get("tabs", []), replacements)
    return projected


def _project_tabs(tabs: list[Any], replacements: dict[tuple[str, int], Any]) -> None:
    for tab in tabs:
        tab_id = tab.get("tabProperties", {}).get("tabId")
        for item in tab.get("documentTab", {}).get("body", {}).get("content", []):
            bullet = replacements.get((tab_id, item.get("startIndex")))
            if bullet is not None and isinstance(item.get("paragraph"), dict):
                item["paragraph"]["bullet"] = bullet
        _project_tabs(tab.get("childTabs", []), replacements)


def _only_planned_style_changes(
    original: dict[str, Any], current: dict[str, Any], permitted: dict[str, Any]
) -> bool:
    # Docs UpdateTextStyle can extend to the paragraph newline and apply the
    # same fields to its bullet. Only those exact emitted values are projected;
    # identities, nesting, links, unknown properties and all other styles stay
    # part of the strict baseline. List definitions are never projected.
    before = dict(original)
    after = dict(current)
    before_style = before.pop("textStyle", {})
    after_style = after.pop("textStyle", {})
    # Preserve the observed ID verbatim in the projected result. COPY may prove
    # an independent bijection later; in-place inventory and IR still reject an
    # identity change. A style projection must not itself authorize renaming.
    before.pop("listId", None)
    after.pop("listId", None)
    if before != after or not isinstance(before_style, dict) or not isinstance(after_style, dict):
        return False
    return all(
        (
            field in before_style
            and field in after_style
            and before_style[field] == after_style[field]
        )
        or (field in permitted and field in after_style and after_style[field] == permitted[field])
        for field in before_style.keys() | after_style.keys()
    )
