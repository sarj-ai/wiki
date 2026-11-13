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
            "The QA worker must not retry rejected writes, except after a fresh revision is obtained.",
            "The QA worker must not retry rejected writes, except after obtaining a fresh revision.",
        ),
        (
            "The inspector may release the file only after it has been approved.",
            "The inspector may release the file only after approving it.",
        ),
        (
            "The observer waits until the sample has been measured.",
            "The observer waits until measuring the sample.",
        ),
        (
            "The worker retries after a fresh revision is obtained by 2026-11-13.",
            "The worker retries after obtaining a fresh revision by 2026-11-13.",
        ),
        (
            "The operator pauses before the record is written.",
            "The operator pauses before writing the record.",
        ),
        (
            "The operator proceeds after the token has been verified.",
            "The operator proceeds after personally verifying the token.",
        ),
        (
            "The inspector may release the file only after it was formally approved.",
            "The inspector may release the file only after formally approving it.",
        ),
    ],
)
def test_actorless_passive_conditions_cannot_assign_actions_to_the_main_subject(
    source: str, replacement: str
) -> None:
    document = parse_markdown(source, title="Conditional actors")
    block = document.blocks[0]
    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["conditional_actor_changed"]


@pytest.mark.parametrize(
    ("source", "replacement"),
    [
        (
            "The operator retries after a fresh revision is obtained by the operator.",
            "The operator retries after obtaining a fresh revision.",
        ),
        (
            "The inspector may release the file only after it has been approved.",
            "The inspector may release the file only after it is approved.",
        ),
        (
            "The worker can proceed after the token is verified. The worker can retain the result.",
            "The worker can proceed after the token is verified and can retain the result.",
        ),
    ],
)
def test_source_identified_actors_and_agentless_conditions_remain_editable(
    source: str, replacement: str
) -> None:
    document = parse_markdown(source, title="Faithful conditions")
    block = document.blocks[0]
    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert decision.accepted == {block.id: replacement}


@pytest.fixture
def conditional_editor() -> _RefiningEditor:
    return _RefiningEditor(("The inspector may release the file only after approving it.",))


async def test_an_approving_reviewer_cannot_authorize_an_unstated_conditional_actor(
    conditional_editor: _RefiningEditor,
) -> None:
    source = "The inspector may release the file only after it has been approved."
    original = parse_markdown(source, title="Observed actor regression")

    result = await TextTransformPlanner(
        generator=conditional_editor, allow_unslop_model=True
    ).transform(original, _request(source))

    assert result.document == original
    assert not conditional_editor.sources
    assert any(finding.code == "conditional_actor_changed" for finding in result.findings)
