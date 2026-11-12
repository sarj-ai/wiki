from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any

from pydantic import JsonValue

from app.document_transform.brand import font_only_paragraph_alignment
from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.copy_identity import (
    align_verified_copy_list_metadata,
    normalize_copy_list_identity,
)
from app.document_transform.errors import UnsupportedSourceError
from app.document_transform.ir import DocumentIR, parse_google_document
from app.document_transform.native_font_defense import (
    apply_native_font_preservation,
    defend_native_font_requests,
    preserved_native_font_blocks,
)
from app.document_transform.native_font_noop import filter_redundant_native_fonts
from app.document_transform.native_font_proof import (
    NativeFontProof,
    NativeFontVerificationPolicy,
    NativeVisualAuthoritySelection,
    _position_deltas,
    capture_native_font_proof,
    verify_native_font_proof,
    verify_native_unstyled_text,
)
from app.document_transform.native_visual_authority import (
    _VisualElement,
    _VisualParagraph,
    apply_visual_authority,
)
from app.document_transform.readability_json import bodies, content, obj
from app.identifiers import DocsTabId


@dataclass(frozen=True, slots=True)
class _CopyFontLayout:
    document: dict[str, JsonValue]
    requests: list[dict[str, JsonValue]]


def copy_preserved_font_ids(
    document: dict[str, Any] | None, original: DocumentIR, transformed: DocumentIR
) -> tuple[str, ...]:
    preserved = preserved_native_font_blocks(
        document,
        apply_native_font_preservation(original, ()),
        apply_native_font_preservation(transformed, ()),
    )
    selected = set(preserved) | {
        block.id for block in transformed.blocks if block.preserved_visual_authority
    }
    return tuple(block.id for block in original.blocks if block.id in selected)


def defend_copy_fonts(
    requests: list[dict[str, Any]],
    *,
    document: dict[str, Any] | None,
    original: DocumentIR,
    transformed: DocumentIR,
    font_family: str,
) -> list[dict[str, Any]]:
    if document is not None:
        layout = _copy_font_layout(document, original, transformed)
        requests = [*requests, *layout.requests]
    original = apply_visual_authority(
        original,
        tuple(block.id for block in transformed.blocks if block.preserved_visual_authority),
    )
    original = apply_native_font_preservation(
        original, tuple(block.id for block in transformed.blocks if block.preserved_font_family)
    )
    return defend_native_font_requests(
        filter_redundant_native_fonts(
            requests,
            document=document,
            original=original,
            transformed=transformed,
            font_family=font_family,
        ),
        document=document,
        original=original,
        transformed=transformed,
    )


def verify_copy_fonts(
    source: dict[str, Any] | None,
    actual: dict[str, Any],
    expected: DocumentIR,
    *,
    list_identity_equivalent: bool,
) -> bool:
    if source is None:
        return True
    try:
        return _verify(source, actual, expected, list_identity_equivalent=list_identity_equivalent)
    except KeyError, IndexError, TypeError, ValueError, AttributeError, UnsupportedSourceError:
        return False


def _verify(
    source: dict[str, Any],
    actual: dict[str, Any],
    expected: DocumentIR,
    *,
    list_identity_equivalent: bool,
) -> bool:
    if list_identity_equivalent:
        normalized_source = normalize_copy_list_identity(source)
        normalized_actual = normalize_copy_list_identity(actual)
        if normalized_source is None or normalized_actual is None:
            return False
        source, actual = normalized_source, normalized_actual
        expected = align_verified_copy_list_metadata(
            expected, parse_google_document(source, title=expected.title)
        )
    proofs = copy_font_proofs(source, expected)
    preserved = tuple(block.id for block in expected.blocks if block.preserved_font_family)
    return verify_native_font_proof(
        actual,
        proofs,
        font_policy=NativeFontVerificationPolicy(
            preserved_family_block_ids=preserved, require_target_family=True
        ),
    ) and verify_native_unstyled_text(
        source, actual, parse_google_document(source, title=expected.title)
    )


def copy_font_proofs(source: dict[str, Any], expected: DocumentIR) -> tuple[NativeFontProof, ...]:
    original = apply_visual_authority(
        parse_google_document(source, title=expected.title),
        tuple(block.id for block in expected.blocks if block.preserved_visual_authority),
    )
    preserved = copy_preserved_font_ids(source, original, expected)
    if preserved != tuple(block.id for block in expected.blocks if block.preserved_font_family):
        raise ValueError
    # Other verifiers own semantic-role changes. Font evidence retains source
    # roles while accounting for the exact expected UTF-16 text displacement.
    transformed = replace(
        original,
        blocks=tuple(
            replace(before, text=after.text)
            for before, after in zip(original.blocks, expected.blocks, strict=True)
        ),
    )
    layout = _copy_font_layout(source, original, expected)
    return capture_native_font_proof(
        layout.document,
        original,
        transformed=transformed,
        target_font_family=CURRENT_BRAND_MANIFEST.document.font_family,
        preserved_family_block_ids=preserved,
        visual_authority=NativeVisualAuthoritySelection(
            tuple(block.id for block in expected.blocks if block.preserved_visual_authority)
        ),
    )


def _copy_font_layout(
    document: dict[str, JsonValue],
    original: DocumentIR,
    expected: DocumentIR,
) -> _CopyFontLayout:
    if not expected.copy_font_defended:
        return _CopyFontLayout(document, [])
    targets = {}
    for candidate in expected.blocks:
        source = candidate.source_range
        if source is not None and font_only_paragraph_alignment(candidate) is not None:
            targets[source.tab_id, source.start_index] = candidate
    if not targets:
        return _CopyFontLayout(document, [])
    projected = deepcopy(document)
    deltas = _position_deltas(original, expected)
    requests: list[dict[str, JsonValue]] = []
    for tab_id, start, element, paragraph in _font_paragraphs(projected):
        block = targets.get((tab_id, start))
        if block is None:
            continue
        alignment = font_only_paragraph_alignment(block)
        if paragraph.paragraph_style.alignment == alignment:
            continue
        source = block.source_range
        if source is None:
            raise ValueError
        raw = obj(element["paragraph"])
        style = (
            obj(raw["paragraphStyle"]) if "paragraph_style" in paragraph.model_fields_set else {}
        )
        style["alignment"] = alignment
        raw["paragraphStyle"] = style
        delta = deltas.get(block.id, 0)
        requests.append(
            {
                "updateParagraphStyle": {
                    "range": {
                        "tabId": tab_id,
                        "startIndex": start + delta,
                        "endIndex": source.end_index + delta,
                    },
                    "paragraphStyle": {"alignment": alignment},
                    "fields": "alignment",
                }
            }
        )
    return _CopyFontLayout(projected, requests)


def _font_paragraphs(
    document: dict[str, JsonValue],
) -> Iterator[tuple[DocsTabId, int, dict[str, JsonValue], _VisualParagraph]]:
    for tab_id, tab in bodies(document):
        for value in content(tab):
            record = _VisualElement.model_validate(value)
            if record.start_index is None or record.paragraph is None:
                continue
            yield tab_id, record.start_index, obj(value), record.paragraph
