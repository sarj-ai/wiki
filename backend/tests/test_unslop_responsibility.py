import pytest

from app.document_transform.safety import rewrite_constraints
from tests.test_editorial_framing_constraints import _decision


@pytest.mark.parametrize(
    ("duty", "action"),
    [
        ("is responsible for checking the quota", "checks the quota"),
        ("is responsible for managing the deployment", "manages the deployment"),
        ("remains responsible for retaining the logs", "retains the logs"),
    ],
)
@pytest.mark.parametrize("context", ["", "The release is pending. "])
def test_assigned_responsibility_does_not_establish_an_action(duty: str, action: str, context: str):
    decision = _decision(f"{context}The team {duty}.", f"{context}The team {action}.")

    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["force_language_changed"]


def test_observed_action_cannot_gain_an_unstated_assignment_of_responsibility() -> None:
    decision = _decision(
        "The team checks the quota.", "The team is responsible for checking the quota."
    )

    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["force_language_changed"]


@pytest.mark.parametrize("spacing", [" ", "  ", "\t"])
def test_responsibility_constraint_normalizes_only_whitespace(spacing: str) -> None:
    constraints = rewrite_constraints(f"The team is RESPONSIBLE{spacing}FOR checking the quota.")

    assert constraints.force_language == ("responsible for",)


def test_a_duty_can_lose_nominalization_while_keeping_its_assignment() -> None:
    source = "The team is responsible for conducting an analysis of the report."
    proposed = "The team is responsible for analyzing the report."

    assert list(_decision(source, proposed).accepted.values()) == [proposed]


def test_actual_live_repair_preserves_scope_but_still_loses_responsibility() -> None:
    source = (
        "The release checklist is reviewed by the team before each deployment. "
        "The team is responsible for checking the quota. The team also checks the rollback plan. "
        "These checks are intended to reduce avoidable operational errors, and they do not "
        "guarantee a successful deployment."
    )
    proposed = (
        "The team reviews the release checklist before each deployment. The team checks "
        "the quota and rollback plan. These checks are intended to reduce avoidable "
        "operational errors and do not guarantee a successful deployment."
    )
    decision = _decision(source, proposed)

    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["force_language_changed"]
