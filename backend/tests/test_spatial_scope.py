import pytest

from app.document_transform.ir import parse_markdown
from app.document_transform.safety import validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase


@pytest.mark.parametrize("qualifier", ["above", "below"])
@pytest.mark.parametrize("invert", [False, True])
@pytest.mark.parametrize("comparison", [False, True])
def test_removing_or_reversing_spatial_scope_is_rejected(
    *, qualifier: str, invert: bool, comparison: bool
) -> None:
    source = (
        f"The queue latency remains {qualifier} 5 ms."
        if comparison
        else f"Composite QA reference. Operations and measurements {qualifier} are fictional."
    )
    replacement = source.replace(
        qualifier, ("below" if qualifier == "above" else "above") if invert else ""
    ).replace("  ", " ")
    document = parse_markdown(source, title="Scoped facts")
    block = document.blocks[0]

    decision = validate_patches(
        document,
        [
            TextPatch(
                block_id=block.id,
                expected_sha256=block.sha256,
                replacement=replacement,
            )
        ],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert not decision.accepted
    assert decision.findings[0].code == "scope_qualifier_changed"


@pytest.mark.parametrize("qualifier", ["above", "below"])
@pytest.mark.parametrize("joiner", ["-", "\u2011"])
@pytest.mark.parametrize("drop_modifier", [False, True])
def test_spatial_words_do_not_hide_a_changed_compound(
    *, qualifier: str, joiner: str, drop_modifier: bool
) -> None:
    compound = f"{qualifier}{joiner}threshold"
    source = f"The readings remain {compound}."
    replacement = source.replace(
        compound, qualifier if drop_modifier else f"{qualifier}{joiner}average"
    )
    document = parse_markdown(source, title="Scoped facts")
    block = document.blocks[0]

    decision = validate_patches(
        document,
        [
            TextPatch(
                block_id=block.id,
                expected_sha256=block.sha256,
                replacement=replacement,
            )
        ],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert not decision.accepted
    assert decision.findings[0].code == "scope_qualifier_changed"


@pytest.mark.parametrize("qualifier", ["above", "below"])
def test_spatial_scope_can_survive_a_shorter_active_voice_rewrite(qualifier: str) -> None:
    source = f"The limits {qualifier} were approved by the team."
    document = parse_markdown(source, title="Scoped facts")
    block = document.blocks[0]

    decision = validate_patches(
        document,
        [
            TextPatch(
                block_id=block.id,
                expected_sha256=block.sha256,
                replacement=f"The team approved the limits {qualifier}.",
            )
        ],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert len(decision.accepted) == 1
