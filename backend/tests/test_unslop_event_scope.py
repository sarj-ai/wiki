import pytest

from tests.test_editorial_framing_constraints import _decision


@pytest.mark.parametrize(
    "scope",
    ["before publication", "after approval", "during the pilot", "until release", "since launch"],
)
def test_fronted_event_cannot_date_an_unrelated_responsibility(scope: str) -> None:
    source = (
        f"The release team reviews the release document {scope}. "
        "The release team has the responsibility for recording results in the review log."
    )
    proposed = (
        f"{scope.capitalize()}, the release team reviews the release document and is "
        "responsible for recording results in the review log."
    )

    decision = _decision(source, proposed)

    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["temporal_scope_changed"]


@pytest.mark.parametrize(
    "scope",
    ["before publication", "after approval", "during the pilot", "until release", "since launch"],
)
def test_fronted_event_can_stay_local_when_nominal_responsibility_is_shortened(scope: str) -> None:
    source = (
        f"The release document is reviewed by the release team {scope}. "
        "The release team has the responsibility for recording results in the review log."
    )
    proposed = (
        f"{scope.capitalize()}, the release team reviews the release document. "
        "The release team is responsible for recording results in the review log."
    )

    assert list(_decision(source, proposed).accepted.values()) == [proposed]


def test_already_shared_event_can_remain_shared() -> None:
    source = (
        "Before publication, the release document is reviewed by the release team, "
        "which has the responsibility for recording results in the review log."
    )
    proposed = (
        "Before publication, the release team reviews the release document and is "
        "responsible for recording results in the review log."
    )

    assert list(_decision(source, proposed).accepted.values()) == [proposed]
