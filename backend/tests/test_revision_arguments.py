import pytest

from app.document_transform import models
from app.document_transform.ir import parse_markdown
from app.document_transform.safety import PatchDecision, rewrite_constraints, validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase


SOURCE = (
    "Obtain a valid lease before recovery. Read the checkpoint. "
    "If the write is verified, return its result. Otherwise, retry with the required revision. "
    "Save the readback. Do not retry a rejected revision."
)
REGRESSION = (
    "Obtain a valid lease before recovery. Read the checkpoint. "
    "If the write is verified, return its result; otherwise, retry the required revision. "
    "Save the readback. Do not retry a rejected revision."
)


def _decision(source: str, replacement: str) -> PatchDecision:
    document = parse_markdown(source, title="Revision-aware recovery")
    block = document.blocks[0]
    return validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )


def test_live_model_regression_cannot_turn_revision_argument_into_retry_target() -> None:
    decision = _decision(SOURCE, REGRESSION)

    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["revision_argument_changed"]


@pytest.mark.parametrize(
    "qualifier",
    ["required", "current", "expected", "matching", "source", "specified", "previous", "latest"],
)
def test_qualified_revision_arguments_cannot_disappear(qualifier: str) -> None:
    decision = _decision(
        f"In order to recover, retry the write with the {qualifier} revision.",
        f"To recover, retry the {qualifier} revision.",
    )

    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["revision_argument_changed"]


def test_equivalent_revision_argument_phrasing_can_shorten_surrounding_prose() -> None:
    decision = _decision(
        "Retry the write with the required revision in order to recover.",
        "Retry the write using the required revision to recover.",
    )

    assert list(decision.accepted.values()) == [
        "Retry the write using the required revision to recover."
    ]
    assert not decision.findings


def test_revision_arguments_preserve_multiplicity() -> None:
    decision = _decision(
        "Retry the first write with the current revision; retry the second write with the current revision.",
        "Retry the first write with the current revision; retry the second write.",
    )

    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["revision_argument_changed"]


def test_revision_identifier_case_remains_meaningful() -> None:
    decision = _decision(
        "Retry with requiredRevisionId in order to recover.",
        "Retry with requiredrevisionid to recover.",
    )

    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["revision_argument_changed"]


@pytest.mark.parametrize("identifier", ["requiredRevisionId", "revisionId"])
@pytest.mark.parametrize("ticks", ["", "`", "``"])
def test_inline_code_keeps_its_revision_argument_role(identifier: str, ticks: str) -> None:
    literal = ticks + identifier + ticks
    decision = _decision(
        f"Retry the write with {literal} in order to recover.",
        f"Retry {literal} to recover.",
    )

    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["revision_argument_changed"]


@pytest.mark.parametrize(
    "compound",
    [
        "revision control",
        "revision-control",
        "revision‑control",
        "revision history",
        "revision tracking",
        "revision management",
    ],
)
def test_revision_control_software_is_not_inferred_to_be_a_write_guard(compound: str) -> None:
    source = f"Use the latest {compound} software in order to retain history."
    replacement = f"Retain history in the latest {compound} software."

    assert rewrite_constraints(source).revision_arguments == ()
    assert list(_decision(source, replacement).accepted.values()) == [replacement]


def test_revision_role_inventory_reaches_writer_and_repair_feedback() -> None:
    document = parse_markdown(SOURCE, title="Revision-aware recovery")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=REGRESSION)

    assert rewrite_constraints(SOURCE).revision_arguments == ("required revision",)
    assert "revision_arguments" in models._prompt(document, phase=TransformPhase.UNSLOP)
    assert any(
        "revision_arguments" in item and '"required revision":1' in item
        for item in models._constraint_repair_feedback(block, patch)
    )
