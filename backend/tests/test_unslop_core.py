from __future__ import annotations

from dataclasses import replace

import pytest

from app.document_transform.ir import parse_markdown
from app.document_transform.unslop import (
    deterministic_unslop_replacement,
    scan_unslop_candidates,
)


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
