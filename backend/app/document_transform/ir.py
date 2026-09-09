from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
import hashlib
import json
import re
from typing import Any
import unicodedata

from app.document_transform.errors import SourceTooLargeError, UnsupportedSourceError
from app.document_transform.limits import MAX_MARKDOWN_BYTES
from app.identifiers import DocsSegmentId, DocsTabId


MAX_DOC_UTF16_UNITS = 500_000
MAX_DOC_TABS = 50
MAX_DOCUMENT_BLOCKS = 750
_MIN_TABLE_COLUMNS = 2
_MAX_AUTHORED_LEAD_IN_LENGTH = 160
_MIN_TERMINAL_TEXT_RUN_COUNT = 2


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
class NativeContent:
    # Offsets refer to displayed paragraph text, not Google Docs indexes.
    # The exact non-style payload retains unknown properties and suggestions.
    kind: str
    text_offset: int
    display_text: str
    payload_json: str


@dataclass(frozen=True, slots=True)
class TextBlock:
    id: str
    kind: BlockKind
    text: str
    level: int | None = None
    source_range: SourceRange | None = None
    editable: bool = True
    # Formatting permission is independent from permission to replace prose.
    # These ranges permit only a font-family override, never a text replacement.
    font_style_ranges: tuple[SourceRange, ...] = ()
    native_content: tuple[NativeContent, ...] = ()
    native_named_style: str | None = None
    # Source provenance, not a semantic role. Retain the original text digest
    # through replacements so request reconstruction cannot mistake edited text
    # for an unchanged provider paragraph.
    source_named_style: str | None = None
    source_style_text_sha256: str | None = None
    native_bullet_json: str | None = None
    # Uniform authored emphasis is independent of the document's heading tree.
    authored_emphasis: tuple[tuple[str, bool], ...] = ()
    # Copy-only, durably attested layout expectation; never a semantic role.
    pagination_keep_with_next: bool | None = None
    suppress_space_above: bool = False
    suppress_space_below: bool = False
    # In-place only: exact source-bound paragraph layout, not a new semantic role.
    preserved_paragraph_style_sha256: str | None = None
    preserved_font_family: bool = False
    preserved_visual_authority: bool = False

    @property
    def is_authored_lead_in(self) -> bool:
        return (
            self.kind == BlockKind.PARAGRAPH
            and len(self.text) <= _MAX_AUTHORED_LEAD_IN_LENGTH
            and dict(self.authored_emphasis).get("bold") is True
        )

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
    copy_font_defended: bool = False

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
                        **_native_content_payload(block),
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
                    **_native_content_payload(block),
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


def _native_content_payload(block: TextBlock) -> dict[str, Any]:
    # Keep fingerprints of existing plain-text documents backward compatible.
    style = {"native_named_style": block.native_named_style} if block.native_named_style else {}
    if block.native_bullet_json is not None:
        style["native_bullet_json"] = block.native_bullet_json
    if not block.native_content:
        return style
    return {
        **style,
        "native_content": [
            {
                "kind": chip.kind,
                "offset": chip.text_offset,
                "display": chip.display_text,
                "payload": chip.payload_json,
            }
            for chip in block.native_content
        ],
    }


def verify_planned_date_conversions(
    original: DocumentIR,
    actual: DocumentIR,
    *,
    source_block_hashes: dict[str, str],
) -> bool:
    # Content-only check: callers still require revision, protected-object,
    # topology and checkpoint checks. Generic hashes never equate text/chips.
    if (
        original.title != actual.title
        or original.source_format != actual.source_format
        or original.tab_ids != actual.tab_ids
        or len(original.blocks) != len(actual.blocks)
        or not source_block_hashes.keys() <= {block.id for block in original.blocks}
    ):
        return False
    normalized: list[TextBlock] = []
    for before, after in zip(original.blocks, actual.blocks, strict=True):
        if before.id != after.id or before.kind != after.kind or before.level != after.level:
            return False
        if before.source_range is None or after.source_range is None:
            return False
        if (
            before.source_range.tab_id != after.source_range.tab_id
            or before.source_range.segment_id != after.source_range.segment_id
        ):
            return False
        if before.id in source_block_hashes:
            if before.sha256 != source_block_hashes[before.id] or not _is_exact_date_conversion(
                before, after
            ):
                return False
            normalized.append(replace(after, editable=before.editable, native_content=()))
        else:
            normalized.append(after)
    return original.semantic_sha256 == replace(actual, blocks=tuple(normalized)).semantic_sha256


def _is_exact_date_conversion(before: TextBlock, after: TextBlock) -> bool:
    if (
        not before.editable
        or before.native_content
        or before.kind != BlockKind.PARAGRAPH
        or before.text != after.text
        or len(after.native_content) != 1
    ):
        return False
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", before.text):
        return False
    try:
        date.fromisoformat(before.text)
    except ValueError:
        return False
    chip = after.native_content[0]
    if chip.kind != "dateElement" or chip.text_offset != 0 or chip.display_text != before.text:
        return False
    payload = json.loads(chip.payload_json)
    element = payload["element"]
    properties = element.get("dateElementProperties", {})
    return (
        not payload["outer"]
        and set(element) <= {"dateId", "dateElementProperties"}
        and set(properties)
        <= {"timestamp", "timeZoneId", "locale", "dateFormat", "timeFormat", "displayText"}
        and isinstance(properties.get("timestamp"), str)
        and re.fullmatch(re.escape(before.text) + r"T00:00:00(?:\.0+)?Z", properties["timestamp"])
        is not None
        and properties.get("timeZoneId") in {"Etc/UTC", "UTC"}
        and properties.get("locale", "en") == "en"
        and properties.get("dateFormat") == "DATE_FORMAT_ISO8601"
        and properties.get("timeFormat", "TIME_FORMAT_DISABLED") == "TIME_FORMAT_DISABLED"
        and properties.get("displayText") == before.text
    )


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
    native_content: list[NativeContent] = []
    contains_non_text = False
    for part in elements:
        if not isinstance(part, dict) or not isinstance(part.get("textRun"), dict):
            contains_non_text = True
        _append_paragraph_run(part, runs, native_content)
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
    bullet = paragraph_data.get("bullet")
    if "bullet" in paragraph_data and not isinstance(bullet, dict):
        raise UnsupportedSourceError
    if isinstance(bullet, dict) and classification.kind == BlockKind.PARAGRAPH:
        classification = BlockClassification(BlockKind.LIST_ITEM, None)
    editable = not contains_non_text and _paragraph_is_editable(
        runs, paragraph_data, start=start, end=end
    )
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
        native_bullet_json=json.dumps(bullet, sort_keys=True) if isinstance(bullet, dict) else None,
        native_content=tuple(native_content),
        authored_emphasis=_uniform_authored_emphasis(elements),
        source_named_style=(
            paragraph_style["namedStyleType"]
            if isinstance(paragraph_style.get("namedStyleType"), str)
            else None
        ),
        source_style_text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        native_named_style=(
            str(paragraph_style["namedStyleType"])
            if paragraph_style.get("namedStyleType") in {"TITLE", "SUBTITLE"}
            else None
        ),
        font_style_ranges=_safe_font_ranges(
            elements, paragraph_data, SourceRange(start, end - 1, tab_id)
        ),
    )


def _uniform_authored_emphasis(elements: list[Any]) -> tuple[tuple[str, bool], ...]:
    styles = [
        part["textRun"].get("textStyle", {})
        for part in elements
        if isinstance(part, dict)
        and isinstance(part.get("textRun"), dict)
        and str(part["textRun"].get("content", "")).strip()
    ]
    return tuple(
        (key, styles[0][key])
        for key in ("bold", "italic", "underline")
        if styles
        and all(isinstance(style, dict) for style in styles)
        and isinstance(styles[0].get(key), bool)
        and all(style.get(key) is styles[0][key] for style in styles)
    )


def _append_paragraph_run(
    part: object, runs: list[tuple[str, bool]], native_content: list[NativeContent]
) -> None:
    if not isinstance(part, dict):
        raise UnsupportedSourceError
    text_run = part.get("textRun")
    if not isinstance(text_run, dict):
        chip = _parse_native_content(part, sum(len(run[0]) for run in runs))
        if chip is not None:
            native_content.append(chip)
            runs.append((chip.display_text, True))
        return
    content = text_run.get("content")
    if not isinstance(content, str):
        raise UnsupportedSourceError
    locked = _text_run_is_locked(part, text_run.get("textStyle", {})) or not _font_run_is_safe(
        part, text_run
    )
    runs.append((content, locked))


def _parse_native_content(part: object, offset: int) -> NativeContent | None:
    if not isinstance(part, dict):
        raise UnsupportedSourceError
    for kind, properties_key, display_keys in (
        ("dateElement", "dateElementProperties", ("displayText",)),
        ("person", "personProperties", ("name", "email")),
        ("richLink", "richLinkProperties", ("title", "uri")),
    ):
        payload = part.get(kind)
        if kind not in part:
            continue
        if not isinstance(payload, dict):
            raise UnsupportedSourceError
        properties = payload.get(properties_key)
        if not isinstance(properties, dict):
            raise UnsupportedSourceError
        display = next((value for key in display_keys if (value := properties.get(key))), None)
        if not isinstance(display, str):
            raise UnsupportedSourceError
        # Styling can legitimately change; identity, suggestions and all future
        # unknown provider properties must survive byte-equivalently in JSON.
        protected = {key: value for key, value in payload.items() if key != "textStyle"}
        outer = {
            key: value for key, value in part.items() if key not in {"startIndex", "endIndex", kind}
        }
        return NativeContent(
            kind,
            offset,
            display,
            json.dumps(
                {"element": protected, "outer": outer}, sort_keys=True, separators=(",", ":")
            ),
        )
    return None


def _safe_font_ranges(
    elements: list[Any], paragraph: dict[str, Any], paragraph_range: SourceRange
) -> tuple[SourceRange, ...]:
    if (
        paragraph.get("suggestedParagraphStyleChanges")
        or paragraph.get("suggestedBulletChanges")
        or any(not isinstance(part, dict) or "textRun" not in part for part in elements)
    ):
        return ()
    ranges: list[SourceRange] = []
    for part in elements:
        run = part.get("textRun", {})
        if not isinstance(run, dict):
            continue
        content = run.get("content")
        start = part.get("startIndex")
        end = part.get("endIndex")
        if not isinstance(content, str) or not isinstance(start, int) or not isinstance(end, int):
            continue
        if (
            start < paragraph_range.start_index
            or end > paragraph_range.end_index + 1
            or end - start != utf16_length(content)
            or not _font_run_is_safe(part, run)
        ):
            continue
        # Do not include the paragraph terminator or let the range reach into
        # the next paragraph. Provider offsets count UTF-16 units, not letters.
        length = utf16_length(content.removesuffix("\n"))
        if length:
            ranges.append(SourceRange(start, start + length, paragraph_range.tab_id))
    return tuple(ranges)


def _font_run_is_safe(part: dict[Any, Any], run: dict[Any, Any]) -> bool:
    style = run.get("textStyle", {})
    if not isinstance(style, dict):
        return False
    if any(key.startswith("suggested") and value for key, value in part.items()):
        return False
    if any(key.startswith("suggested") and value for key, value in run.items()):
        return False
    family = style.get("weightedFontFamily", {})
    font = str(family.get("fontFamily", "")) if isinstance(family, dict) else ""
    if (
        any(
            token in font.casefold()
            for token in (
                "mono",
                "courier",
                "consolas",
                "menlo",
                "code",
                "math",
                "symbol",
                "monaco",
                "inconsolata",
                "andale",
                "lucida console",
                "cascadia",
            )
        )
        or style.get("baselineOffset", "NONE") != "NONE"
    ):
        return False
    # Nunito's Latin branding must not replace a script-specific typeface.
    return all(
        not character.isalpha() or "LATIN" in unicodedata.name(character, "")
        for character in str(run.get("content", ""))
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


def _paragraph_is_editable(
    runs: list[tuple[str, bool]], paragraph: dict[str, Any], *, start: int, end: int
) -> bool:
    has_suggestion = bool(
        paragraph.get("suggestedParagraphStyleChanges") or paragraph.get("suggestedBulletChanges")
    )
    return (
        (len(runs) == 1 or _terminal_text_run_shape(runs, paragraph, start=start, end=end))
        and not any(run[1] for run in runs)
        and not has_suggestion
    )


def _terminal_text_run_shape(
    runs: list[tuple[str, bool]], paragraph: dict[str, Any], *, start: int, end: int
) -> bool:
    # A separately styled paragraph terminator is not a second editable text
    # span. Docs may also split the visible text into adjacent, identically
    # styled runs after a formatting request. Accept only that representation;
    # distinct visible styles remain protected rich text.
    if (
        len(runs) < _MIN_TERMINAL_TEXT_RUN_COUNT
        or any(
            not text or any(character in text for character in "\n\r\v\f\u2028\u2029")
            for text, _locked in runs[:-1]
        )
        or runs[-1][0] != "\n"
    ):
        return False
    if isinstance(start, bool) or isinstance(end, bool) or start < 0:
        return False
    elements = paragraph.get("elements")
    if not isinstance(elements, list) or len(elements) != len(runs):
        return False
    visible_styles = [
        element.get("textRun", {}).get("textStyle", {})
        for element in elements[:-1]
        if isinstance(element, dict)
    ]
    if len(visible_styles) != len(elements) - 1 or any(
        style != visible_styles[0] for style in visible_styles[1:]
    ):
        return False
    cursor = start
    for element, (text, _) in zip(elements, runs, strict=True):
        if not isinstance(element, dict) or set(element) - {"startIndex", "endIndex", "textRun"}:
            return False
        text_run = element.get("textRun")
        if not isinstance(text_run, dict) or set(text_run) - {"content", "textStyle"}:
            return False
        run_start = element.get("startIndex")
        run_end = element.get("endIndex")
        if (
            not isinstance(run_start, int)
            or isinstance(run_start, bool)
            or not isinstance(run_end, int)
            or isinstance(run_end, bool)
        ):
            return False
        if run_start != cursor or run_end != cursor + utf16_length(text):
            return False
        cursor = run_end
    return cursor == end


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
