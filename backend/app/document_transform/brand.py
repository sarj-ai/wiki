from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.errors import SourceTooLargeError
from app.document_transform.gateway import TabHeader
from app.document_transform.ir import BlockKind, DocumentIR, SourceRange, TextBlock, utf16_length
from app.identifiers import DocsTabId


SARJ_PURPLE = CURRENT_BRAND_MANIFEST.colors.primary
MAX_DOCS_MUTATION_REQUESTS = 2_500
MAX_DOCS_MUTATION_BYTES = 5 * 1024 * 1024
type DocsRequest = dict[str, Any]


@dataclass(frozen=True, slots=True)
class SarjBrandProfile:
    version: str = CURRENT_BRAND_MANIFEST.profile_id
    sha256: str = CURRENT_BRAND_MANIFEST.sha256
    primary_color: str = CURRENT_BRAND_MANIFEST.colors.primary
    ink_color: str = CURRENT_BRAND_MANIFEST.colors.ink
    muted_color: str = CURRENT_BRAND_MANIFEST.colors.muted
    border_color: str = CURRENT_BRAND_MANIFEST.colors.border
    subtle_color: str = CURRENT_BRAND_MANIFEST.colors.subtle
    font_family: str = CURRENT_BRAND_MANIFEST.document.font_family
    body_size_pt: int = CURRENT_BRAND_MANIFEST.document.body_size_pt
    h1_size_pt: int = CURRENT_BRAND_MANIFEST.document.h1_size_pt
    h2_size_pt: int = CURRENT_BRAND_MANIFEST.document.h2_size_pt
    h3_size_pt: int = CURRENT_BRAND_MANIFEST.document.h3_size_pt
    caption_size_pt: int = CURRENT_BRAND_MANIFEST.document.caption_size_pt
    line_spacing_percent: int = CURRENT_BRAND_MANIFEST.document.line_spacing_percent
    paragraph_space_above_pt: int = CURRENT_BRAND_MANIFEST.document.paragraph_space_above_pt
    paragraph_space_below_pt: int = CURRENT_BRAND_MANIFEST.document.paragraph_space_below_pt
    horizontal_margin_pt: int = CURRENT_BRAND_MANIFEST.document.horizontal_margin_pt
    vertical_margin_pt: int = CURRENT_BRAND_MANIFEST.document.vertical_margin_pt

    def __post_init__(self) -> None:
        expected = {
            "version": CURRENT_BRAND_MANIFEST.profile_id,
            "sha256": CURRENT_BRAND_MANIFEST.sha256,
            "primary_color": CURRENT_BRAND_MANIFEST.colors.primary,
            "ink_color": CURRENT_BRAND_MANIFEST.colors.ink,
            "muted_color": CURRENT_BRAND_MANIFEST.colors.muted,
            "border_color": CURRENT_BRAND_MANIFEST.colors.border,
            "subtle_color": CURRENT_BRAND_MANIFEST.colors.subtle,
            "font_family": CURRENT_BRAND_MANIFEST.document.font_family,
            "body_size_pt": CURRENT_BRAND_MANIFEST.document.body_size_pt,
            "h1_size_pt": CURRENT_BRAND_MANIFEST.document.h1_size_pt,
            "h2_size_pt": CURRENT_BRAND_MANIFEST.document.h2_size_pt,
            "h3_size_pt": CURRENT_BRAND_MANIFEST.document.h3_size_pt,
            "caption_size_pt": CURRENT_BRAND_MANIFEST.document.caption_size_pt,
            "line_spacing_percent": CURRENT_BRAND_MANIFEST.document.line_spacing_percent,
            "paragraph_space_above_pt": (CURRENT_BRAND_MANIFEST.document.paragraph_space_above_pt),
            "paragraph_space_below_pt": (CURRENT_BRAND_MANIFEST.document.paragraph_space_below_pt),
            "horizontal_margin_pt": CURRENT_BRAND_MANIFEST.document.horizontal_margin_pt,
            "vertical_margin_pt": CURRENT_BRAND_MANIFEST.document.vertical_margin_pt,
        }
        if any(getattr(self, name) != value for name, value in expected.items()):
            msg = "Sarj brand tokens must match the pinned manifest"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class MaterializedMarkdown:
    text: str
    document: DocumentIR


def build_copy_mutations(
    original: DocumentIR,
    transformed: DocumentIR,
    *,
    profile: SarjBrandProfile,
    headers: tuple[TabHeader, ...],
    logo_uri: str,
) -> list[DocsRequest]:
    requests = build_copy_text_mutations(original, transformed)
    located = _post_edit_locations(original, transformed)
    requests.extend(
        _brand_shell_requests(
            transformed.title,
            profile=profile,
            tab_ids=_tab_ids(transformed),
            headers=headers,
            logo_uri=logo_uri,
        )
    )
    requests.extend(_style_requests(located, profile=profile, branded=True))
    return requests


def build_copy_text_mutations(
    original: DocumentIR,
    transformed: DocumentIR,
) -> list[DocsRequest]:
    transformed_by_id = {block.id: block for block in transformed.blocks}
    requests: list[DocsRequest] = []
    changed = [
        block
        for block in original.blocks
        if block.source_range is not None
        and transformed_by_id.get(block.id) is not None
        and transformed_by_id[block.id].text != block.text
    ]
    for block in sorted(
        changed,
        key=lambda item: (
            item.source_range.tab_id if item.source_range is not None else "",
            item.source_range.start_index if item.source_range is not None else -1,
        ),
        reverse=True,
    ):
        source_range = block.source_range
        if source_range is None:  # pragma: no cover - filtered above
            continue
        replacement = transformed_by_id[block.id].text
        location: dict[str, Any] = {
            "index": source_range.start_index,
            "tabId": source_range.tab_id,
        }
        range_value: dict[str, Any] = {
            "startIndex": source_range.start_index,
            "endIndex": source_range.end_index,
            "tabId": source_range.tab_id,
        }
        if source_range.segment_id is not None:
            location["segmentId"] = source_range.segment_id
            range_value["segmentId"] = source_range.segment_id
        requests.extend(
            [
                {"deleteContentRange": {"range": range_value}},
                {"insertText": {"location": location, "text": replacement}},
            ]
        )
    return requests


def build_markdown_mutations(
    document: DocumentIR,
    *,
    tab_id: DocsTabId,
    profile: SarjBrandProfile,
    headers: tuple[TabHeader, ...],
    logo_uri: str,
    branded: bool = True,
) -> list[DocsRequest]:
    materialized = _materialize_markdown(document, tab_id=tab_id)
    requests: list[DocsRequest] = [
        {
            "insertText": {
                "location": {"index": 1, "tabId": tab_id},
                "text": materialized.text,
            }
        }
    ]
    if branded:
        requests.extend(
            _brand_shell_requests(
                document.title,
                profile=profile,
                tab_ids=(tab_id,),
                headers=headers,
                logo_uri=logo_uri,
            )
        )
    requests.extend(_style_requests(materialized.document, profile=profile, branded=branded))
    return requests


def mutation_sha256(requests: list[DocsRequest]) -> str:
    payload = json.dumps(requests, sort_keys=True, separators=(",", ":"))
    if (
        len(requests) > MAX_DOCS_MUTATION_REQUESTS
        or len(payload.encode()) > MAX_DOCS_MUTATION_BYTES
    ):
        raise SourceTooLargeError
    return hashlib.sha256(payload.encode()).hexdigest()


def _materialize_markdown(
    document: DocumentIR,
    *,
    tab_id: DocsTabId,
) -> MaterializedMarkdown:
    chunks: list[str] = []
    blocks: list[TextBlock] = []
    index = 1
    for block in document.blocks:
        prefix = ""
        if block.kind == BlockKind.QUOTE:
            prefix = "› "
        text = f"{prefix}{block.text}\n"
        chunks.append(text)
        length = utf16_length(text)
        blocks.append(
            TextBlock(
                id=block.id,
                kind=block.kind,
                text=block.text,
                level=block.level,
                source_range=SourceRange(index, index + length, tab_id),
                editable=block.editable,
            )
        )
        index += length
    return MaterializedMarkdown(
        "".join(chunks),
        DocumentIR(document.title, tuple(blocks), document.source_format),
    )


def _style_requests(
    document: DocumentIR,
    *,
    profile: SarjBrandProfile,
    branded: bool,
) -> list[DocsRequest]:
    requests: list[DocsRequest] = []
    for block in document.blocks:
        source_range = block.source_range
        if source_range is None or block.kind == BlockKind.BLANK or not block.editable:
            continue
        range_value = {
            "startIndex": source_range.start_index,
            "endIndex": source_range.end_index,
            "tabId": source_range.tab_id,
        }
        size = _font_size(block, profile)
        if branded:
            text_style: dict[str, Any] = {
                "weightedFontFamily": {"fontFamily": profile.font_family},
                "fontSize": {"magnitude": size, "unit": "PT"},
                "foregroundColor": {
                    "color": {
                        "rgbColor": _rgb_color(
                            profile.primary_color
                            if block.kind == BlockKind.HEADING
                            else profile.ink_color
                        )
                    }
                },
            }
            fields = "weightedFontFamily,fontSize,foregroundColor"
            if block.kind == BlockKind.HEADING:
                text_style["bold"] = True
                fields += ",bold"
            requests.append(
                {
                    "updateTextStyle": {
                        "range": range_value,
                        "textStyle": text_style,
                        "fields": fields,
                    }
                }
            )
        if block.kind in {BlockKind.LIST_ITEM, BlockKind.CHECKLIST}:
            requests.append(
                {
                    "createParagraphBullets": {
                        "range": range_value,
                        "bulletPreset": (
                            "BULLET_CHECKBOX"
                            if block.kind == BlockKind.CHECKLIST
                            else "BULLET_DISC_CIRCLE_SQUARE"
                        ),
                    }
                }
            )
        requests.append(
            {
                "updateParagraphStyle": {
                    "range": range_value,
                    "paragraphStyle": {
                        "alignment": "START",
                        "lineSpacing": profile.line_spacing_percent if branded else 100,
                        "spaceAbove": {
                            "magnitude": profile.paragraph_space_above_pt if branded else 0,
                            "unit": "PT",
                        },
                        "spaceBelow": {
                            "magnitude": profile.paragraph_space_below_pt if branded else 0,
                            "unit": "PT",
                        },
                        "keepWithNext": block.kind == BlockKind.HEADING,
                        "namedStyleType": _named_style(block),
                    },
                    "fields": (
                        "alignment,lineSpacing,spaceAbove,spaceBelow,keepWithNext,namedStyleType"
                    ),
                }
            }
        )
    return requests


def _rgb_color(value: str) -> dict[str, float]:
    normalized = value.removeprefix("#")
    return {
        "red": int(normalized[0:2], 16) / 255,
        "green": int(normalized[2:4], 16) / 255,
        "blue": int(normalized[4:6], 16) / 255,
    }


def _font_size(block: TextBlock, profile: SarjBrandProfile) -> int:
    if block.kind != BlockKind.HEADING:
        return profile.body_size_pt
    heading_sizes = {
        1: profile.h1_size_pt,
        2: profile.h2_size_pt,
        3: profile.h3_size_pt,
    }
    return (
        heading_sizes.get(block.level, profile.body_size_pt)
        if block.level
        else profile.body_size_pt
    )


def _named_style(block: TextBlock) -> str:
    if block.kind == BlockKind.HEADING and block.level in {1, 2, 3, 4, 5, 6}:
        return f"HEADING_{block.level}"
    return "NORMAL_TEXT"


def _brand_shell_requests(
    title: str,
    *,
    profile: SarjBrandProfile,
    tab_ids: tuple[DocsTabId, ...],
    headers: tuple[TabHeader, ...],
    logo_uri: str,
) -> list[DocsRequest]:
    margin = {"magnitude": profile.horizontal_margin_pt, "unit": "PT"}
    vertical_margin = {"magnitude": profile.vertical_margin_pt, "unit": "PT"}
    requests: list[DocsRequest] = [
        {
            "updateDocumentStyle": {
                "documentStyle": {
                    "marginLeft": margin,
                    "marginRight": margin,
                    "marginTop": vertical_margin,
                    "marginBottom": vertical_margin,
                },
                "fields": "marginLeft,marginRight,marginTop,marginBottom",
                "tabId": tab_id,
            }
        }
        for tab_id in tab_ids
    ]
    for header in headers:
        location = {
            "segmentId": header.header_id,
            "tabId": header.tab_id,
        }
        requests.append(
            {
                "insertText": {
                    "endOfSegmentLocation": location,
                    "text": f"{title}\t",
                }
            }
        )
        if logo_uri:
            requests.append(
                {
                    "insertInlineImage": {
                        "endOfSegmentLocation": location,
                        "uri": logo_uri,
                        "objectSize": {
                            "width": {"magnitude": 52, "unit": "PT"},
                            "height": {"magnitude": 28, "unit": "PT"},
                        },
                    }
                }
            )
    return requests


def _tab_ids(document: DocumentIR) -> tuple[DocsTabId, ...]:
    located = tuple(
        dict.fromkeys(
            block.source_range.tab_id for block in document.blocks if block.source_range is not None
        )
    )
    return located or document.tab_ids


def _post_edit_locations(original: DocumentIR, transformed: DocumentIR) -> DocumentIR:
    transformed_by_id = {block.id: block for block in transformed.blocks}
    deltas: dict[str, int] = {}
    located: list[TextBlock] = []
    for block in sorted(
        original.blocks,
        key=lambda item: (
            item.source_range.tab_id if item.source_range is not None else "",
            item.source_range.start_index if item.source_range is not None else -1,
        ),
    ):
        target = transformed_by_id[block.id]
        source_range = block.source_range
        if source_range is None:
            located.append(target)
            continue
        delta = deltas.get(source_range.tab_id, 0)
        start = source_range.start_index + delta
        old_length = source_range.end_index - source_range.start_index
        new_length = utf16_length(target.text)
        located.append(
            TextBlock(
                id=target.id,
                kind=target.kind,
                text=target.text,
                level=target.level,
                source_range=SourceRange(
                    start,
                    start + new_length,
                    source_range.tab_id,
                    source_range.segment_id,
                ),
                editable=target.editable,
            )
        )
        deltas[source_range.tab_id] = delta + new_length - old_length
    return DocumentIR(
        transformed.title,
        tuple(located),
        transformed.source_format,
        transformed.tab_ids,
    )
