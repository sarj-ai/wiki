import pytest

from app.document_transform.safety import rewrite_constraints
from tests.test_editorial_framing_constraints import _decision


@pytest.mark.parametrize("assignment", ["has responsibility for", "has the responsibility for"])
def test_nominal_responsibility_does_not_establish_performed_action(assignment: str) -> None:
    decision = _decision(
        f"The release team {assignment} recording results in the review log.",
        "The release team records results in the review log.",
    )

    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["force_language_changed"]


def test_observed_action_cannot_gain_nominal_responsibility() -> None:
    decision = _decision(
        "The release team records results in the review log.",
        "The release team has responsibility for recording results in the review log.",
    )

    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["force_language_changed"]


@pytest.mark.parametrize("assignment", ["has responsibility for", "has the responsibility for"])
@pytest.mark.parametrize("reverse", [False, True])
def test_nominal_and_adjectival_duties_can_be_rephrased(assignment: str, *, reverse: bool) -> None:
    source = f"The release team {assignment} recording results in the review log."
    proposed = "The release team is responsible for recording results in the review log."
    if reverse:
        source, proposed = proposed, source
    source = "It is important to note that " + source[0].lower() + source[1:]

    assert list(_decision(source, proposed).accepted.values()) == [proposed]


@pytest.mark.parametrize("spacing", [" ", "  ", "\t"])
def test_nominal_responsibility_preserves_the_canonical_duty(spacing: str) -> None:
    source = f"The release team has the RESPONSIBILITY{spacing}FOR recording results."

    assert rewrite_constraints(source).force_language == ("responsible for",)


def test_shorter_negated_assignment_cannot_lose_its_negation() -> None:
    source = "The release team has no responsibility for recording results in the review log."
    proposed = "The release team is responsible for recording results in the review log."

    assert not _decision(source, proposed).accepted


def test_live_paragraph_can_shorten_its_assignment_without_expanding_the_date_scope() -> None:
    source = (
        "It is important to note that the release team reviews the release document before "
        "publication, and the release team also approves the release document before publication. "
        "The release team has the responsibility for recording the results in the review log. "
        "In order to make sure that the review log remains useful, the release team records the "
        "date of the review, the name of the reviewer, and the result of the review. "
        "The review log is retained for 30 days."
    )
    proposed = (
        "The release team reviews and approves the release document before publication. "
        "The team is responsible for recording results in the review log. To keep the review "
        "log useful, the team records the review date, reviewer name, and result. "
        "The review log is retained for 30 days."
    )

    assert list(_decision(source, proposed).accepted.values()) == [proposed]
