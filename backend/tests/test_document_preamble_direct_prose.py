from collections import Counter

import pytest

from app.document_transform.ir import parse_markdown
from app.document_transform.safety import rewrite_constraints, validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase


@pytest.mark.parametrize("action", ["brings together", "is intended to bring together"])
def test_document_announcement_can_be_removed_when_its_attributed_facts_survive(
    action: str,
) -> None:
    source = f"This note {action} latency estimates from the Google SRE reference. The figures are rules of thumb rather than new measurements of current hardware."
    replacement = "Google SRE reference latency estimates are rules of thumb rather than new measurements of current hardware."
    document = parse_markdown(source, title="Direct prose")
    block = document.blocks[0]

    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert decision.accepted == {block.id: replacement}
    assert not decision.findings


@pytest.mark.parametrize(
    "source",
    [
        "The team brings together latency estimates from the Google SRE reference.",
        "This note may bring together latency estimates from the Google SRE reference.",
        'The team quotes "This note brings together latency estimates".',
        "The team preserves `This note brings together latency estimates`.",
    ],
)
def test_substantive_actions_uncertainty_quotes_and_code_keep_together(source: str) -> None:
    assert "together" in rewrite_constraints(source).scope_qualifiers


def test_document_announcement_exception_keeps_another_actors_grouping() -> None:
    source = (
        "This note brings together latency estimates. The team brings together field measurements."
    )

    assert Counter(rewrite_constraints(source).scope_qualifiers)["together"] == 1
