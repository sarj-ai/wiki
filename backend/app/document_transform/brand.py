from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from typing import Any

from app.document_transform.brand_manifest import (
    CURRENT_BRAND_MANIFEST,
    HEADER_TITLE_SEPARATOR,
    DocumentHeaderBrandTokens,
    normalize_header_title,
)
from app.document_transform.custom_furniture import CustomHeaderTitleTarget
from app.document_transform.errors import SourceTooLargeError, UnsupportedSourceError
from app.document_transform.gateway import TabHeader
from app.document_transform.ir import BlockKind, DocumentIR, SourceRange, TextBlock, utf16_length
from app.identifiers import DocsTabId


SARJ_PURPLE = CURRENT_BRAND_MANIFEST.colors.primary


def _current_header_tokens() -> DocumentHeaderBrandTokens:
    tokens = CURRENT_BRAND_MANIFEST.document.header
    if tokens is None:  # pragma: no cover - immutable manifest invariant
        msg = "Current Sarj brand profile must define document header tokens"
        raise RuntimeError(msg)
    return tokens


CURRENT_HEADER_TOKENS = _current_header_tokens()
MAX_DOCS_MUTATION_REQUESTS = 2_500
MAX_DOCS_MUTATION_BYTES = 5 * 1024 * 1024
_MIN_NATIVE_STYLE_SPLIT_CODEPOINTS = 2
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
    header_font_family: str = CURRENT_HEADER_TOKENS.font_family
    header_size_pt: int = CURRENT_HEADER_TOKENS.size_pt
    header_color: str = CURRENT_HEADER_TOKENS.color
    header_bold: bool = CURRENT_HEADER_TOKENS.bold
    header_alignment: str = CURRENT_HEADER_TOKENS.alignment
    header_line_spacing_percent: int = CURRENT_HEADER_TOKENS.line_spacing_percent
    header_space_above_pt: int = CURRENT_HEADER_TOKENS.space_above_pt
    header_space_below_pt: int = CURRENT_HEADER_TOKENS.space_below_pt
    header_logo_width_pt: int = CURRENT_HEADER_TOKENS.logo_width_pt
    header_logo_height_pt: int = CURRENT_HEADER_TOKENS.logo_height_pt
    header_max_title_chars: int = CURRENT_HEADER_TOKENS.max_title_chars

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
            "header_font_family": CURRENT_HEADER_TOKENS.font_family,
            "header_size_pt": CURRENT_HEADER_TOKENS.size_pt,
            "header_color": CURRENT_HEADER_TOKENS.color,
            "header_bold": CURRENT_HEADER_TOKENS.bold,
            "header_alignment": CURRENT_HEADER_TOKENS.alignment,
            "header_line_spacing_percent": CURRENT_HEADER_TOKENS.line_spacing_percent,
            "header_space_above_pt": CURRENT_HEADER_TOKENS.space_above_pt,
            "header_space_below_pt": CURRENT_HEADER_TOKENS.space_below_pt,
            "header_logo_width_pt": CURRENT_HEADER_TOKENS.logo_width_pt,
            "header_logo_height_pt": CURRENT_HEADER_TOKENS.logo_height_pt,
            "header_max_title_chars": CURRENT_HEADER_TOKENS.max_title_chars,
        }
        if any(getattr(self, name) != value for name, value in expected.items()):
            msg = "Sarj brand tokens must match the pinned manifest"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class MaterializedMarkdown:
    text: str
    document: DocumentIR


@dataclass(frozen=True, slots=True)
class ParagraphSpacing:
    above: int
    below: int


def build_copy_mutations(
    original: DocumentIR,
    transformed: DocumentIR,
    *,
    profile: SarjBrandProfile,
    headers: tuple[TabHeader, ...],
    logo_uri: str,
    header_title: str | None = None,
) -> list[DocsRequest]:
    requests = build_copy_text_mutations(original, transformed)
    located = _post_edit_locations(original, transformed)
    requests.extend(
        _brand_shell_requests(
            transformed.title if header_title is None else header_title,
            profile=profile,
            tab_ids=_tab_ids(transformed),
            headers=headers,
            logo_uri=logo_uri,
        )
    )
    # Check provider widths before post-edit locations reconstruct them from text.
    split_native_ids = frozenset(
        block.id
        for block in original.blocks
        if block.source_range is not None
        and block.source_range.end_index - block.source_range.start_index
        == utf16_length(block.text)
    )
    requests.extend(
        _style_requests(located, profile=profile, branded=True, split_native_ids=split_native_ids)
    )
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


def build_custom_header_title_requests(
    title: str,
    *,
    profile: SarjBrandProfile,
    headers: tuple[CustomHeaderTitleTarget, ...],
) -> list[DocsRequest]:
    header_text = (
        normalize_header_title(title, max_chars=profile.header_max_title_chars)
        + HEADER_TITLE_SEPARATOR
    )
    end_index = utf16_length(header_text)
    requests: list[DocsRequest] = []
    for header in headers:
        location = {
            "index": 0,
            "segmentId": header.header_id,
            "tabId": header.tab_id,
        }
        requests.extend(
            [
                {"insertText": {"location": location, "text": header_text}},
                {
                    "updateTextStyle": {
                        "range": {
                            "startIndex": 0,
                            "endIndex": end_index,
                            "segmentId": header.header_id,
                            "tabId": header.tab_id,
                        },
                        "textStyle": {
                            "weightedFontFamily": {"fontFamily": profile.header_font_family},
                            "fontSize": {"magnitude": profile.header_size_pt, "unit": "PT"},
                            "foregroundColor": {
                                "color": {"rgbColor": _rgb_color(profile.header_color)}
                            },
                            "bold": profile.header_bold,
                        },
                        "fields": "weightedFontFamily,fontSize,foregroundColor,bold",
                    }
                },
            ]
        )
        if (
            header.terminal_newline_index is not None
            and header.terminal_newline_text_style_json is not None
        ):
            newline_index = end_index + header.terminal_newline_index
            requests.append(
                {
                    "updateTextStyle": {
                        "range": {
                            "startIndex": newline_index,
                            "endIndex": newline_index + 1,
                            "segmentId": header.header_id,
                            "tabId": header.tab_id,
                        },
                        "textStyle": json.loads(header.terminal_newline_text_style_json),
                        "fields": "*",
                    }
                }
            )
    return requests


def preserve_in_place_native_list_text_styles(
    requests: list[DocsRequest], *, original: DocumentIR, transformed: DocumentIR
) -> list[DocsRequest]:
    located = _post_edit_locations(original, transformed)
    native_list_ranges = tuple(
        block.source_range
        for block in located.blocks
        if block.native_bullet_json is not None and block.source_range is not None
    )
    if not native_list_ranges:
        return requests

    def intersects_native_list(request: DocsRequest) -> bool:
        update = request.get("updateTextStyle")
        if not isinstance(update, dict):
            return False
        target = update.get("range")
        if not isinstance(target, dict) or target.get("segmentId") is not None:
            return False
        start, end, tab_id = (
            target.get("startIndex"),
            target.get("endIndex"),
            target.get("tabId"),
        )
        if not isinstance(start, int) or not isinstance(end, int) or not isinstance(tab_id, str):
            return False
        return any(
            source.segment_id is None
            and source.tab_id == tab_id
            and start < source.end_index + 1
            and end > source.start_index
            for source in native_list_ranges
        )

    return [request for request in requests if not intersects_native_list(request)]


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
                native_named_style=block.native_named_style,
                native_bullet_json=block.native_bullet_json,
                authored_emphasis=block.authored_emphasis,
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
    split_native_ids: frozenset[str] = frozenset(),
) -> list[DocsRequest]:
    requests: list[DocsRequest] = []
    for block in document.blocks:
        source_range = block.source_range
        if (
            source_range is None
            or block.kind == BlockKind.BLANK
            or block.preserved_visual_authority
        ):
            continue
        if not block.editable:
            if branded:
                requests.extend(_font_only_requests(block, profile))
            continue
        range_value = {
            "startIndex": source_range.start_index,
            "endIndex": source_range.end_index,
            "tabId": source_range.tab_id,
        }
        if (
            block.kind in {BlockKind.LIST_ITEM, BlockKind.CHECKLIST}
            and block.native_bullet_json is None
        ):
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
        preserve_native_style = (
            block.kind in {BlockKind.LIST_ITEM, BlockKind.CHECKLIST}
            and block.native_bullet_json is not None
            and block.source_named_style == _named_style(block)
            and block.source_style_text_sha256 == block.sha256
        )
        paragraph_request = {
            "updateParagraphStyle": {
                "range": range_value,
                "paragraphStyle": {
                    "alignment": "START",
                    "lineSpacing": profile.line_spacing_percent if branded else 100,
                    "spaceAbove": {
                        "magnitude": paragraph_spacing(block, profile).above if branded else 0,
                        "unit": "PT",
                    },
                    "spaceBelow": {
                        "magnitude": paragraph_spacing(block, profile).below if branded else 0,
                        "unit": "PT",
                    },
                    "keepWithNext": paragraph_keep_with_next(block),
                    **(
                        {"keepLinesTogether": True}
                        if block.pagination_keep_with_next is not None
                        else {}
                    ),
                    **({} if preserve_native_style else {"namedStyleType": _named_style(block)}),
                },
                "fields": (
                    "alignment,lineSpacing,spaceAbove,spaceBelow,keepWithNext"
                    + (",keepLinesTogether" if block.pagination_keep_with_next is not None else "")
                    + ("" if preserve_native_style else ",namedStyleType")
                ),
            }
        }
        if block.preserved_paragraph_style_sha256 is None:
            requests.append(paragraph_request)
        # Named styles can affect inherited text appearance. Apply the explicit
        # brand overrides after selecting the paragraph's named style.
        if branded:
            text_style: dict[str, Any] = {
                "weightedFontFamily": {"fontFamily": profile.font_family},
                "fontSize": {"magnitude": _font_size(block, profile), "unit": "PT"},
                "foregroundColor": {
                    "color": {"rgbColor": _rgb_color(paragraph_color(block, profile))}
                },
            }
            fields = "weightedFontFamily,fontSize,foregroundColor"
            if block.kind == BlockKind.HEADING:
                text_style["bold"] = block.native_named_style != "SUBTITLE"
                fields += ",bold"
            elif not preserve_native_style:
                # Explicitly retain authored body emphasis across named-style
                # selection. Never synthesize emphasis for ordinary prose.
                text_style.update(block.authored_emphasis)
                fields += "".join(f",{key}" for key, _value in block.authored_emphasis)
            ranges = [range_value]
            if preserve_native_style and block.id in split_native_ids:
                ranges = _native_text_style_ranges(block, range_value)
            requests.extend(
                {
                    "updateTextStyle": {
                        "range": text_range,
                        "textStyle": text_style,
                        "fields": fields,
                    }
                }
                for text_range in ranges
            )
    return requests


def _native_text_style_ranges(block: TextBlock, range_value: DocsRequest) -> list[DocsRequest]:
    # Full-paragraph text styling may also update its bullet. Two proper
    # subranges are a bounded avoidance hypothesis, not a verifier relaxation.
    # Split at a Unicode codepoint boundary, never inside a UTF-16 surrogate pair.
    if (
        len(block.text) < _MIN_NATIVE_STYLE_SPLIT_CODEPOINTS
        or "\n" in block.text
        or "\r" in block.text
        or range_value["endIndex"] - range_value["startIndex"] != utf16_length(block.text)
    ):
        return [range_value]
    midpoint = range_value["startIndex"] + utf16_length(block.text[: len(block.text) // 2])
    return [
        {**range_value, "endIndex": midpoint},
        {**range_value, "startIndex": midpoint},
    ]


def _font_only_requests(block: TextBlock, profile: SarjBrandProfile) -> list[DocsRequest]:
    if block.preserved_font_family:
        return []
    # Preserve links, emphasis and paragraph semantics while styling safe prose runs.
    return [
        {
            "updateTextStyle": {
                "range": {
                    "startIndex": source.start_index,
                    "endIndex": source.end_index,
                    "tabId": source.tab_id,
                    **({"segmentId": source.segment_id} if source.segment_id else {}),
                },
                "textStyle": {"weightedFontFamily": {"fontFamily": profile.font_family}},
                "fields": "weightedFontFamily.fontFamily",
            }
        }
        for source in block.font_style_ranges
    ]


def _rgb_color(value: str) -> dict[str, float]:
    normalized = value.removeprefix("#")
    return {
        "red": int(normalized[0:2], 16) / 255,
        "green": int(normalized[2:4], 16) / 255,
        "blue": int(normalized[4:6], 16) / 255,
    }


def _font_size(block: TextBlock, profile: SarjBrandProfile) -> int:
    if block.native_named_style == "TITLE":
        return profile.h1_size_pt + profile.paragraph_space_below_pt
    if block.native_named_style == "SUBTITLE":
        return profile.h3_size_pt
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
    if block.native_named_style in {"TITLE", "SUBTITLE"}:
        return block.native_named_style
    if block.kind == BlockKind.HEADING and block.level in {1, 2, 3, 4, 5, 6}:
        return f"HEADING_{block.level}"
    return "NORMAL_TEXT"


def paragraph_spacing(block: TextBlock, profile: SarjBrandProfile) -> ParagraphSpacing:
    spacing = _role_paragraph_spacing(block, profile)
    return ParagraphSpacing(
        0 if block.suppress_space_above else spacing.above,
        0 if block.suppress_space_below else spacing.below,
    )


def _role_paragraph_spacing(block: TextBlock, profile: SarjBrandProfile) -> ParagraphSpacing:
    # Derive role rhythm from pinned tokens; never infer new headings from prose.
    above = profile.paragraph_space_above_pt
    below = profile.paragraph_space_below_pt
    if block.native_named_style == "TITLE":
        return ParagraphSpacing(0, below * 2)
    if block.native_named_style == "SUBTITLE":
        return ParagraphSpacing(0, below * 2)
    if block.kind == BlockKind.HEADING:
        return ParagraphSpacing(above + below * (2 if block.level in {1, 2} else 1), below)
    if block.kind in {BlockKind.LIST_ITEM, BlockKind.CHECKLIST}:
        return ParagraphSpacing(0, above)
    if block.is_authored_lead_in:
        return ParagraphSpacing(above, above)
    return ParagraphSpacing(above, below)


def paragraph_named_style(block: TextBlock) -> str:
    return _named_style(block)


def paragraph_keep_with_next(block: TextBlock) -> bool:
    return (
        block.kind is BlockKind.HEADING
        if block.pagination_keep_with_next is None
        else block.pagination_keep_with_next
    )


def paragraph_font_size(block: TextBlock, profile: SarjBrandProfile) -> int:
    return _font_size(block, profile)


def paragraph_color(block: TextBlock, profile: SarjBrandProfile) -> str:
    if block.native_named_style == "SUBTITLE":
        return profile.muted_color
    return (
        profile.primary_color
        if block.kind == BlockKind.HEADING or block.is_authored_lead_in
        else profile.ink_color
    )


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
        header_text = (
            normalize_header_title(title, max_chars=profile.header_max_title_chars)
            + HEADER_TITLE_SEPARATOR
        )
        requests.append(
            {
                "insertText": {
                    "endOfSegmentLocation": location,
                    "text": header_text,
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
                            "width": {
                                "magnitude": profile.header_logo_width_pt,
                                "unit": "PT",
                            },
                            "height": {
                                "magnitude": profile.header_logo_height_pt,
                                "unit": "PT",
                            },
                        },
                    }
                }
            )
        header_text_end = utf16_length(header_text)
        text_range = {
            "startIndex": 0,
            "endIndex": header_text_end,
            **location,
        }
        paragraph_range = {
            **text_range,
            "endIndex": header_text_end + (1 if logo_uri else 0),
        }
        requests.extend(
            [
                {
                    "updateParagraphStyle": {
                        "range": paragraph_range,
                        "paragraphStyle": {
                            "alignment": profile.header_alignment,
                            "lineSpacing": profile.header_line_spacing_percent,
                            "spaceAbove": {
                                "magnitude": profile.header_space_above_pt,
                                "unit": "PT",
                            },
                            "spaceBelow": {
                                "magnitude": profile.header_space_below_pt,
                                "unit": "PT",
                            },
                            "keepWithNext": False,
                            "namedStyleType": "NORMAL_TEXT",
                        },
                        "fields": (
                            "alignment,lineSpacing,spaceAbove,spaceBelow,"
                            "keepWithNext,namedStyleType"
                        ),
                    }
                },
                {
                    "updateTextStyle": {
                        "range": text_range,
                        "textStyle": {
                            "weightedFontFamily": {"fontFamily": profile.header_font_family},
                            "fontSize": {
                                "magnitude": profile.header_size_pt,
                                "unit": "PT",
                            },
                            "foregroundColor": {
                                "color": {"rgbColor": _rgb_color(profile.header_color)}
                            },
                            "bold": profile.header_bold,
                        },
                        "fields": "weightedFontFamily,fontSize,foregroundColor,bold",
                    }
                },
            ]
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
        if block.native_content and (
            target.native_content != block.native_content or target.text != block.text
        ):
            raise UnsupportedSourceError
        semantic_length = utf16_length(block.text)
        if target.text == block.text and not block.editable and old_length != semantic_length:
            # Unchanged blocks are never deleted/reinserted, so their provider
            # width remains authoritative. A protected paragraph can mix text
            # with an inline object that is intentionally absent from semantic
            # text; recomputing that width from ``target.text`` would shift all
            # later style requests into the preceding character.
            new_length = old_length
        elif not block.editable:
            if target.text != block.text:
                raise UnsupportedSourceError
            new_length = semantic_length
        else:
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
                native_content=target.native_content,
                native_named_style=target.native_named_style,
                source_named_style=target.source_named_style,
                source_style_text_sha256=target.source_style_text_sha256,
                native_bullet_json=target.native_bullet_json,
                authored_emphasis=target.authored_emphasis,
                pagination_keep_with_next=target.pagination_keep_with_next,
                suppress_space_above=target.suppress_space_above,
                suppress_space_below=target.suppress_space_below,
                preserved_paragraph_style_sha256=target.preserved_paragraph_style_sha256,
                preserved_font_family=target.preserved_font_family,
                preserved_visual_authority=target.preserved_visual_authority,
                font_style_ranges=tuple(
                    replace(
                        item,
                        start_index=item.start_index + delta,
                        end_index=item.end_index + delta,
                    )
                    for item in target.font_style_ranges
                ),
            )
        )
        deltas[source_range.tab_id] = delta + new_length - old_length
    return DocumentIR(
        transformed.title,
        tuple(located),
        transformed.source_format,
        transformed.tab_ids,
    )
