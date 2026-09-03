from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib
import re
from typing import Any

from app.document_transform.errors import SourceTooLargeError, UnsupportedSourceError
from app.identifiers import DocsSegmentId, DocsTabId


MAX_DOC_UTF16_UNITS = 500_000
MAX_DOC_TABS = 50
MAX_DRIVE_MARKDOWN_BYTES = 2 * 1024 * 1024


class BlockKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    CHECKLIST = "checklist"
    QUOTE = "quote"
    CODE = "code"
    TABLE = "table"
    BLANK = "blank"


@dataclass(frozen=True, slots=True)
class SourceRange:
    start_index: int
    end_index: int
    tab_id: DocsTabId
    segment_id: DocsSegmentId | None = None


@dataclass(frozen=True, slots=True)
class TextBlock:
    id: str
    kind: BlockKind
    text: str
    level: int | None = None
    source_range: SourceRange | None = None
    editable: bool = True

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class DocumentIR:
    title: str
    blocks: tuple[TextBlock, ...]
    source_format: str

    @property
    def text_sha256(self) -> str:
        payload = "\n".join(f"{block.kind}:{block.text}" for block in self.blocks)
        return hashlib.sha256(payload.encode()).hexdigest()

    def replace_blocks(self, replacements: dict[str, str]) -> DocumentIR:
        return replace(
            self,
            blocks=tuple(
                replace(block, text=replacements.get(block.id, block.text)) for block in self.blocks
            ),
        )


_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST = re.compile(r"^\s*(?:[-*+] |\d+[.)] )(.*)$")


def parse_markdown(content: str, *, title: str) -> DocumentIR:
    if len(content.encode()) > MAX_DRIVE_MARKDOWN_BYTES:
        raise SourceTooLargeError
    blocks: list[TextBlock] = []
    in_fence = False
    fence_lines: list[str] = []
    for line in content.splitlines():
        if line.lstrip().startswith("```"):
            fence_lines.append(line)
            in_fence = not in_fence
            if not in_fence:
                _append_block(blocks, BlockKind.CODE, "\n".join(fence_lines), editable=False)
                fence_lines = []
            continue
        if in_fence:
            fence_lines.append(line)
            continue
        if (heading := _HEADING.match(line)) is not None:
            _append_block(
                blocks,
                BlockKind.HEADING,
                heading.group(2),
                level=len(heading.group(1)),
            )
            continue
        if (list_item := _LIST.match(line)) is not None:
            item_text = list_item.group(1)
            if item_text.startswith(("[ ] ", "[x] ", "[X] ")):
                _append_block(blocks, BlockKind.CHECKLIST, item_text[4:])
            else:
                _append_block(blocks, BlockKind.LIST_ITEM, item_text)
            continue
        if line.startswith("> "):
            _append_block(blocks, BlockKind.QUOTE, line[2:])
        elif line.startswith("|") and line.endswith("|"):
            _append_block(blocks, BlockKind.TABLE, line, editable=False)
        elif not line.strip():
            _append_block(blocks, BlockKind.BLANK, "", editable=False)
        else:
            _append_block(blocks, BlockKind.PARAGRAPH, line)
    if in_fence:
        raise UnsupportedSourceError
    return DocumentIR(title=title, blocks=tuple(blocks), source_format="markdown")


def parse_google_document(document: dict[str, Any], *, title: str) -> DocumentIR:
    revision_id = document.get("revisionId")
    if not isinstance(revision_id, str) or not revision_id:
        raise UnsupportedSourceError
    tabs = document.get("tabs")
    if not isinstance(tabs, list) or not tabs:
        raise UnsupportedSourceError
    flattened = list(_walk_tabs(tabs))
    if len(flattened) > MAX_DOC_TABS:
        raise SourceTooLargeError
    blocks: list[TextBlock] = []
    total_units = 0
    for tab in flattened:
        tab_id = _tab_id(tab)
        body = tab.get("documentTab", {}).get("body", {})
        content = body.get("content", []) if isinstance(body, dict) else []
        if not isinstance(content, list):
            raise UnsupportedSourceError
        for element in content:
            parsed = _parse_structural_element(element, tab_id, len(blocks))
            if parsed is None:
                continue
            total_units += utf16_length(parsed.text)
            if total_units > MAX_DOC_UTF16_UNITS:
                raise SourceTooLargeError
            blocks.append(parsed)
    return DocumentIR(title=title, blocks=tuple(blocks), source_format="google_doc")


def utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def rewrite_markdown(
    source: str,
    original: DocumentIR,
    transformed: DocumentIR,
) -> str:
    transformed_by_id = {block.id: block for block in transformed.blocks}
    replacements: list[tuple[int, int, str]] = []
    cursor = 0
    for block in original.blocks:
        if not block.text:
            continue
        start = source.find(block.text, cursor)
        if start < 0:
            raise UnsupportedSourceError
        end = start + len(block.text)
        cursor = end
        replacement = transformed_by_id[block.id].text
        if replacement != block.text:
            replacements.append((start, end, replacement))
    output = source
    for start, end, replacement in reversed(replacements):
        output = f"{output[:start]}{replacement}{output[end:]}"
    return output


def _append_block(
    blocks: list[TextBlock],
    kind: BlockKind,
    text: str,
    *,
    level: int | None = None,
    editable: bool = True,
) -> None:
    blocks.append(
        TextBlock(
            id=f"b{len(blocks)}",
            kind=kind,
            text=text,
            level=level,
            editable=editable,
        )
    )


def _walk_tabs(tabs: list[Any]) -> Iterator[dict[str, Any]]:
    for candidate in tabs:
        if not isinstance(candidate, dict):
            raise UnsupportedSourceError
        yield candidate
        children = candidate.get("childTabs", [])
        if not isinstance(children, list):
            raise UnsupportedSourceError
        yield from _walk_tabs(children)


def _tab_id(tab: dict[str, Any]) -> DocsTabId:
    properties = tab.get("tabProperties")
    tab_id = properties.get("tabId") if isinstance(properties, dict) else None
    if not isinstance(tab_id, str) or not tab_id:
        raise UnsupportedSourceError
    return DocsTabId(tab_id)


def _parse_structural_element(
    element: object,
    tab_id: DocsTabId,
    ordinal: int,
) -> TextBlock | None:
    if not isinstance(element, dict):
        raise UnsupportedSourceError
    paragraph = element.get("paragraph")
    if paragraph is None:
        return None
    if not isinstance(paragraph, dict):
        raise UnsupportedSourceError
    elements = paragraph.get("elements", [])
    if not isinstance(elements, list):
        raise UnsupportedSourceError
    runs: list[tuple[str, bool]] = []
    for part in elements:
        text_run = part.get("textRun") if isinstance(part, dict) else None
        if not isinstance(text_run, dict):
            return None
        content = text_run.get("content")
        if not isinstance(content, str):
            raise UnsupportedSourceError
        style = text_run.get("textStyle", {})
        locked = isinstance(style, dict) and "link" in style
        runs.append((content, locked))
    text = "".join(run[0] for run in runs).removesuffix("\n")
    if not text:
        return None
    start = element.get("startIndex")
    end = element.get("endIndex")
    if not isinstance(start, int) or not isinstance(end, int) or end <= start:
        raise UnsupportedSourceError
    paragraph_style = paragraph.get("paragraphStyle", {})
    if not isinstance(paragraph_style, dict):
        raise UnsupportedSourceError
    named_style = paragraph_style.get("namedStyleType", "NORMAL_TEXT")
    kind = BlockKind.HEADING if str(named_style).startswith("HEADING_") else BlockKind.PARAGRAPH
    level = int(str(named_style).rsplit("_", maxsplit=1)[-1]) if kind == BlockKind.HEADING else None
    editable = len(runs) == 1 and not any(run[1] for run in runs)
    return TextBlock(
        id=f"b{ordinal}",
        kind=kind,
        text=text,
        level=level,
        source_range=SourceRange(
            start_index=start,
            end_index=end - 1,
            tab_id=tab_id,
        ),
        editable=editable,
    )
