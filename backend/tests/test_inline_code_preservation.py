import pytest

from app.document_transform.ir import parse_markdown
from app.document_transform.planner import TextTransformPlanner
from app.document_transform.safety import rewrite_constraints, validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase
from app.document_transform.unslop import scan_unslop_candidates
from tests.test_editorial_refinement import _RefiningEditor, _request


_LITERALS = (
    "``echo `alpha` omega``",
    "```echo ``alpha`` omega```",
    "``echo `alpha` and ```omega``` tail``",
    "``echo `alpha` and 'omega'``",
    "``echo `alpha` and 🧪``",
    "`alpha`",
)


@pytest.mark.parametrize("literal", _LITERALS)
def test_complete_inline_code_literal_is_one_protected_token(literal: str) -> None:
    assert rewrite_constraints(f"The example uses {literal}.").protected_tokens == (literal,)


@pytest.mark.parametrize("literal", _LITERALS)
def test_rewrites_cannot_mutate_a_shorter_backtick_run_inside_code(literal: str) -> None:
    source = f"The example uses {literal} in order to show the literal."
    original = parse_markdown(source, title="Code literal")
    block = original.blocks[0]
    replacement = f"The example uses {literal.replace('alpha', 'beta')} to show the literal."

    decision = validate_patches(
        original,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["protected_content_changed"]


@pytest.mark.parametrize("literal", _LITERALS)
def test_surrounding_prose_can_shorten_while_code_remains_exact(literal: str) -> None:
    source = f"The example uses {literal} in order to show the literal."
    original = parse_markdown(source, title="Code literal")
    block = original.blocks[0]
    replacement = f"The example uses {literal} to show the literal."

    decision = validate_patches(
        original,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert decision.accepted == {block.id: replacement}
    assert not decision.findings


def test_code_words_do_not_become_editorial_candidates() -> None:
    source = "The example uses ``echo `in order to` omega`` in the command."

    assert not scan_unslop_candidates(parse_markdown(source, title="Code literal"))


def test_repeated_complete_code_literals_cannot_be_dropped() -> None:
    literal = "``echo `alpha` omega``"
    assert rewrite_constraints(f"Use {literal}, then retain {literal}.").protected_tokens == (
        literal,
        literal,
    )


def test_prose_between_two_complete_code_literals_remains_editable() -> None:
    source = "Use ``echo `alpha` omega`` in order to show ``echo `beta` omega``."
    original = parse_markdown(source, title="Independent literals")
    block = original.blocks[0]
    replacement = source.replace("in order to", "to")

    decision = validate_patches(
        original,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert decision.accepted == {block.id: replacement}
    assert not decision.findings


def test_a_link_like_prefix_cannot_hide_the_end_of_an_overlapping_code_literal() -> None:
    source = (
        "The example contains [read ``alpha beta](https://example.test/) gamma`` "
        "in order to show a literal."
    )
    original = parse_markdown(source, title="Overlapping literals")
    block = original.blocks[0]
    replacement = source.replace("gamma", "delta").replace("in order to", "to")

    decision = validate_patches(
        original,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["protected_content_changed"]


async def test_invalid_code_never_reaches_a_permissive_factual_reviewer() -> None:
    source = "The example uses ``echo `alpha` omega`` in order to show the literal."
    replacement = "The example uses ``echo `beta` omega`` to show the literal."
    editor = _RefiningEditor((replacement,))
    original = parse_markdown(source, title="Code literal")

    outcome = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        original, _request(source)
    )

    assert outcome.document == original
    assert not editor.sources
    assert any(finding.code == "protected_content_changed" for finding in outcome.findings)
