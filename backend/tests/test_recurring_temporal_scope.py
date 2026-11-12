import pytest

from tests.test_unslop_temporal_scope import _decision


@pytest.mark.parametrize("operator", ["before", "after", "during"])
@pytest.mark.parametrize("frequency", ["each", "every"])
def test_recurring_condition_cannot_gain_unrelated_source_actions(
    operator: str, frequency: str
) -> None:
    source = (
        f"The release checklist is reviewed by the team {operator} {frequency} deployment. "
        "The team is responsible for checking the quota. The team also checks the rollback plan. "
        "These checks are intended to reduce avoidable operational errors, and they do not "
        "guarantee a successful deployment."
    )
    replacement = (
        f"{operator.capitalize()} {frequency} deployment, the team reviews the release checklist, "
        "is responsible for checking the quota, and checks the rollback plan. "
        "These checks are intended to reduce avoidable "
        "operational errors and do not guarantee a successful deployment."
    )

    decision = _decision(source, replacement)

    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["temporal_scope_changed"]


@pytest.mark.parametrize("operator", ["before", "after", "during"])
def test_recurring_condition_can_move_with_its_original_action(operator: str) -> None:
    source = (
        f"The release checklist is reviewed by the team {operator} each deployment. "
        "The team checks the quota."
    )
    replacement = (
        f"{operator.capitalize()} each deployment, the team reviews the release checklist. "
        "The team checks the quota."
    )

    assert list(_decision(source, replacement).accepted.values()) == [replacement]


def test_already_shared_recurring_condition_keeps_both_original_actions() -> None:
    source = (
        "The team is responsible for reviewing the checklist and checking the quota "
        "before each deployment. The team also checks the rollback plan."
    )
    replacement = (
        "Before each deployment, the team is responsible for reviewing the checklist "
        "and checking the quota. "
        "The team checks the rollback plan."
    )

    assert list(_decision(source, replacement).accepted.values()) == [replacement]
