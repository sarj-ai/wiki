import pytest

from app.document_transform.ir import parse_markdown
from app.document_transform.planner import TextTransformPlanner
from app.document_transform.safety import validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase
from tests.test_editorial_refinement import _RefiningEditor, _request


@pytest.mark.parametrize(
    ("source", "replacement"),
    [
        (
            "Concurrency is the number of operations in flight.",
            "Concurrency is operations in flight.",
        ),
        ("Queue depth is the number of jobs waiting.", "Queue depth is jobs waiting."),
        ("Occupancy is the count of occupied slots.", "Occupancy is occupied slots."),
        ("Cardinality is the number of distinct elements.", "Cardinality is distinct elements."),
        ("The backlog is the number of pending requests.", "The backlog is pending requests."),
        ("Concurrency: the number of operations in flight.", "Concurrency: operations in flight."),
        ("Queue depth denotes the number of jobs waiting.", "Queue depth denotes jobs waiting."),
        (
            "Queue depth represents the count of jobs waiting.",
            "Queue depth represents jobs waiting.",
        ),
        ("Queue depth is the number of jobs waiting.", ""),
        (
            "Queue depth is the number of jobs waiting. The metric counts retries.",
            "Queue depth is jobs waiting. The metric counts retries.",
        ),
        (
            "Concurrency is the number of operations in flight. Queue depth is the number of jobs waiting.",
            "Concurrency counts operations in flight. Queue depth is jobs waiting.",
        ),
        (
            "Queue depth is the number of jobs waiting. Queue depth counts vary.",
            "Queue depth is jobs waiting. Queue depth counts vary.",
        ),
        (
            "`N` is the number of queued jobs; `M` is the number of active jobs.",
            "`N` counts queued jobs; `M` is active jobs.",
        ),
        (
            "‘N’ is the number of queued jobs; ‘M’ is the number of active jobs.",
            "‘N’ counts queued jobs; ‘M’ is active jobs.",
        ),
        (
            "Occupancy is the number of occupied slots; occupancy is the number of reserved bytes.",
            "Occupancy counts occupied slots; occupancy is reserved bytes.",
        ),
        (
            "On Linux, occupancy is the number of occupied slots. On Windows, occupancy is the number of occupied slots.",
            "On Linux, occupancy counts occupied slots. On Windows, occupancy is occupied slots.",
        ),
        (
            "The sum of `queued` and `running` is the number of active jobs.",
            "The sum of `queued` and `running` is active jobs.",
        ),
        (
            "The sum of ‘queued’ and ‘running’ is the number of active jobs.",
            "The sum of ‘queued’ and ‘running’ is active jobs.",
        ),
        (
            "`N` is the number of queued jobs; `n` is the number of queued jobs.",
            "`N` counts queued jobs; `n` is queued jobs.",
        ),
        (
            "Load is the number of `N` jobs; load is the number of `n` jobs.",
            "Load counts `N` jobs; load is `n` jobs.",
        ),
        (
            "Depth is the number of queued jobs. Example: ``console.log(`Stats. Depth counts queued jobs.`)``.",
            "Depth is queued jobs. Example: ``console.log(`Stats. Depth counts queued jobs.`)``.",
        ),
        (
            'The panel width is inches(6"). Depth is the number of queued jobs. The next panel width is inches(8").',
            'The panel width is inches(6"). Depth is queued jobs. The next panel width is inches(8").',
        ),
        (
            "Example: ``token = ``` ``. Depth is the number of queued jobs. Example: ``token = ``` ``.",
            "Example: ``token = ``` ``. Depth is queued jobs. Example: ``token = ``` ``.",
        ),
    ],
)
def test_count_definitions_keep_their_quantity_dimension(source: str, replacement: str) -> None:
    document = parse_markdown(source, title="Dimensional definitions")
    block = document.blocks[0]

    result = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert not result.accepted
    assert [finding.code for finding in result.findings] == ["count_definition_changed"]


@pytest.mark.parametrize(
    ("source", "replacement"),
    [
        (
            "Concurrency is the number of operations in flight.",
            "Concurrency counts operations in flight.",
        ),
        ("Queue depth is the number of jobs waiting.", "Queue depth counts jobs waiting."),
        ("Occupancy is the count of occupied slots.", "Occupancy counts occupied slots."),
        (
            "Cardinality is the number of distinct elements.",
            "Cardinality counts distinct elements.",
        ),
        ("The backlog is the number of pending requests.", "Backlog counts pending requests."),
        ("Queue depth is the number of jobs waiting.", "Queue depth: the count of jobs waiting."),
        (
            "Queue depth is the number of queued jobs.",
            "Queue depth is the queued job count.",
        ),
        (
            "Cardinality is the number of distinct entries.",
            "Cardinality is the distinct entry count.",
        ),
        (
            "Concurrency is the number of active processes.",
            "Concurrency is the active process count.",
        ),
        (
            "`N` is the number of queued jobs; `M` is the number of active jobs.",
            "`N` counts queued jobs; `M` counts active jobs.",
        ),
        (
            "In the service, concurrency is the number of operations in flight.",
            "Concurrency counts operations in flight in the service.",
        ),
        (
            "Concurrency simply is the number of operations in flight.",
            "Concurrency counts operations in flight.",
        ),
        (
            "Concurrency is the number of active jobs. Concurrency is the number of active jobs.",
            "Concurrency counts active jobs.",
        ),
        (
            "Concurrency is the number of active jobs. Concurrency is the count of active jobs.",
            "Concurrency counts active jobs.",
        ),
        (
            "`N` is the number of queued jobs; `n` is the number of queued jobs.",
            "`N` counts queued jobs; `n` counts queued jobs.",
        ),
        (
            "Depth is the number of queued jobs. Example: ``console.log(`Stats. Depth counts queued jobs.`)``.",
            "Depth counts queued jobs. Example: ``console.log(`Stats. Depth counts queued jobs.`)``.",
        ),
        (
            'The panel width is inches(6"). Depth is the number of queued jobs. The next panel width is inches(8").',
            'The panel width is inches(6"). Depth counts queued jobs. The next panel width is inches(8").',
        ),
        (
            "Example: ``token = ``` ``. Depth is the number of queued jobs. Example: ``token = ``` ``.",
            "Example: ``token = ``` ``. Depth counts queued jobs. Example: ``token = ``` ``.",
        ),
        (
            "Concurrency is the number of operations in flight.",
            "Concurrency: how many operations are in flight.",
        ),
        (
            "Concurrency is the number of operations in flight. Queue depth is the number of jobs waiting.",
            "Queue depth counts jobs waiting. Concurrency counts operations in flight.",
        ),
        (
            "The number of pending requests was reviewed by the team before deployment.",
            "The team reviewed the number of pending requests before deployment.",
        ),
        (
            "The report says `Queue depth is the number of jobs waiting`. It was approved by the team.",
            "The report says `Queue depth is the number of jobs waiting`. The team approved it.",
        ),
    ],
)
def test_faithful_count_definitions_and_unrelated_prose_remain_editable(
    source: str, replacement: str
) -> None:
    document = parse_markdown(source, title="Faithful count definitions")
    block = document.blocks[0]

    result = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert result.accepted == {block.id: replacement}


@pytest.fixture
def count_editor() -> _RefiningEditor:
    return _RefiningEditor(("Concurrency is operations in flight.",))


async def test_missing_count_never_reaches_a_permissive_factual_reviewer(
    count_editor: _RefiningEditor,
) -> None:
    source = "Concurrency is the number of operations in flight."
    original = parse_markdown(source, title="Count definition source")
    result = await TextTransformPlanner(generator=count_editor, allow_unslop_model=True).transform(
        original, _request(source)
    )

    assert result.document == original
    assert not count_editor.sources
    assert any(finding.code == "count_definition_changed" for finding in result.findings)


@pytest.fixture
def refining_count_editor() -> _RefiningEditor:
    return _RefiningEditor(
        (
            "Concurrency counts operations in flight.",
            "Concurrency is operations in flight.",
        )
    )


async def test_original_count_definition_survives_later_refinement(
    refining_count_editor: _RefiningEditor,
) -> None:
    source = "Concurrency is the number of operations in flight."

    result = await TextTransformPlanner(
        generator=refining_count_editor, allow_unslop_model=True
    ).transform(parse_markdown(source, title="Original count definition"), _request(source))

    assert result.document.blocks[0].text == "Concurrency counts operations in flight."
    assert result.model_count == 1
