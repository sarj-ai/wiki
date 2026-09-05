from dataclasses import replace
import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app.document_transform.errors import OutputConflictError
from app.document_transform.ir import BlockKind, DocumentIR, TextBlock, utf16_length


MAX_PAGINATION_RULES = 60
_MAX_NATIVE_ELEMENTS = 2_000
_MAX_LEAD_CHARS = 120
_MAX_ITEM_CHARS = 240
_MAX_BODY_CHARS = 1_000
_MAX_BULLET_BYTES = 8_192


class NativePaginationRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    block_id: str = Field(min_length=1, max_length=64)
    text_sha256: str = Field(pattern="^[0-9a-f]{64}$")
    keep_with_next: StrictBool


def plan_native_pagination(
    native: dict[str, Any] | None, document: DocumentIR
) -> tuple[NativePaginationRule, ...]:
    # Never derive adjacency from flattened IR: it omits empty paragraphs and
    # section boundaries. Initial scope is a single flat native body only.
    if native is None or document.source_format != "google_doc" or len(document.tab_ids) != 1:
        return ()
    tabs = native.get("tabs")
    if not isinstance(tabs, list) or len(tabs) != 1 or not isinstance(tabs[0], dict):
        return ()
    tab = tabs[0]
    properties = tab.get("tabProperties")
    if (
        tab.get("childTabs", []) != []
        or not isinstance(properties, dict)
        or properties.get("tabId") != document.tab_ids[0]
    ):
        return ()
    body = tab.get("documentTab")
    if not isinstance(body, dict) or not _page_boundaries_known(native, body):
        return ()
    native_body = body.get("body")
    if not isinstance(native_body, dict):
        return ()
    content = native_body.get("content")
    if not isinstance(content, list) or len(content) > _MAX_NATIVE_ELEMENTS:
        return ()
    located = [
        block
        for block in document.blocks
        if block.source_range is not None
        and block.source_range.segment_id is None
        and block.source_range.tab_id == document.tab_ids[0]
    ]
    by_start = {
        block.source_range.start_index: block for block in located if block.source_range is not None
    }
    if len(by_start) != len(located):
        return ()
    mapped = [_body_paragraph(element, by_start) for element in content]
    rules: dict[str, NativePaginationRule] = {}
    for first, second, third in zip(mapped, mapped[1:], mapped[2:], strict=False):
        if first is None or second is None or third is None:
            continue
        if not _contiguous(first, second) or not _contiguous(second, third):
            continue
        group: tuple[tuple[TextBlock, bool], ...]
        if all(
            (
                first.kind is BlockKind.HEADING,
                len(first.text) <= _MAX_LEAD_CHARS,
                second.is_authored_lead_in,
                len(second.text) <= _MAX_LEAD_CHARS,
                third.kind is BlockKind.PARAGRAPH,
                not third.is_authored_lead_in,
                len(third.text) <= _MAX_BODY_CHARS,
            )
        ):
            group = ((first, True), (second, True))
        elif all(
            (
                first.kind is BlockKind.HEADING or first.is_authored_lead_in,
                len(first.text) <= _MAX_LEAD_CHARS,
                second.kind is BlockKind.LIST_ITEM,
                third.kind is BlockKind.LIST_ITEM,
                len(second.text) <= _MAX_ITEM_CHARS,
                len(third.text) <= _MAX_ITEM_CHARS,
                _same_list(second, third),
            )
        ):
            # Only the opening pair is linked. Item two explicitly ends it.
            group = ((first, True), (second, True), (third, False))
        else:
            continue
        for block, keep in group:
            rule = NativePaginationRule(
                block_id=block.id, text_sha256=block.sha256, keep_with_next=keep
            )
            if block.id in rules and rules[block.id] != rule:
                return ()
            rules[block.id] = rule
        if len(rules) > MAX_PAGINATION_RULES:
            return ()
    return tuple(rules[block.id] for block in document.blocks if block.id in rules)


def _page_boundaries_known(native: dict[str, Any], body: dict[str, Any]) -> bool:
    for scope in (native, body):
        style = scope.get("documentStyle", {})
        if not isinstance(style, dict):
            return False
        document_format = style.get("documentFormat", {})
        if not isinstance(document_format, dict) or (
            document_format.get("documentMode") is not None
            and document_format.get("documentMode") != "PAGES"
        ):
            return False
        named = scope.get("namedStyles", {})
        if (
            not isinstance(named, dict)
            or not isinstance(named.get("styles", []), list)
            or len(named.get("styles", [])) > MAX_PAGINATION_RULES
        ):
            return False
        for entry in named.get("styles", []):
            if not isinstance(entry, dict):
                return False
            paragraph = entry.get("paragraphStyle", {})
            if (
                not isinstance(paragraph, dict)
                or paragraph.get("pageBreakBefore", False) is not False
            ):
                return False
    return True


def _body_paragraph(element: object, by_start: dict[int, TextBlock]) -> TextBlock | None:
    if not isinstance(element, dict) or set(element) - {"startIndex", "endIndex", "paragraph"}:
        return None
    start, end = element.get("startIndex"), element.get("endIndex")
    if type(start) is not int or type(end) is not int or start < 0 or end <= start:
        return None
    paragraph = element.get("paragraph")
    block = by_start.get(start)
    if not isinstance(paragraph, dict) or set(paragraph) - {"elements", "paragraphStyle", "bullet"}:
        return None
    if block is None or not block.editable or block.native_content or not block.text.strip():
        return None
    if block.source_range is None or block.source_range.end_index != end - 1:
        return None
    style = paragraph.get("paragraphStyle", {})
    elements = paragraph.get("elements")
    if (
        not isinstance(style, dict)
        or style.get("pageBreakBefore", False) is not False
        or not isinstance(elements, list)
        or not elements
    ):
        return None
    if any(character in block.text for character in "\n\r\v\f\u2028\u2029"):
        return None
    runs: list[str] = []
    for item in elements:
        if not isinstance(item, dict) or set(item) - {"startIndex", "endIndex", "textRun"}:
            return None
        run = item.get("textRun")
        if not isinstance(run, dict):
            return None
        text = run.get("content")
        if not isinstance(text, str):
            return None
        runs.append(text)
    raw_text = "".join(runs).removesuffix("\n")
    if (
        hashlib.sha256(raw_text.encode()).hexdigest() != block.source_style_text_sha256
        or utf16_length(raw_text) != end - start - 1
        or any(character in raw_text for character in "\n\r\v\f\u2028\u2029")
    ):
        return None
    return block


def _contiguous(first: TextBlock, second: TextBlock) -> bool:
    return bool(
        first.source_range is not None
        and second.source_range is not None
        and first.source_range.end_index + 1 == second.source_range.start_index
    )


def _same_list(first: TextBlock, second: TextBlock) -> bool:
    # The parser preserves complete bullet payloads, including list identity,
    # nesting and emphasis. Requiring equality is deliberately conservative.
    if (
        first.native_bullet_json is None
        or first.native_bullet_json != second.native_bullet_json
        or len(first.native_bullet_json) > _MAX_BULLET_BYTES
    ):
        return False
    try:
        bullet = json.loads(first.native_bullet_json)
    except ValueError:
        return False
    return (
        isinstance(bullet, dict)
        and isinstance(bullet.get("listId"), str)
        and bool(bullet["listId"])
        and type(bullet.get("nestingLevel", 0)) is int
        and bullet.get("nestingLevel", 0) >= 0
    )


def apply_native_pagination(
    document: DocumentIR, rules: tuple[NativePaginationRule, ...]
) -> DocumentIR:
    by_id = {rule.block_id: rule for rule in rules}
    if len(by_id) != len(rules) or set(by_id) - {block.id for block in document.blocks}:
        raise OutputConflictError
    blocks = []
    for block in document.blocks:
        rule = by_id.get(block.id)
        if rule is not None:
            if block.sha256 != rule.text_sha256:
                raise OutputConflictError
            blocks.append(replace(block, pagination_keep_with_next=rule.keep_with_next))
        else:
            blocks.append(block)
    return replace(document, blocks=tuple(blocks))
