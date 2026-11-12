import pytest

from app.document_transform.ir import parse_markdown
from app.document_transform.safety import PatchDecision, validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase


def _decision(source: str, target: str) -> PatchDecision:
    document = parse_markdown(source, title="QA cache")
    block = document.blocks[0]
    return validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=target)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )


@pytest.mark.parametrize("subject", ["This note", "The document", "Our guide"])
def test_self_referential_nominal_description_can_be_direct(subject: str) -> None:
    source = f"{subject} is intended to provide a description of the cache. The cache may be stale."
    target = f"{subject} describes the cache. The cache may be stale."

    decision = _decision(source, target)

    assert decision.accepted == {"b0": target}
    assert not decision.findings


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (
            "The queue is intended to provide a description of each task.",
            "The queue provides a description of each task.",
        ),
        (
            "This note is intended to provide data that may be incomplete.",
            "This note provides data that may be incomplete.",
        ),
        (
            "This note is intended to provide a description of the cache. The cache may be stale.",
            "This note describes the cache. The cache is stale.",
        ),
        (
            "The fixture says “This note is intended to provide a description of the cache.”",
            "The fixture says “This note describes the cache.”",
        ),
        (
            "This note is intended to provide a description of the cache.",
            "The cache describes itself.",
        ),
    ],
)
def test_nominal_framing_keeps_goals_uncertainty_quotes_and_actor(source: str, target: str) -> None:
    assert not _decision(source, target).accepted
