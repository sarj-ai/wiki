from datetime import timedelta

import pytest

from app.document_transform.readability import plan_readability
from app.document_transform.readability_types import PlannedReadabilityCheckpoint
from app.performance import provider_retry_budget
from tests.test_document_readability import _fixture, _LayoutEditor, _plan


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

    assert legacy.accepted_improvements is None
    assert legacy.stop_reason == "unrecorded"
    assert legacy.passes == plan.passes
