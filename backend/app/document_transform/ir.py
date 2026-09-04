from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib
import json
import re
from typing import Any

from app.document_transform.errors import SourceTooLargeError, UnsupportedSourceError
from app.document_transform.limits import MAX_MARKDOWN_BYTES
from app.identifiers import DocsSegmentId, DocsTabId


MAX_DOC_UTF16_UNITS = 500_000
MAX_DOC_TABS = 50
MAX_DOCUMENT_BLOCKS = 750
_MIN_TABLE_COLUMNS = 2


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
class BlockClassification:
    kind: BlockKind
    level: int | None


@dataclass(frozen=True, slots=True)
class DocumentIR:
    title: str
    blocks: tuple[TextBlock, ...]
    source_format: str
    tab_ids: tuple[DocsTabId, ...] = ()

    @property
    def text_sha256(self) -> str:
        payload = json.dumps(
            {
                "blocks": [
                    {
                        "editable": block.editable,
                        "id": block.id,
                        "kind": block.kind,
                        "level": block.level,
                        "source_range": (
                            {
                                "end": block.source_range.end_index,
                                "segment": block.source_range.segment_id,
                                "start": block.source_range.start_index,
                                "tab": block.source_range.tab_id,
                            }
                            if block.source_range is not None
                            else None
                        ),
                        "text": block.text,
                    }
                    for block in self.blocks
                ],
                "source_format": self.source_format,
                "tab_ids": self.tab_ids,
                "title": self.title,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def replace_blocks(self, replacements: dict[str, str]) -> DocumentIR:
        return replace(
            self,
            blocks=tuple(
                replace(block, text=replacements.get(block.id, block.text)) for block in self.blocks
            ),
        )

    @property
    def semantic_sha256(self) -> str:
        payload = json.dumps(
            [
                {
                    "editable": block.editable,
                    "kind": block.kind,
                    "level": block.level,
                    "text": block.text,
                }
                for block in self.blocks
            ],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST = re.compile(r"^\s*(?:[-*+] |\d+[.)] )(.*)$")


def parse_markdown(content: str, *, title: str) -> DocumentIR:
    try:
        encoded = content.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise UnsupportedSourceError from exc
    if len(encoded) > MAX_MARKDOWN_BYTES:
        raise SourceTooLargeError
    lines = content.splitlines()
    if any(_looks_like_raw_html(line) for line in lines):
        raise UnsupportedSourceError
    protected_lines = _frontmatter_lines(lines) | _table_lines(lines)
    blocks: list[TextBlock] = []
    fence_marker: str | None = None
    fence_lines: list[str] = []
    for line_index, line in enumerate(lines):
        stripped = line.lstrip()
        fence = re.match(r"(`{3,}|~{3,})", stripped)
        is_closing_fence = (
            fence_marker is not None
            and fence is not None
            and fence.group(1)[0] == fence_marker[0]
            and len(fence.group(1)) >= len(fence_marker)
            and not stripped[len(fence.group(1)) :].strip()
        )
        if fence_marker is None and fence is not None:
            fence_lines.append(line)
            fence_marker = fence.group(1)
            continue
        if is_closing_fence:
            fence_lines.append(line)
            _append_block(blocks, BlockKind.CODE, "\n".join(fence_lines), editable=False)
            fence_lines = []
            fence_marker = None
            continue
        if fence_marker is not None:
            fence_lines.append(line)
            continue
        if line_index in protected_lines:
            _append_block(blocks, BlockKind.TABLE, line, editable=False)
            continue
        if line.startswith(("    ", "\t")) and line.strip():
            _append_block(blocks, BlockKind.CODE, line, editable=False)
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
        if (quote := re.match(r"^ {0,3}> ?(.*)$", line)) is not None:
            _append_block(blocks, BlockKind.QUOTE, quote.group(1), editable=False)
        elif line.startswith("|") and line.endswith("|"):
            _append_block(blocks, BlockKind.TABLE, line, editable=False)
        elif not line.strip():
            _append_block(blocks, BlockKind.BLANK, "", editable=False)
        else:
            _append_block(blocks, BlockKind.PARAGRAPH, line)
    if fence_marker is not None:
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
            if len(blocks) >= MAX_DOCUMENT_BLOCKS:
                raise SourceTooLargeError
            blocks.append(parsed)
    return DocumentIR(
        title=title,
        blocks=tuple(blocks),
        source_format="google_doc",
        tab_ids=tuple(_tab_id(tab) for tab in flattened),
    )


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
    if len(blocks) >= MAX_DOCUMENT_BLOCKS:
        raise SourceTooLargeError
    blocks.append(
        TextBlock(
            id=f"b{len(blocks)}",
            kind=kind,
            text=text,
            level=level,
            editable=editable,
        )
    )


def _looks_like_raw_html(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("<!--") or re.match(r"</?[A-Za-z][^>]*>", stripped) is not None


def _frontmatter_lines(lines: list[str]) -> set[int]:
    if not lines or lines[0].strip() != "---":
        return set()
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() in {"---", "..."}:
            return set(range(index + 1))
    raise UnsupportedSourceError


def _table_lines(lines: list[str]) -> set[int]:
    protected: set[int] = set()
    index = 0
    while index + 1 < len(lines):
        if _is_table_row(lines[index]) and _is_table_separator(lines[index + 1]):
            protected.update({index, index + 1})
            cursor = index + 2
            while cursor < len(lines) and _is_table_row(lines[cursor]):
                protected.add(cursor)
                cursor += 1
            index = cursor
            continue
        index += 1
    return protected


def _is_table_row(line: str) -> bool:
    return "|" in line and len(_table_cells(line)) >= _MIN_TABLE_COLUMNS


def _is_table_separator(line: str) -> bool:
    cells = _table_cells(line)
    return len(cells) >= _MIN_TABLE_COLUMNS and all(
        re.fullmatch(r":?-{3,}:?", cell) for cell in cells
    )


def _table_cells(line: str) -> tuple[str, ...]:
    return tuple(cell.strip() for cell in line.strip().strip("|").split("|"))


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
    paragraph_data: dict[str, Any] = {str(key): value for key, value in paragraph.items()}
    elements = paragraph_data.get("elements", [])
    if not isinstance(elements, list):
        raise UnsupportedSourceError
    runs: list[tuple[str, bool]] = []
    contains_non_text = False
    for part in elements:
        text_run = part.get("textRun") if isinstance(part, dict) else None
        if not isinstance(text_run, dict):
            contains_non_text = True
            continue
        content = text_run.get("content")
        if not isinstance(content, str):
            raise UnsupportedSourceError
        style = text_run.get("textStyle", {})
        locked = _text_run_is_locked(part, style)
        runs.append((content, locked))
    text = "".join(run[0] for run in runs).removesuffix("\n")
    if not text and contains_non_text:
        text = "\ufffc"
    if not text:
        return None
    start = element.get("startIndex")
    end = element.get("endIndex")
    if not isinstance(start, int) or not isinstance(end, int) or end <= start:
        raise UnsupportedSourceError
    paragraph_style = paragraph_data.get("paragraphStyle", {})
    if not isinstance(paragraph_style, dict):
        raise UnsupportedSourceError
    classification = _classify_block(paragraph_style)
    editable = not contains_non_text and _paragraph_is_editable(runs, paragraph_data)
    return TextBlock(
        id=f"b{ordinal}",
        kind=classification.kind,
        text=text,
        level=classification.level,
        source_range=SourceRange(
            start_index=start,
            end_index=end - 1,
            tab_id=tab_id,
        ),
        editable=editable,
    )


def _text_run_is_locked(part: object, style: object) -> bool:
    style_dict = style if isinstance(style, dict) else {}
    weighted_family = style_dict.get("weightedFontFamily", {})
    font_family = weighted_family.get("fontFamily", "") if isinstance(weighted_family, dict) else ""
    has_suggestion = bool(
        isinstance(part, dict)
        and (part.get("suggestedInsertionIds") or part.get("suggestedDeletionIds"))
    )
    return (
        "link" in style_dict
        or str(font_family).casefold()
        in {"consolas", "courier new", "menlo", "monospace", "roboto mono"}
        or has_suggestion
    )


def _paragraph_is_editable(runs: list[tuple[str, bool]], paragraph: dict[str, Any]) -> bool:
    has_suggestion = bool(
        paragraph.get("suggestedParagraphStyleChanges") or paragraph.get("suggestedBulletChanges")
    )
    return len(runs) == 1 and not any(run[1] for run in runs) and not has_suggestion


def _classify_block(paragraph_style: dict[object, object]) -> BlockClassification:
    named_style = str(paragraph_style.get("namedStyleType", "NORMAL_TEXT"))
    if named_style.startswith("HEADING_"):
        return BlockClassification(
            BlockKind.HEADING,
            int(named_style.rsplit("_", maxsplit=1)[-1]),
        )
    if named_style == "TITLE":
        return BlockClassification(BlockKind.HEADING, 1)
    if named_style == "SUBTITLE":
        return BlockClassification(BlockKind.HEADING, 2)
    return BlockClassification(BlockKind.PARAGRAPH, None)
