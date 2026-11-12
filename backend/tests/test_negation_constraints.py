import pytest

from app.document_transform.ir import parse_markdown
from app.document_transform.safety import PatchDecision, validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase


def _decision(source: str, target: str, *, aggressive: bool = True) -> PatchDecision:
    document = parse_markdown(source, title="QA recovery")
    block = document.blocks[0]
    return validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=target)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=aggressive,
    )


@pytest.mark.parametrize("apostrophe", ["'", "’", "‘", "ʼ", "＇"])
@pytest.mark.parametrize("aggressive", [False, True])
@pytest.mark.parametrize(
    ("negative", "positive"),
    [("can't", "can"), ("won't", "will"), ("shouldn't", "should"), ("mustn't", "must")],
)
def test_modal_negation_cannot_become_permission_or_obligation(
    apostrophe: str, aggressive: bool, negative: str, positive: str
) -> None:
    source = f"The worker {negative.replace(chr(39), apostrophe)} discard the checkpoint."
    target = f"The worker {positive} discard the checkpoint."

    decision = _decision(source, target, aggressive=aggressive)

    assert not decision.accepted
    assert "force_language_changed" in [finding.code for finding in decision.findings]


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (
            "The worker doesn't retry a rejected revision.",
            "The worker does retry a rejected revision.",
        ),
        (
            "The worker didn’t retry a rejected revision.",
            "The worker did retry a rejected revision.",
        ),
        ("The workers don’t retry rejected revisions.", "The workers do retry rejected revisions."),
        ("The worker isn't ready.", "The worker is ready."),
        ("The workers aren’t ready.", "The workers are ready."),
        ("The worker wasn’t ready.", "The worker was ready."),
        ("The workers weren’t ready.", "The workers were ready."),
        ("The worker hasn’t saved the result.", "The worker has saved the result."),
        ("The workers haven’t saved the result.", "The workers have saved the result."),
        ("The worker hadn’t saved the result.", "The worker had saved the result."),
        (
            "The worker oughtn’t to discard the checkpoint.",
            "The worker ought to discard the checkpoint.",
        ),
    ],
)
def test_negative_auxiliary_contracts_remain_negative(source: str, target: str) -> None:
    decision = _decision(source, target)

    assert not decision.accepted
    assert "force_language_changed" in [finding.code for finding in decision.findings]


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("The worker can’t retry in order to recover.", "The worker cannot retry to recover."),
        ("The worker cannot retry in order to recover.", "The worker can not retry to recover."),
        ("The worker won’t retry in order to recover.", "The worker will not retry to recover."),
        ("The worker doesn’t retry in order to recover.", "The worker does not retry to recover."),
        (
            "The worker shouldn’t retry in order to recover.",
            "The worker should not retry to recover.",
        ),
        (
            "The worker oughtn’t to retry in order to recover.",
            "The worker ought not to retry to recover.",
        ),
    ],
)
def test_equivalent_expanded_negation_can_shorten_other_prose(source: str, target: str) -> None:
    decision = _decision(source, target)

    assert decision.accepted == {"b0": target}
    assert not decision.findings


def test_normalizing_contractions_cannot_change_an_exact_quotation() -> None:
    decision = _decision("The fixture says “Don’t retry.”", "The fixture says “Do not retry.”")

    assert not decision.accepted
    assert "quoted_content_changed" in [finding.code for finding in decision.findings]


def test_negative_clause_multiplicity_survives_normalization() -> None:
    decision = _decision(
        "The worker can’t discard the checkpoint and doesn’t retry a rejected revision.",
        "The worker cannot discard the checkpoint and retries a rejected revision.",
    )

    assert not decision.accepted
    assert "force_language_changed" in [finding.code for finding in decision.findings]


@pytest.mark.parametrize("apostrophe", ["'", "’", "‘", "ʼ", "＇"])
@pytest.mark.parametrize(
    ("contraction", "expanded", "infinitive"),
    [
        ("doesn't", "do not", ""),
        ("won't", "will not", ""),
        ("wouldn't", "would not", ""),
        ("shan't", "shall not", ""),
        ("needn't", "need not", ""),
        ("oughtn't", "ought not", "to "),
        ("oughtn't", "ought not", "to automatically "),
    ],
)
def test_normalizing_negation_cannot_drop_its_transformation_relationship(
    apostrophe: str, contraction: str, expanded: str, infinitive: str
) -> None:
    negative = contraction.replace("'", apostrophe)
    source = (
        "The figures are rules of thumb. A modern presentation "
        f"{negative} {infinitive}turn the figures into modern benchmarks."
    )
    target = f"The figures are rules of thumb and {expanded} {infinitive}be modern benchmarks."
    decision = _decision(source, target)

    assert not decision.accepted
    assert "negative_transformation_scope_changed" in [
        finding.code for finding in decision.findings
    ]


@pytest.mark.parametrize("apostrophe", ["'", "’", "‘", "ʼ", "＇"])
@pytest.mark.parametrize(
    ("contraction", "infinitive"),
    [("doesn't", ""), ("won't", ""), ("oughtn't", "to "), ("oughtn't", "to automatically ")],
)
def test_normalizing_negation_retains_equivalent_transformation_relationship(
    apostrophe: str, contraction: str, infinitive: str
) -> None:
    negative = contraction.replace("'", apostrophe)
    source = (
        "The figures are rules of thumb. A modern presentation "
        f"{negative} {infinitive}turn the figures into modern benchmarks."
    )
    target = (
        "The figures are rules of thumb. "
        f"Modern presentation {negative} {infinitive}make the figures modern benchmarks."
    )

    assert _decision(source, target).accepted == {"b0": target}


def test_expanded_negation_with_an_adverb_keeps_its_transformation_relationship() -> None:
    source = (
        "The figures are rules of thumb. "
        "A modern presentation ought not to automatically turn the figures into modern benchmarks."
    )
    target = "The figures are rules of thumb and ought not to be modern benchmarks."

    decision = _decision(source, target)

    assert not decision.accepted
    assert "negative_transformation_scope_changed" in [
        finding.code for finding in decision.findings
    ]
