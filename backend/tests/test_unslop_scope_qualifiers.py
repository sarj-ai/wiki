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
        (
            "Cutting, regrowth and browsing help shape the habitat available in spring.",
            "Cutting, regrowth and browsing help maintain suitable spring habitat.",
        ),
        (
            "Researchers group songs by sound and structure, with specific types within categories.",
            "Researchers group songs into categories and song types.",
        ),
        (
            "Earlier research found the longest prospecting movements after pairing failed.",
            "Birds prospected longest after pairing failed.",
        ),
        (
            "Some song types were heard more often from older birds than from first-year birds.",
            "Older birds often sang song types rare in first-year birds.",
        ),
        (
            "Playback can divert territorial birds from other duties.",
            "Playback can disrupt territorial duties.",
        ),
        ("The cover is useful for nesting birds.", "The cover is essential for nesting birds."),
        (
            "The light-level estimates identify uncertain non-breeding locations.",
            "The geolocator estimates identify uncertain wintering locations.",
        ),
        (
            "The sample average describes successive notes in the studied sequences.",
            "The average describes notes in the recorded sequences.",
        ),
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


@pytest.mark.parametrize("hyphen", ["-", "\u2011"])
def test_rhetorical_compounds_can_be_removed_while_technical_compounds_survive(hyphen: str):
    before = f"In an ever{hyphen}evolving world, light{hyphen}level estimates help locate birds."
    after = f"Light{hyphen}level estimates help locate birds."
    document = parse_markdown(before, title="Source")
    block = document.blocks[0]
    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=after)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )
    assert decision.accepted == {block.id: after}
    assert f"light{hyphen}level" in rewrite_constraints(before).scope_qualifiers
    assert f"ever{hyphen}evolving" not in rewrite_constraints(before).scope_qualifiers
