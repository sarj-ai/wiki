from dataclasses import replace
from typing import Any

from app.document_transform.errors import InPlaceNeedsReviewError, UnsupportedSourceError
from app.document_transform.ir import DocumentIR, parse_google_document
from app.document_transform.native_font_proof import (
    NativeFontVerificationPolicy,
    NativeVisualAuthoritySelection,
    capture_native_font_proof,
    verify_native_font_proof,
)
from app.document_transform.native_visual_authority import visual_authority_block_ids


def verify_copy_visual_authority(
    source: dict[str, Any] | None, actual: dict[str, Any], expected: DocumentIR
) -> bool:
    if source is None:
        return True
    try:
        return _verify(source, actual, expected)
    except (
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        AttributeError,
        InPlaceNeedsReviewError,
        UnsupportedSourceError,
    ):
        return False


def _verify(source: dict[str, Any], actual: dict[str, Any], expected: DocumentIR) -> bool:
    original = parse_google_document(source, title=expected.title)
    selected = visual_authority_block_ids(source, original)
    if not selected:
        return True
    # Only selected authored paragraphs belong to this proof. Keep every
    # block for UTF-16 displacement accounting after preceding text edits.
    original = replace(
        original, blocks=tuple(replace(block, editable=True) for block in original.blocks)
    )
    transformed = replace(
        expected, blocks=tuple(replace(block, editable=True) for block in expected.blocks)
    )
    proofs = capture_native_font_proof(
        source,
        original,
        transformed=transformed,
        target_font_family="Nunito",
        preserved_family_block_ids=selected,
        visual_authority=NativeVisualAuthoritySelection(selected),
    )
    return verify_native_font_proof(
        actual,
        proofs,
        font_policy=NativeFontVerificationPolicy(
            preserved_family_block_ids=selected, require_target_family=True
        ),
    )
