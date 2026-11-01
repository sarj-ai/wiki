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
        "I hope this helps. ",
        "I dwell on the shape of the introduction. ",
        "I enjoy the pleasure of a measured tone. ",
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


@pytest.mark.parametrize(
    ("before", "after", "code"),
    [
        (
            "This AI model measured 12 birds.",
            "This model measured 12 birds.",
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
    ],
)
def test_editorial_cleanup_retains_entities_disclosures_and_factual_qualifiers(
    before: str, after: str, code: str
):
    decision = _decision(before, after)
    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == [code]
