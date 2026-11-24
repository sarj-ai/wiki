from __future__ import annotations

from dataclasses import replace

import pytest

from app.document_transform.ir import parse_markdown
from app.document_transform.safety import validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase
from app.document_transform.unslop import (
    deterministic_unslop_replacement,
    model_editable_blocks,
    scan_unslop_candidates,
)


def test_authored_title_survives_both_fixed_deletions_and_model_patches() -> None:
    document = parse_markdown(
        "\n# Furthermore, we approved the plan.\n\n# Furthermore, we approved the date.",
        title="File name",
    )
    title, section = document.blocks[1], document.blocks[3]
    assert model_editable_blocks(document) == (section,)
    assert {candidate.block_id for candidate in scan_unslop_candidates(document)} == {section.id}
    patches = [
        TextPatch(
            block_id=block.id,
            expected_sha256=block.sha256,
            replacement=block.text.removeprefix("Furthermore, ").capitalize(),
        )
        for block in (title, section)
    ]
    decision = validate_patches(
        document, patches, phase=TransformPhase.UNSLOP, aggressive_unslop=True
    )
    assert decision.accepted == {section.id: "We approved the date."}
    assert [f.code for f in decision.findings] == ["protected_block"]


@pytest.mark.parametrize(
    ("source", "eligible"),
    [
        ("At the end of the day, ship.", True),
        ("Why should you care? Birds sing.", True),
        ("The bird sings at dawn.", True),
        ("Each of 19 males may sing.", True),
        ('"At the end of the day, ship."', False),
        ("`At the end of the day, ship.`", False),
    ],
)
def test_short_prose_is_reviewed_while_whole_quotations_and_code_are_protected(
    source: str, *, eligible: bool
) -> None:
    document = parse_markdown(source, title="Short prose")

    assert len(source.split()) < 8
    assert model_editable_blocks(document) == (document.blocks if eligible else ())


@pytest.mark.parametrize(
    "source",
    [
        "Moreoverwe approved the plan.",
        "Furthermorewe approved the plan.",
        "In conclusionwe approved the plan.",
    ],
)
def test_transition_is_not_scanned_inside_a_word(source: str) -> None:
    document = parse_markdown(source, title="Identifier")

    assert scan_unslop_candidates(document) == ()


@pytest.mark.parametrize(
    "source",
    [
        "Moreover, we approved the plan.",
        "Furthermore, we approved the plan.",
        "In conclusion, we approved the plan.",
    ],
)
def test_transition_still_deletes_at_word_boundary(source: str) -> None:
    document = parse_markdown(source, title="Transition")
    block = document.blocks[0]

    assert (
        deterministic_unslop_replacement(block.text, scan_unslop_candidates(document))
        == "We approved the plan."
    )


def test_stale_candidate_cannot_delete_newer_text() -> None:
    document = parse_markdown("Moreover, we approved the plan.", title="Revision")
    candidate = scan_unslop_candidates(document)[0]
    newer_text = "Furthermore, we approved the plan."

    assert deterministic_unslop_replacement(newer_text, (candidate,)) == newer_text


@pytest.mark.parametrize(("start", "end"), [(-1, 8), (0, 1000), (8, 0)])
def test_malformed_candidate_offsets_do_not_change_text(start: int, end: int) -> None:
    document = parse_markdown("Moreover, we approved the plan.", title="Offsets")
    block = document.blocks[0]
    candidate = replace(scan_unslop_candidates(document)[0], start=start, end=end)

    assert deterministic_unslop_replacement(block.text, (candidate,)) == block.text
