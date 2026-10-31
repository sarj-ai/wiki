import pytest

from app.document_transform.ir import parse_markdown
from app.document_transform.safety import rewrite_constraints, validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (
            "Males generally stopped singing at night after acquiring a mate.",
            "Males stopped singing after pairing.",
        ),
        (
            "Researchers interpreted the pattern as evidence that night song helps attract females.",
            "Night song attracts mates.",
        ),
        (
            "Show the ordinary-looking body as clearly as the singing pose.",
            "Show the ordinary-looking body alongside the singing pose.",
        ),
        (
            "Researchers compared twelve methods estimating the same national population.",
            "Researchers compared twelve methods estimating a population.",
        ),
        ("Researchers measured about 12 birds in this study.", "Researchers measured 12 birds."),
        (
            "The guide marks the usual period of presence.",
            "The guide marks the period of presence.",
        ),
        (
            "The guide gives rounded figures for reference.",
            "The guide gives figures for reference.",
        ),
        (
            "Combining limits does not produce a confidence interval.",
            "Combining limits does not produce an interval.",
        ),
        ("The methods estimate a national population.", "The methods estimate a population."),
    ],
)
def test_aggressive_rewrite_rejects_lost_frequency_inference_and_comparison(
    before: str, after: str
):
    document = parse_markdown(before, title="Source")
    block = document.blocks[0]
    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=after)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )
    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["scope_qualifier_changed"]
    assert document.blocks[0].text == before


@pytest.mark.parametrize("qualifier", ["usually", "typically", "often", "rarely", "sometimes"])
def test_frequency_qualifiers_are_supplied_to_the_writer(qualifier: str):
    constraints = rewrite_constraints(f"Researchers report that birds {qualifier} return.")
    assert constraints.scope_qualifiers == (qualifier,)


def test_qualified_facts_can_be_shortened_and_reordered():
    before = (
        "The researchers interpreted the pattern as evidence that night song helps attract "
        "females. Males generally stopped singing at night after pairing."
    )
    after = (
        "Males generally stopped singing at night after pairing; researchers interpreted this "
        "as evidence that night song helps attract females."
    )
    document = parse_markdown(before, title="Source")
    block = document.blocks[0]
    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=after)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )
    assert decision.accepted == {block.id: after}
    assert not decision.findings


def test_topic_about_is_not_an_approximation_constraint():
    assert rewrite_constraints("An article about birds.").scope_qualifiers == ()
