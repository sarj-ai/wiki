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


@pytest.mark.parametrize("overview", ["a comprehensive overview", "an overview"])
def test_incident_overview_can_state_what_its_document_covers(overview: str) -> None:
    source = (
        f"This document aims to provide {overview} of the checkout incident "
        "that occurred on 14 October, delve into its root cause, and highlight the key "
        "learnings that will help us move forward together as a team."
    )
    target = (
        "This document covers the checkout incident that occurred on 14 October, delves "
        "into its root cause, and highlights the key learnings that will help us move "
        "forward together as a team."
    )

    decision = _decision(source, target)

    assert decision.accepted == {"b0": target}, decision.findings
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
        (
            "The service aims to provide a comprehensive overview of each incident.",
            "The service provides a comprehensive overview of each incident.",
        ),
        (
            "This report aims to provide a comprehensive overview of the checkout incident.",
            "The checkout incident describes the report.",
        ),
        (
            "The editor wrote “This report aims to provide an overview of the incident.”",
            "The editor wrote “This report covers the incident.”",
        ),
    ],
)
def test_nominal_framing_keeps_goals_uncertainty_quotes_and_actor(source: str, target: str) -> None:
    assert not _decision(source, target).accepted
