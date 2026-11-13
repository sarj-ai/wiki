from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from app.document_transform import native_quote_spans
from app.document_transform.ir import parse_google_document, parse_markdown, utf16_length
from app.document_transform.native_chip_flow import plan_native_chips
from app.document_transform.native_quote_spans import native_inline_protected_ranges
from app.document_transform.quotations import quoted_block_ids
from app.document_transform.safety import rewrite_constraints, validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase
from app.document_transform.table_layout import numeric_table_value
from app.document_transform.unslop import model_editable_blocks, scan_unslop_candidates
from tests.test_multiblock_quotations import _native_document
from tests.test_native_chip_protection import _document as _chip_document


_ANNOTATIONS = (
    ('12 inches (12")', '"'),
    ('1 inch (1")', '"'),
    ('1/2 inch (1/2")', '"'),
    ('1 1/2 inches (1 1/2")', '"'),
    ('1.50 inches (1.50")', '"'),
    ('12 INCHES (12")', '"'),
    ("3 feet (3')", "'"),
    ("1 foot (1')", "'"),
)
_OUTSIDE = "The result was measured by the team."
_ACTIVE = "The team measured the result."


@pytest.mark.parametrize(("annotation", "delimiter"), _ANNOTATIONS)
@pytest.mark.parametrize("native", [False, True])
def test_unit_annotation_cannot_end_a_real_multiblock_quotation(
    annotation: str, delimiter: str, *, native: bool
) -> None:
    texts = (
        delimiter + f"The board is {annotation}.",
        "The load may change later." + delimiter,
        _OUTSIDE,
    )
    document = (
        parse_google_document(_native_document(texts), title="Exact engineering quotation")
        if native
        else parse_markdown("\n\n".join(texts), title="Exact engineering quotation")
    )
    quoted = next(block for block in document.blocks if "load may" in block.text)
    last_quoted = document.blocks.index(quoted)
    assert quoted_block_ids(document) == frozenset(
        block.id for block in document.blocks[: last_quoted + 1]
    )
    assert tuple(block.text for block in model_editable_blocks(document)) == (_OUTSIDE,)
    outside = document.blocks[-1]
    rejected = validate_patches(
        document,
        [
            TextPatch(
                block_id=quoted.id,
                expected_sha256=quoted.sha256,
                replacement=quoted.text.replace("change", "vary"),
            )
        ],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )
    assert not rejected.accepted
    assert [finding.code for finding in rejected.findings] == ["quoted_content_changed"]
    accepted = validate_patches(
        document,
        [TextPatch(block_id=outside.id, expected_sha256=outside.sha256, replacement=_ACTIVE)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )
    assert accepted.accepted == {outside.id: _ACTIVE}
    assert not accepted.findings


@pytest.mark.parametrize(("annotation", "delimiter"), _ANNOTATIONS)
def test_unquoted_annotation_keeps_unrelated_paragraphs_editable(
    annotation: str, delimiter: str
) -> None:
    quotation = delimiter + "Leave the error here." + delimiter
    source = f"The board is {annotation}.\n\n{_OUTSIDE}\n\nThe reviewer wrote {quotation}"
    document = parse_markdown(source, title="Measurements and quotation")

    assert not quoted_block_ids(document)
    assert _OUTSIDE in tuple(block.text for block in model_editable_blocks(document))
    assert rewrite_constraints(source).quotations == (quotation,)


@pytest.mark.parametrize("annotation", ['12 inches (12")', "3 feet (3')"])
def test_annotations_do_not_mask_later_filler_or_change_real_quoted_bytes(annotation: str) -> None:
    quotation = '"The workers is ready."'
    source = f"The board is {annotation}. The result was measured by the team; {quotation}"
    replacement = f"The board is {annotation}. The team measured the result; {quotation}"
    document = parse_markdown(source, title="Exact quotation")
    block = document.blocks[0]

    assert rewrite_constraints(source).quotations == (quotation,)
    candidates = scan_unslop_candidates(document)
    assert len(candidates) == 1
    assert candidates[0].code == "passive_voice"
    assert candidates[0].start == source.index("was measured")
    accepted = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )
    assert accepted.accepted == {block.id: replacement}
    assert not accepted.findings


@pytest.mark.parametrize("annotation", ['12 inches (12")', "3 feet (3')"])
def test_native_quote_offsets_after_annotations_preserve_utf16_boundaries(annotation: str) -> None:
    quotation = '"Leave 𐐀 exact."'
    text = f"🧪 Reading: {annotation}; {quotation} Tail."
    ranges = native_inline_protected_ranges(text)

    assert len(ranges) == 1
    assert ranges[0].reason == "protected_quotation"
    assert ranges[0].start == utf16_length(text[: text.index(quotation)])
    assert ranges[0].end == utf16_length(text[: text.index(quotation) + len(quotation)])


@pytest.mark.parametrize("annotation", ['12 inches (12")', "3 feet (3')"])
def test_single_paragraph_quote_retains_original_annotation_bytes(annotation: str) -> None:
    quotation = f'"The panel is {annotation} and may change later."'
    source = f"The reviewer retained {quotation} in the report."
    document = parse_markdown(source, title="Exact engineering quotation")
    block = document.blocks[0]
    assert quotation in rewrite_constraints(source).quotations
    rejected = validate_patches(
        document,
        [
            TextPatch(
                block_id=block.id,
                expected_sha256=block.sha256,
                replacement=source.replace("change", "vary"),
            )
        ],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )
    assert not rejected.accepted
    assert [finding.code for finding in rejected.findings] == ["quoted_content_changed"]


@pytest.mark.parametrize("later_quotation", [False, True])
@pytest.mark.parametrize("native", [False, True])
def test_malformed_quoted_annotation_is_verbatim_without_protecting_unrelated_tail(
    *, later_quotation: bool, native: bool
) -> None:
    quoted_prefix = '"The board is 12 inches (12"'
    first = f"The reviewer retained {quoted_prefix}) in the report."
    texts = (
        (first, 'The reviewer wrote "Leave the error here."', _OUTSIDE)
        if later_quotation
        else (first, _OUTSIDE)
    )
    source = "\n\n".join(texts)
    document = (
        parse_google_document(_native_document(texts), title="Authored quotation error")
        if native
        else parse_markdown(source, title="Authored quotation error")
    )
    quoted, outside = document.blocks[0], document.blocks[-1]
    assert quoted_prefix in rewrite_constraints(first).quotations
    rejected = validate_patches(
        document,
        [
            TextPatch(
                block_id=quoted.id,
                expected_sha256=quoted.sha256,
                replacement=first.replace("The board is", "The board:"),
            )
        ],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )
    assert not rejected.accepted
    assert [finding.code for finding in rejected.findings] == ["quoted_content_changed"]
    accepted = validate_patches(
        document,
        [TextPatch(block_id=outside.id, expected_sha256=outside.sha256, replacement=_ACTIVE)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )
    assert accepted.accepted == {outside.id: _ACTIVE}
    assert outside in model_editable_blocks(document)
    ranges = native_inline_protected_ranges(source)
    if not later_quotation:
        assert all(span.end <= utf16_length(source[: source.index(_OUTSIDE)]) for span in ranges)
    if later_quotation:
        later = '"Leave the error here."'
        start = utf16_length(source[: source.index(later)])
        assert any(
            span.start <= start and span.end >= start + utf16_length(later) for span in ranges
        )


def test_genuine_later_unfinished_quote_keeps_its_native_date_literal() -> None:
    source = (
        '"The board is 12 inches (12"). The load may change later." '
        'The reviewer wrote "Keep 2026-11-13 verbatim'
    )
    start = utf16_length(source[: source.index('"Keep')])
    assert any(
        span.start == start
        and span.end == utf16_length(source)
        and span.reason == "unclosed_quotation"
        for span in native_inline_protected_ranges(source)
    )
    plan = plan_native_chips(_chip_document((source,)), operation_id=uuid.UUID(int=7))
    assert plan is not None
    assert not plan.spans
    assert len(plan.skipped) == 1
    assert plan.skipped[0].reason in {"protected_quotation", "unclosed_quotation"}


def test_closed_mixed_quote_with_unit_keeps_only_the_outside_date_eligible() -> None:
    source = '“The reviewer retained "12 inches (12") on 2026-11-13.” Visit 2026-11-14.'
    plan = plan_native_chips(_chip_document((source,)), operation_id=uuid.UUID(int=8))

    assert plan is not None
    assert [span.original_text for span in plan.spans] == ["2026-11-14"]
    assert len(plan.skipped) == 1
    assert plan.skipped[0].reason == "protected_quotation"


@pytest.mark.parametrize("annotation", ['12 inches (12")', "12 inches (12″)"])
def test_supported_size_unmatched_quote_scan_cannot_rescan_every_suffix(annotation: str) -> None:
    expected_module = str(Path(native_quote_spans.__file__).resolve())
    script = (
        "from pathlib import Path\n"
        "from app.document_transform import native_quote_spans\n"
        "from app.document_transform.ir import utf16_length\n"
        f"assert str(Path(native_quote_spans.__file__).resolve()) == {expected_module!r}\n"
        f"source = '“' * 400_000 + {annotation!r} + ' Keep 2026-11-13 verbatim.'\n"
        "assert utf16_length(source) < 500_000\n"
        "ranges = native_quote_spans.native_inline_protected_ranges(source)\n"
        "assert any(span.start == 0 and span.end == utf16_length(source) "
        "and span.reason == 'unclosed_quotation' for span in ranges)\n"
        "print('protected')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.stdout.strip() == "protected"


def test_annotation_inside_code_retains_code_and_real_quote_ranges() -> None:
    code = '`inches (12")`'
    quotation = '"Leave exact."'
    source = f"🧪 {code}; {quotation} Tail."
    ranges = native_inline_protected_ranges(source)
    assert [(span.reason, span.start, span.end) for span in ranges] == [
        (
            "protected_code",
            utf16_length(source[: source.index(code)]),
            utf16_length(source[: source.index(code) + len(code)]),
        ),
        (
            "protected_quotation",
            utf16_length(source[: source.index(quotation)]),
            utf16_length(source[: source.index(quotation) + len(quotation)]),
        ),
    ]


@pytest.mark.parametrize(("literal", "altered"), [('12"', "12'"), ("3'", '3"'), ("12″", "12′")])
def test_measurement_marks_remain_exact_protected_quantity_units(
    literal: str, altered: str
) -> None:
    source = f"The reading was measured by the team at {literal}."
    document = parse_markdown(source, title="Exact unit glyph")
    block = document.blocks[0]
    rejected = validate_patches(
        document,
        [
            TextPatch(
                block_id=block.id,
                expected_sha256=block.sha256,
                replacement=f"The team measured the reading at {altered}.",
            )
        ],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert not rejected.accepted
    assert [finding.code for finding in rejected.findings] == ["protected_content_changed"]
    assert literal in rewrite_constraints(source).protected_tokens
    assert numeric_table_value(literal)


def test_a_quote_after_a_digit_without_explicit_unit_context_still_closes_quoted_prose() -> None:
    quotation = '"The board was measured by the team at 12"'
    source = quotation + ". " + _OUTSIDE
    document = parse_markdown(_OUTSIDE, title="Ambiguous quote")
    document = replace(document, blocks=(replace(document.blocks[0], text=source),))
    block = document.blocks[0]
    assert rewrite_constraints(source).quotations == (quotation,)
    rejected = validate_patches(
        document,
        [
            TextPatch(
                block_id=block.id,
                expected_sha256=block.sha256,
                replacement=source.replace("was measured by the team", "the team measured", 1),
            )
        ],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )
    assert not rejected.accepted
    assert [finding.code for finding in rejected.findings] == ["quoted_content_changed"]
