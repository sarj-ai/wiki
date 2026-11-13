import pytest

from app.document_transform.ir import parse_markdown
from app.document_transform.safety import rewrite_constraints, validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase


_FACTS = (
    "The operator is responsible for quota checks before each deployment. "
    "The operator may retry only if the lease expired. "
    "The queue is intended to retain snapshots for 30 days."
)


@pytest.mark.parametrize(
    "recap",
    [
        "This paragraph describes the operational requirements stated above.",
        (
            "In order to make the situation clear, this paragraph describes the operational "
            "requirements that have already been stated above."
        ),
        "This paragraph summarizes the information discussed earlier.",
        "This paragraph repeats the points that were already described below.",
    ],
)
def test_editorial_recap_does_not_force_a_source_position_qualifier_to_survive(
    recap: str,
) -> None:
    source = f"{_FACTS} {recap}"
    document = parse_markdown(source, title="Operations")
    block = document.blocks[0]
    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=_FACTS)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert decision.accepted == {block.id: _FACTS}
    assert not decision.findings
    assert rewrite_constraints(source) == rewrite_constraints(_FACTS)


@pytest.mark.parametrize(
    "substantive",
    [
        "The operator must follow the operational requirements stated above.",
        "This paragraph describes the operational requirements stated above for deployment.",
        "The owner said this paragraph describes the operational requirements stated above.",
        "This paragraph describes the 3 operational requirements stated above.",
        'The owner said, "This paragraph describes the operational requirements stated above."',
        "This paragraph describes the operational requirements stated above and below.",
    ],
)
def test_substantive_reported_and_quoted_references_keep_their_source_position(
    substantive: str,
) -> None:
    assert "above" in rewrite_constraints(substantive).scope_qualifiers


def test_recaps_never_remove_a_qualifier_from_the_adjacent_requirement() -> None:
    source = (
        "This paragraph describes the operational requirements stated above. "
        "The operator must follow the limits stated above."
    )
    assert rewrite_constraints(source).scope_qualifiers == ("above",)


def test_a_reviewed_recap_can_be_removed_on_continuation_with_original_source_proof() -> None:
    original = parse_markdown(
        _FACTS + " This paragraph describes the operational requirements stated above.",
        title="Operations",
    )
    block = original.blocks[0]
    current = original.replace_blocks(
        {block.id: _FACTS + " These operational requirements were stated above."}
    )
    patch = TextPatch(
        block_id=block.id, expected_sha256=current.blocks[0].sha256, replacement=_FACTS
    )
    decision = validate_patches(
        current,
        [patch],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
        framing_source=original,
    )
    assert decision.accepted == {block.id: _FACTS}
    assert not decision.findings

    unproven = validate_patches(
        current, [patch], phase=TransformPhase.UNSLOP, aggressive_unslop=True
    )
    assert not unproven.accepted
    assert [finding.code for finding in unproven.findings] == ["scope_qualifier_changed"]


@pytest.mark.parametrize(
    "source",
    [
        "This paragraph describes the operational requirements stated above for deployment.",
        'The owner said, "This paragraph describes the operational requirements stated above."',
        "The owner said this paragraph describes the operational requirements stated above.",
    ],
)
def test_reported_or_qualified_text_cannot_authorize_a_continued_recap_exception(
    source: str,
) -> None:
    assert rewrite_constraints(
        "These operational requirements were stated above.", framing_source=source
    ).scope_qualifiers == ("above",)


def test_an_original_cross_reference_cannot_be_laundered_by_a_neighboring_recap() -> None:
    source = (
        "This paragraph describes the operational requirements stated above. "
        "These operational requirements were stated above."
    )
    assert rewrite_constraints(source, framing_source=source).scope_qualifiers == ("above",)


def test_original_recap_proof_never_authorizes_multiple_or_nonterminal_references() -> None:
    source = "This paragraph describes the operational requirements stated above."
    repeated = (
        "These operational requirements were stated above. "
        "These operational requirements were stated above."
    )
    assert rewrite_constraints(repeated, framing_source=source).scope_qualifiers == (
        "above",
        "above",
    )
    assert rewrite_constraints(
        "These operational requirements were stated above. The team retained the log.",
        framing_source=source,
    ).scope_qualifiers == ("above",)


def test_a_reviewed_recap_preface_can_be_removed_with_its_exact_original_proof() -> None:
    original = parse_markdown(
        _FACTS + " This paragraph describes the operational requirements stated above.",
        title="Operations",
    )
    block = original.blocks[0]
    current_text = _FACTS.replace("The queue is intended", "As stated above, the queue is intended")
    current = original.replace_blocks({block.id: current_text})
    patch = TextPatch(
        block_id=block.id, expected_sha256=current.blocks[0].sha256, replacement=_FACTS
    )
    decision = validate_patches(
        current,
        [patch],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
        framing_source=original,
    )
    assert decision.accepted == {block.id: _FACTS}
    assert not decision.findings
    assert not rewrite_constraints(current_text, framing_source=block.text).scope_qualifiers
    assert rewrite_constraints(current_text).scope_qualifiers == ("above",)


def test_original_or_duplicate_recap_prefaces_keep_their_source_positions() -> None:
    original = (
        "This paragraph describes the operational requirements stated above. "
        "As stated above, the operator must follow the limits."
    )
    assert rewrite_constraints(original, framing_source=original).scope_qualifiers == ("above",)
    repeated = (
        "As stated above, the queue retains logs. As stated above, the operator checks quotas."
    )
    proof = "This paragraph describes the operational requirements stated above."
    assert rewrite_constraints(repeated, framing_source=proof).scope_qualifiers == (
        "above",
        "above",
    )
