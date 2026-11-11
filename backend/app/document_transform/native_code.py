from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from importlib.metadata import version
import re
from typing import TYPE_CHECKING

from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.code_syntax import CodeToken, format_code
from app.document_transform.native_code_source import (
    NativeCodeRecord,
    NativeCodeSourceGroup,
    NativeCodeSourceParagraph,
    code_color,
    code_index,
    code_json,
    code_parents,
    code_units,
    native_code_eligible,
    native_code_groups,
    protect_native_code as protect_native_code,
)
from app.document_transform.native_code_styles import (
    project_native_code_styles as _project,
    styled_paragraph,
    verify_native_code_styles as _verify,
)
from app.document_transform.native_font_proof import NativeFontBlockId
from app.identifiers import DocsTabId


if TYPE_CHECKING:
    from app.document_transform.ir import DocumentIR, TextBlock


type _BlockPairs = dict[tuple[DocsTabId, int], tuple[TextBlock, TextBlock]]


@dataclass(frozen=True, slots=True)
class NativeCodeColor:
    start: int
    end: int
    color: str


@dataclass(frozen=True, slots=True)
class NativeCodeParagraph:
    tab_id: DocsTabId
    body_ordinal: int
    source_start: int
    source_end: int
    target_start: int
    target_end: int
    block_id: NativeFontBlockId | None
    language: str | None
    source_json: str
    expected_json: str
    colors: tuple[NativeCodeColor, ...]


@dataclass(frozen=True, slots=True)
class NativeCodeStylePlan:
    syntax_version: str
    parents_sha256: str
    paragraphs: tuple[NativeCodeParagraph, ...]

    @property
    def block_ids(self) -> tuple[NativeFontBlockId, ...]:
        return tuple(item.block_id for item in self.paragraphs if item.block_id is not None)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(code_json(asdict(self)).encode()).hexdigest()

    @property
    def requests(self) -> list[NativeCodeRecord]:
        requests: list[NativeCodeRecord] = []
        for item in self.paragraphs:
            requests.append(
                {
                    "updateParagraphStyle": {
                        "range": _range(item, item.target_start, item.target_end),
                        "paragraphStyle": {
                            "keepLinesTogether": True,
                            "shading": {
                                "backgroundColor": code_color(CURRENT_BRAND_MANIFEST.colors.subtle)
                            },
                        },
                        "fields": "shading.backgroundColor,keepLinesTogether",
                    }
                }
            )
            requests.extend(
                {
                    "updateTextStyle": {
                        "range": _range(item, color.start, color.end),
                        "textStyle": {"foregroundColor": code_color(color.color)},
                        "fields": "foregroundColor",
                    }
                }
                for color in item.colors
            )
        return requests


def plan_native_code_styles(
    raw: NativeCodeRecord, original: DocumentIR, transformed: DocumentIR
) -> NativeCodeStylePlan:
    blocks = _block_pairs(original, transformed)
    planned = []
    for group in native_code_groups(raw):
        planned.extend(_plan_group(group, blocks, original, transformed))
    return NativeCodeStylePlan(
        f"native-code-v2/pygments-{version('Pygments')}", code_parents(raw), tuple(planned)
    )


def _block_pairs(original: DocumentIR, transformed: DocumentIR) -> _BlockPairs:
    if len(original.blocks) != len(transformed.blocks) or original.tab_ids != transformed.tab_ids:
        msg = "Native code plan requires unchanged block topology"
        raise ValueError(msg)
    result = {}
    for before, after in zip(original.blocks, transformed.blocks, strict=True):
        if before.id != after.id:
            msg = "Native code plan block identities changed"
            raise ValueError(msg)
        if before.source_range is not None:
            result[before.source_range.tab_id, before.source_range.start_index] = before, after
    return result


def _plan_group(
    group: NativeCodeSourceGroup, blocks: _BlockPairs, original: DocumentIR, transformed: DocumentIR
) -> list[NativeCodeParagraph]:
    if not group.complete or not all(
        native_code_eligible(item, fenced=group.fenced) for item in group.paragraphs
    ):
        return []
    body = group.paragraphs[1:-1] if group.fenced else group.paragraphs
    lexical_text = "".join(item.text or "" for item in body).replace("\v", "\n")
    syntax = format_code(lexical_text, group.language)
    if "".join(token.text for token in syntax.tokens) != lexical_text:
        msg = "Code syntax did not preserve the source"
        raise ValueError(msg)
    if syntax.language is None:
        return []
    colors = _token_colors(syntax.tokens, code_index(body[0].element["startIndex"])) if body else ()
    result = []
    for paragraph in group.paragraphs:
        start = code_index(paragraph.element["startIndex"])
        delta = _preceding_delta(original, transformed, paragraph.tab_id, start)
        result.append(_plan_paragraph(paragraph, blocks, colors, syntax.language, delta))
    return result


def _plan_paragraph(
    paragraph: NativeCodeSourceParagraph,
    blocks: _BlockPairs,
    colors: tuple[NativeCodeColor, ...],
    language: str,
    delta: int,
) -> NativeCodeParagraph:
    start, end = (
        code_index(paragraph.element["startIndex"]),
        code_index(paragraph.element["endIndex"]),
    )
    before, after = blocks.get((paragraph.tab_id, start), (None, None))
    if before is not None and (after is None or before.text != after.text or before.editable):
        msg = "Native code must remain protected and literal"
        raise ValueError(msg)
    selected = _paragraph_colors(colors, start, end, delta)
    expected = styled_paragraph(paragraph.element, selected, delta=delta)
    return NativeCodeParagraph(
        paragraph.tab_id,
        paragraph.ordinal,
        start,
        end,
        start + delta,
        end + delta,
        NativeFontBlockId(before.id) if before else None,
        language,
        code_json(paragraph.element),
        code_json(expected),
        selected,
    )


def _paragraph_colors(
    colors: tuple[NativeCodeColor, ...], start: int, end: int, delta: int
) -> tuple[NativeCodeColor, ...]:
    result = []
    for color in colors:
        left, right = max(start, color.start), min(end - 1, color.end)
        if left < right:
            result.append(NativeCodeColor(left + delta, right + delta, color.color))
    return tuple(result)


def project_native_code_styles(
    source: NativeCodeRecord, actual: NativeCodeRecord, plan: NativeCodeStylePlan
) -> NativeCodeRecord:
    return _project(source, actual, plan)


def verify_native_code_styles(
    source: NativeCodeRecord, actual: NativeCodeRecord, plan: NativeCodeStylePlan
) -> bool:
    return _verify(source, actual, plan)


def _preceding_delta(
    original: DocumentIR, transformed: DocumentIR, tab_id: DocsTabId, start: int
) -> int:
    return sum(
        code_units(after.text) - code_units(before.text)
        for before, after in zip(original.blocks, transformed.blocks, strict=True)
        if before.source_range is not None
        and before.source_range.tab_id == tab_id
        and before.source_range.end_index <= start
    )


def _token_colors(tokens: tuple[CodeToken, ...], start: int) -> tuple[NativeCodeColor, ...]:
    result = []
    cursor = start
    for token in tokens:
        for line in token.text.splitlines(keepends=True):
            visible = line.removesuffix("\n")
            end = cursor + code_units(visible)
            if token.color is not None and visible:
                if not re.fullmatch(r"[0-9A-Fa-f]{6}", token.color):
                    msg = "Unsupported code token color"
                    raise ValueError(msg)
                result.append(NativeCodeColor(cursor, end, token.color.upper()))
            cursor += code_units(line)
    return tuple(result)


def _range(item: NativeCodeParagraph, start: int, end: int) -> NativeCodeRecord:
    return {"tabId": item.tab_id, "startIndex": start, "endIndex": end}
