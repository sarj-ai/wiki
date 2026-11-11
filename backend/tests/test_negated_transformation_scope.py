import pytest

from app.document_transform.ir import parse_markdown
from app.document_transform.safety import validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase


@pytest.mark.parametrize(
    ("replacement", "accepted"),
    [
        ("The figures are rules of thumb, not modern benchmarks.", False),
        ("Modern presentation does not make the figures modern benchmarks.", True),
        ("Modern presentation cannot turn the figures into modern benchmarks.", False),
        ("Modern presentation does not convert figures into modern benchmarks.", True),
    ],
)
def test_negated_transformation_keeps_its_relationship_and_modal_force(
    replacement: str, *, accepted: bool
) -> None:
    source = "The figures are rules of thumb. A modern presentation does not turn the figures into modern benchmarks."
    if accepted:
        replacement = "The figures are rules of thumb. " + replacement
    document = parse_markdown(source, title="Transformation scope")
    block = document.blocks[0]

    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert bool(decision.accepted) is accepted
    if not accepted:
        assert decision.findings


def test_literal_quotations_still_prevent_a_changed_transformation() -> None:
    source = 'The guide says "Presentation does not turn estimates into benchmarks."'
    replacement = 'The guide says "Estimates are not benchmarks."'
    document = parse_markdown(source, title="Quoted scope")
    block = document.blocks[0]

    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert not decision.accepted
