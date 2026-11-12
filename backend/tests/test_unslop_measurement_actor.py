import pytest

from app.document_transform.ir import parse_markdown
from app.document_transform.measurement_actor import reassigned_measurement_action
from app.document_transform.planner import TextTransformPlanner
from app.document_transform.safety import validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase
from tests.test_editorial_refinement import _RefiningEditor, _request


@pytest.mark.parametrize(
    ("source", "replacement"),
    [
        ("The QA estimate is 1/2 µs, not 12 µs.", "QA estimates 1/2 µs, not 12 µs."),
        ("The QA estimate: 1/2 µs, not 12 µs.", "QA estimates 1/2 µs, not 12 µs."),
        (
            "The NASA forecast is approximately 2 GiB.",
            "NASA forecasts approximately 2 GiB.",
        ),
        ("The North Lab measurement is 1.2300 ms.", "North Lab measured 1.2300 ms."),
        ("The Atlas projection is 1.20e-7 s.", "Atlas projects 1.20e-7 s."),
        ("The Ops reading is −0.00 °C.", "Ops reads −0.00 °C."),
    ],
)
def test_nominal_labels_cannot_become_new_measurement_actors(source: str, replacement: str) -> None:
    document = parse_markdown(source, title="Source attribution")
    block = document.blocks[0]
    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["measurement_actor_changed"]


@pytest.mark.parametrize("literal", ["`QA estimates 1/2 µs`", "“QA estimates 1/2 µs”"])
def test_examples_cannot_supply_an_unstated_measurement_actor(literal: str) -> None:
    source = f"The QA estimate is 1/2 µs, not 12 µs. The example is {literal}."
    replacement = f"QA estimates 1/2 µs, not 12 µs. The example is {literal}."

    assert reassigned_measurement_action(source, replacement)


def test_long_unrelated_name_sequences_do_not_create_an_actor_constraint() -> None:
    source = "Atlas " * 5000 + "retains the source."

    assert not reassigned_measurement_action(source, source + " ")


@pytest.mark.parametrize(
    ("source", "replacement"),
    [
        ("The QA estimate is 1/2 µs, not 12 µs.", "The QA estimate: 1/2 µs, not 12 µs."),
        (
            "QA estimated 1/2 µs, not 12 µs. This is the QA estimate.",
            "QA estimated 1/2 µs, not 12 µs.",
        ),
        (
            "QA has estimated 1/2 µs, not 12 µs. This is the QA estimate.",
            "QA estimated 1/2 µs, not 12 µs.",
        ),
        (
            "The example is `QA estimates 1/2 µs`. The QA estimate is 1/2 µs.",
            "The example is `QA estimates 1/2 µs`. The QA estimate: 1/2 µs.",
        ),
    ],
)
def test_faithful_nominal_shortening_and_explicit_source_actors_remain_editable(
    source: str, replacement: str
) -> None:
    document = parse_markdown(source, title="Faithful attribution")
    block = document.blocks[0]
    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert decision.accepted == {block.id: replacement}


@pytest.fixture
def estimator_editor() -> _RefiningEditor:
    return _RefiningEditor(("QA estimates 1/2 µs, not 12 µs.",))


async def test_unstated_estimator_never_reaches_a_permissive_factual_reviewer(
    estimator_editor: _RefiningEditor,
) -> None:
    source = "The QA estimate is 1/2 µs, not 12 µs."
    original = parse_markdown(source, title="Observed live attribution risk")

    result = await TextTransformPlanner(
        generator=estimator_editor, allow_unslop_model=True
    ).transform(original, _request(source))

    assert result.document == original
    assert not estimator_editor.sources
    assert any(finding.code == "measurement_actor_changed" for finding in result.findings)
