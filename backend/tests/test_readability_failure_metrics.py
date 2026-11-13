from datetime import timedelta
from typing import override

from pydantic import JsonValue, ValidationError
import pytest

from app.document_transform.errors import ModelDeadlineError
from app.document_transform.ir import DocumentIR
from app.document_transform.readability import plan_readability
from app.document_transform.readability_types import (
    FinishedReadabilityRefinement,
    LegacyReadabilityRefinement,
    PlannedReadabilityCheckpoint,
    ReadabilityProposal,
)
from app.performance import provider_retry_budget
from tests.test_document_readability import _fixture, _LayoutEditor, _plan


class _DeadlineAfterImprovement(_LayoutEditor):
    @override
    async def plan_readability(
        self, document: DocumentIR, *, previous: list[ReadabilityProposal], feedback: str = ""
    ) -> list[ReadabilityProposal]:
        if self.calls:
            self.calls += 1
            raise ModelDeadlineError
        return await super().plan_readability(document, previous=previous, feedback=feedback)


@pytest.fixture
def deadline_editor() -> _DeadlineAfterImprovement:
    return _DeadlineAfterImprovement()


@pytest.mark.parametrize("outage", [True, False])
async def test_table_only_receipt_reports_attempted_layout_calls_after_provider_failure(
    outage: bool,
) -> None:
    editor = _LayoutEditor(unavailable=outage, invalid=not outage)

    plan = await plan_readability(_fixture("before"), title="Layout outage", generator=editor)

    assert plan is not None
    assert not plan.paragraphs
    assert plan.tables
    assert plan.model_invoked
    assert plan.passes == editor.calls == (1 if outage else 2)
    assert plan.accepted_improvements == 0
    assert plan.stop_reason == ("provider_unavailable" if outage else "invalid_proposal")


async def test_budget_skipped_layout_reports_no_model_calls() -> None:
    editor = _LayoutEditor()

    with provider_retry_budget(timedelta(seconds=89)):
        plan = await plan_readability(_fixture("before"), title="Short budget", generator=editor)

    assert plan is not None
    assert plan.tables
    assert not plan.paragraphs
    assert not plan.model_invoked
    assert plan.passes == editor.calls == 0
    assert plan.accepted_improvements == 0
    assert plan.stop_reason == "deadline_reserve"


async def test_improving_pass_limit_is_distinct_from_convergence() -> None:
    editor = _LayoutEditor()

    plan = await plan_readability(
        _fixture("before"), title="One improving pass", generator=editor, max_passes=1
    )

    assert plan is not None
    assert plan.passes == plan.accepted_improvements == editor.calls == 1
    assert plan.stop_reason == "pass_limit"


async def test_legacy_receipt_does_not_invent_an_accepted_iteration_count() -> None:
    plan = await _plan()

    legacy = PlannedReadabilityCheckpoint.model_validate(
        plan.model_dump(exclude={"accepted_improvements", "stop_reason"})
    )

    assert legacy.model_dump(include={"accepted_improvements", "stop_reason", "passes"}) == {
        "accepted_improvements": None,
        "stop_reason": "unrecorded",
        "passes": plan.passes,
    }


async def test_provider_deadline_keeps_the_best_draft_and_attempted_pass_count(
    deadline_editor: _DeadlineAfterImprovement,
) -> None:
    plan = await plan_readability(_fixture("before"), title="Deadline", generator=deadline_editor)

    assert plan is not None
    assert plan.model_dump(
        include={"passes", "accepted_improvements", "planning_failure_code", "stop_reason"}
    ) == {
        "passes": 2,
        "accepted_improvements": 1,
        "planning_failure_code": "model_deadline_exhausted",
        "stop_reason": "deadline_reserve",
    }
    assert deadline_editor.calls == 2
    assert len(plan.paragraphs) == 3


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model_invoked", False),
        ("passes", 0),
        ("passes", True),
        ("accepted_improvements", 101),
        ("accepted_improvements", None),
        ("planning_failure_code", "model_unavailable"),
        ("stop_reason", "not_eligible"),
        ("stop_reason", "deadline_reserve"),
        ("stop_reason", "invalid_proposal"),
        ("stop_reason", "provider_unavailable"),
        ("stop_reason", "unrecorded"),
        ("stop_reason", "invented_outcome"),
    ],
)
async def test_persisted_refinement_rejects_contradictory_state_data(
    field: str, value: JsonValue
) -> None:
    plan = await _plan()
    payload = {**plan.model_dump(), field: value}
    with pytest.raises(ValidationError):
        PlannedReadabilityCheckpoint.model_validate(payload)


async def test_refinement_is_authoritative_and_legacy_projection_round_trips() -> None:
    plan = await _plan()
    assert isinstance(plan.refinement, FinishedReadabilityRefinement)
    wire = plan.model_dump(mode="json")
    assert "refinement" not in wire
    recovered = PlannedReadabilityCheckpoint.model_validate(wire)
    assert recovered.refinement == plan.refinement
    legacy = PlannedReadabilityCheckpoint.model_validate(
        plan.model_dump(exclude={"accepted_improvements", "stop_reason"})
    )
    assert isinstance(legacy.refinement, LegacyReadabilityRefinement)
    assert legacy.refinement.accepted_improvements is None
    with pytest.raises(ValidationError):
        PlannedReadabilityCheckpoint.model_validate({**wire, "refinement": plan.refinement})


async def test_legacy_invocation_evidence_is_not_rewritten_from_an_unrecorded_count() -> None:
    plan = await _plan()
    wire = plan.model_dump(exclude={"accepted_improvements", "stop_reason"})
    wire["passes"] = 0
    wire["model_invoked"] = True
    legacy = PlannedReadabilityCheckpoint.model_validate(wire)
    assert isinstance(legacy.refinement, LegacyReadabilityRefinement)
    assert legacy.model_invoked
    assert legacy.passes == 0
    assert legacy.accepted_improvements is None
    assert legacy.model_dump(include={"model_invoked", "passes"}) == {
        "model_invoked": True,
        "passes": 0,
    }


async def test_receipt_metadata_filtering_preserves_native_model_dump_behavior() -> None:
    plan = await _plan()
    wire = plan.model_dump()
    assert isinstance(wire["tables"], tuple)
    assert plan.model_dump(include={"tables"}) == {"tables": wire["tables"]}
    assert plan.model_dump(include={"passes": False}) == {"passes": plan.passes}
    assert plan.model_dump(exclude={"passes": False})["passes"] == plan.passes
    assert "passes" not in plan.model_dump(exclude={"passes": True})
    assert plan.model_dump_json(include={"passes"}) == f'{{"passes":{plan.passes}}}'
