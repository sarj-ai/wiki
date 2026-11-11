from datetime import timedelta

import pytest

from app.document_transform.readability import plan_readability
from app.performance import provider_retry_budget
from tests.test_document_readability import _fixture, _LayoutEditor


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


async def test_budget_skipped_layout_reports_no_model_calls() -> None:
    editor = _LayoutEditor()

    with provider_retry_budget(timedelta(seconds=89)):
        plan = await plan_readability(_fixture("before"), title="Short budget", generator=editor)

    assert plan is not None
    assert plan.tables
    assert not plan.paragraphs
    assert not plan.model_invoked
    assert plan.passes == editor.calls == 0
