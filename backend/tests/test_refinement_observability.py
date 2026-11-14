from collections.abc import Iterator
from datetime import timedelta

from loguru import logger
from pydantic import BaseModel, JsonValue
import pytest

from app.document_transform.errors import ModelUnavailableError
from app.document_transform.ir import parse_markdown
from app.document_transform.planner import TextTransformPlanner
from app.document_transform.readability import plan_readability
from app.performance import provider_retry_budget
from tests.test_document_readability import _fixture, _LayoutEditor
from tests.test_editorial_refinement import _RefiningEditor, _request


class _LogRecord(BaseModel):
    message: str
    extra: dict[str, JsonValue]


class _LogEnvelope(BaseModel):
    record: _LogRecord


@pytest.fixture
def refinement_messages() -> Iterator[list[str]]:
    messages: list[str] = []
    sink = logger.add(messages.append, serialize=True)
    try:
        yield messages
    finally:
        logger.remove(sink)


@pytest.fixture
def refining_editor_factory() -> type[_RefiningEditor]:
    return _RefiningEditor


@pytest.fixture
def layout_editor_factory() -> type[_LayoutEditor]:
    return _LayoutEditor


def _record(messages: list[str], name: str) -> dict[str, JsonValue]:
    records = [_LogEnvelope.model_validate_json(message).record for message in messages]
    (record,) = [record for record in records if record.message == name]
    return record.extra


async def test_editorial_log_counts_100_real_loop_improvements_as_a_pass_limit(
    refinement_messages: list[str], refining_editor_factory: type[_RefiningEditor]
) -> None:
    fact = "The team reviews and approves the release document before publication."
    source = fact + " padding" * 200
    original = parse_markdown(source, title="Private fixture")
    editor = refining_editor_factory(tuple(fact + " padding" * (199 - i) for i in range(100)))
    outcome = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        original, _request(source)
    )
    record = _record(refinement_messages, "document editorial refinement finished")
    assert record["editorial_attempted"] == editor.calls == 100
    assert record["editorial_accepted"] == 100
    assert record["editorial_stop_reason"] == "pass_limit"
    assert record["source_sha256"] == original.text_sha256
    assert record["result_sha256"] == outcome.document.text_sha256
    assert source not in "".join(refinement_messages)


@pytest.mark.parametrize("short_budget", [False, True])
async def test_editorial_log_distinguishes_convergence_from_the_write_reserve(
    *,
    short_budget: bool,
    refinement_messages: list[str],
    refining_editor_factory: type[_RefiningEditor],
) -> None:
    source = "The release document was reviewed and approved by the team."
    final = "The team reviewed and approved the release document."
    editor = refining_editor_factory((final,))
    with provider_retry_budget(timedelta(seconds=170 if short_budget else 1000)):
        await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
            parse_markdown(source, title="Private fixture"), _request(source)
        )
    record = _record(refinement_messages, "document editorial refinement finished")
    assert record["editorial_attempted"] == editor.calls == (1 if short_budget else 2)
    assert record["editorial_accepted"] == 1
    assert record["editorial_stop_reason"] == ("deadline_reserve" if short_budget else "converged")


async def test_failed_first_editorial_attempt_is_recorded_without_accepting_a_candidate(
    refinement_messages: list[str], refining_editor_factory: type[_RefiningEditor]
) -> None:
    source = "The team reviewed the release document."
    editor = refining_editor_factory((source,), outage_after=0)
    with pytest.raises(ModelUnavailableError):
        await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
            parse_markdown(source, title="Private fixture"), _request(source)
        )
    record = _record(refinement_messages, "document editorial refinement finished")
    assert record["editorial_attempted"] == editor.calls == 1
    assert record["editorial_accepted"] == 0
    assert record["editorial_stop_reason"] == "model_unavailable"


async def test_layout_log_reports_the_actual_checkpoint_counts(
    refinement_messages: list[str], layout_editor_factory: type[_LayoutEditor]
) -> None:
    editor = layout_editor_factory()
    plan = await plan_readability(
        _fixture("before"), title="Private fixture", generator=editor, max_passes=1
    )
    assert plan is not None
    record = _record(refinement_messages, "document layout refinement finished")
    assert record["layout_refinement"] == plan.refinement.model_dump(mode="json")
    assert plan.passes == plan.accepted_improvements == editor.calls == 1
    assert plan.stop_reason == "pass_limit"


async def test_optional_editorial_failure_reports_the_preserved_reviewed_draft(
    refinement_messages: list[str], refining_editor_factory: type[_RefiningEditor]
) -> None:
    source = "The release document was reviewed and approved by the team."
    final = "The team reviewed and approved the release document."
    editor = refining_editor_factory((final,), outage_after=1)
    outcome = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        parse_markdown(source, title="Private fixture"), _request(source)
    )
    record = _record(refinement_messages, "document editorial refinement finished")
    assert outcome.document.blocks[0].text == final
    assert record["editorial_attempted"] == editor.calls == 2
    assert record["editorial_accepted"] == 1
    assert record["editorial_stop_reason"] == "model_unavailable"
    assert record["result_sha256"] == outcome.document.text_sha256
