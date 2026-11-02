import pytest

from app.document_transform.ir import parse_markdown
from app.document_transform.safety import PatchDecision, rewrite_constraints, validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase


def _decision(before: str, after: str) -> PatchDecision:
    document = parse_markdown(before, title="Source")
    block = document.blocks[0]
    return validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=after)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )


@pytest.mark.parametrize(
    "framing",
    [
        "As an AI language model, ",
        "This deliberately wordy AI draft opens with an expansive preamble. ",
        "An AI draft takes shape. ",
        "BEFORE — deliberately padded AI-draft demonstration. ",
        "Deliberately wordy AI-draft input: ",
        "Deliberately wordy AI-draft input: In the spirit of a long preamble, ",
        "I hope this helps. ",
        "I dwell on the shape of the introduction. ",
        "I enjoy the pleasure of a measured tone. ",
        "This feels lovely, at least to my taste. ",
        "I enjoy an introduction within an introduction. ",
        "I like the feeling of a rounded paragraph. ",
        "I anticipate another gentle transition. ",
        "I enjoy another small rhetorical pause. ",
        "I welcome another reflective sentence. ",
        "I add another small flourish. ",
        "I enjoy another moment of anticipation. ",
        "I enjoy another moment of appreciation. ",
        "I enjoy another moment of verbal ceremony. ",
        "The closing lines offer another opportunity for a farewell. ",
        "I enjoy a grander transition than I would otherwise choose. ",
        "I let the close feel grander than my personal taste might otherwise favour. ",
    ],
)
def test_editorial_framing_can_be_removed_without_losing_the_qualified_fact(framing: str):
    fact = "BTO measured about 12 birds and reports that cover helps shape the habitat."
    decision = _decision(framing + fact, fact)
    assert list(decision.accepted.values()) == [fact]
    assert not decision.findings
    constraints = rewrite_constraints(framing + fact)
    assert constraints.protected_names == ("BTO",)
    assert constraints.scope_qualifiers == ("measured", "about", "helps", "shape")
    assert constraints.force_language == ()


@pytest.mark.parametrize(
    ("before", "after", "code"),
    [
        (
            "This AI model measured 12 birds.",
            "This model measured 12 birds.",
            "proper_noun_changed",
        ),
        (
            "Deliberately wordy AI-draft input: Google measured 12 birds.",
            "The team measured 12 birds.",
            "proper_noun_changed",
        ),
        (
            "Deliberately wordy AI-draft input: IN measured 12 birds.",
            "The sensor measured 12 birds.",
            "proper_noun_changed",
        ),
        (
            "This deliberately wordy AI draft must retain the label.",
            "This draft must retain the label.",
            "proper_noun_changed",
        ),
        (
            "The required label is This deliberately wordy AI draft.",
            "The required label is This draft.",
            "proper_noun_changed",
        ),
        (
            "I dwell on the shape of the introduction. Browsing helps shape the habitat.",
            "Browsing helps the habitat.",
            "scope_qualifier_changed",
        ),
        (
            "I enjoy the pleasure of a measured tone. Researchers measured 12 birds.",
            "Researchers recorded 12 birds.",
            "scope_qualifier_changed",
        ),
        (
            "As an AI language model, BTO measured about 12 birds.",
            "BTO measured 12 birds.",
            "scope_qualifier_changed",
        ),
        (
            "As an AI language model, BTO measured 12 birds.",
            "BTO measured 13 birds.",
            "protected_content_changed",
        ),
        (
            "As an AI language model, playback can divert birds from other duties.",
            "Playback diverts birds from other duties.",
            "force_language_changed",
        ),
        (
            'The source quotes "As an AI language model". BTO measured 12 birds.',
            "BTO measured 12 birds.",
            "quoted_content_changed",
        ),
        (
            "BTO compared the same national population within the reported area.",
            "BTO compared a national population within the reported area.",
            "scope_qualifier_changed",
        ),
        (
            "The rounded paragraph box must retain its shape.",
            "The paragraph box must retain its shape.",
            "scope_qualifier_changed",
        ),
        (
            "The material undergoes another transition within the measured range.",
            "The material undergoes a transition within the measured range.",
            "scope_qualifier_changed",
        ),
        (
            "The operating mode would otherwise change the measured result.",
            "The operating mode changes the measured result.",
            "force_language_changed",
        ),
        (
            "The reported sample might otherwise include another population.",
            "The reported sample includes another population.",
            "force_language_changed",
        ),
        (
            'The required wording is "my personal taste might otherwise favour".',
            "The required wording is personal taste.",
            "force_language_changed",
        ),
    ],
)
def test_editorial_cleanup_retains_entities_disclosures_and_factual_qualifiers(
    before: str, after: str, code: str
):
    decision = _decision(before, after)
    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == [code]
