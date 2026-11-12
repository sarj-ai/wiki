import pytest

from app.document_transform.ir import parse_markdown
from app.document_transform.safety import rewrite_constraints, validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase


@pytest.mark.parametrize("apostrophe", ["'", "’"])
@pytest.mark.parametrize("auxiliary", ["do", "does", "did"])
def test_negative_contractions_preserve_force_and_allow_real_concision(
    auxiliary: str, apostrophe: str
) -> None:
    source = f"Modern presentation {auxiliary} not turn estimates into modern benchmarks."
    replacement = source.replace(f"{auxiliary} not", f"{auxiliary}n{apostrophe}t")
    document = parse_markdown(source, title="Negation")
    block = document.blocks[0]

    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert rewrite_constraints(source).force_language == ("not",)
    assert rewrite_constraints(replacement).force_language == ("not",)
    assert decision.accepted == {block.id: replacement}


@pytest.mark.parametrize("auxiliary", ["do", "does", "did"])
def test_removing_contracted_negation_still_changes_force(auxiliary: str) -> None:
    source = f"The team {auxiliary}n't use these estimates for current hardware."

    assert (
        rewrite_constraints(source).force_language
        != rewrite_constraints(source.replace(f"{auxiliary}n't", auxiliary)).force_language
    )


@pytest.mark.parametrize("marker", ['"', "`"])
def test_quoted_and_code_negations_keep_their_original_spelling(marker: str) -> None:
    source = f"Keep {marker}does not turn estimates into benchmarks{marker} exactly as written."
    replacement = source.replace("does not", "doesn't")
    document = parse_markdown(source, title="Literal negation")
    block = document.blocks[0]

    assert not validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    ).accepted
