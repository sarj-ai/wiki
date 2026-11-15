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
            (
                "Option A costs $1,234.50 per month and supports replay. "
                "Option B costs $934.50 per month but lacks replay. "
                "We prefer A only while replay is required; revisit after the trial."
            ),
            (
                "Option A costs $1,234.50 per month and supports replay. "
                "Option B costs $934.50 per month without replay. "
                "Prefer A only while replay is required; revisit after the trial."
            ),
        ),
        ("We prefer replay.", "Prefer replay."),
        ("I prefer replay.", "Prefer replay."),
        ("Our preference is replay.", "Prefer replay."),
        ("Replay is preferred by us.", "Prefer replay."),
        ("We prefer replay.", "I prefer replay."),
        ("We prefer replay.", ""),
        (
            "We prefer replay. Example: `we prefer replay`.",
            "Prefer replay. Example: `we prefer replay`.",
        ),
        (
            "We prefer replay. Example: “we prefer replay”.",
            "Prefer replay. Example: “we prefer replay”.",
        ),
    ],
)
def test_preference_keeps_its_explicit_source_actor(source: str, replacement: str) -> None:
    document = parse_markdown(source, title="Source preference")
    block = document.blocks[0]

    result = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert not result.accepted
    assert any(finding.code == "preference_attribution_changed" for finding in result.findings)


@pytest.mark.parametrize(
    "replacement",
    ["We prefer replay.", "Replay is our preference.", "Replay is preferred by us."],
)
def test_faithful_preference_forms_remain_editable(replacement: str) -> None:
    source = "It is important to note that we prefer replay."
    document = parse_markdown(source, title="Faithful preference")
    block = document.blocks[0]

    result = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert result.accepted == {block.id: replacement}


@pytest.fixture
def preference_editor() -> _RefiningEditor:
    return _RefiningEditor(("Prefer replay.",))


async def test_missing_preference_actor_never_reaches_a_permissive_reviewer(
    preference_editor: _RefiningEditor,
) -> None:
    source = "We prefer replay."
    original = parse_markdown(source, title="Attributed preference")

    result = await TextTransformPlanner(
        generator=preference_editor, allow_unslop_model=True
    ).transform(original, _request(source))

    assert result.document == original
    assert not preference_editor.sources
    assert any(finding.code == "preference_attribution_changed" for finding in result.findings)


@pytest.fixture
def refining_preference_editor() -> _RefiningEditor:
    return _RefiningEditor(("We prefer replay.", "Prefer replay."))


async def test_original_preference_actor_survives_later_refinement(
    refining_preference_editor: _RefiningEditor,
) -> None:
    source = "Our preference is for replay."

    result = await TextTransformPlanner(
        generator=refining_preference_editor, allow_unslop_model=True
    ).transform(parse_markdown(source, title="Original preference"), _request(source))

    assert result.document.blocks[0].text == "We prefer replay."
    assert result.model_count == 1
