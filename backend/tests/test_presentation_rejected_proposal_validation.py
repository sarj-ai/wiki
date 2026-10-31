from typing import NamedTuple, override
import uuid

import pytest

from app.document_transform.ir import DocumentIR
from app.document_transform.models import PatchGenerator
from app.document_transform.presentation_executor import PresentationTransformExecutor
from app.document_transform.schemas import OutputStrategy, TextPatch, TransformPhase
from app.drive.schemas import MIME_SLIDE
from tests.test_presentation_transform import PresentationGatewaySpy, _request


class _RejectedProposal(PatchGenerator):
    supports_semantic_rewrite = True

    def __init__(self) -> None:
        self.calls = 0

    @property
    @override
    def model_ids(self) -> tuple[str, ...]:
        return ("test-reviewer",)

    @override
    async def generate(self, document: DocumentIR, *, phase: TransformPhase) -> list[TextPatch]:
        assert phase is TransformPhase.UNSLOP
        self.calls += 1
        block = document.blocks[0]
        return [
            TextPatch(
                block_id=block.id,
                expected_sha256=block.sha256,
                replacement="Night song attracts mates.",
            )
        ]


class _RejectedScenario(NamedTuple):
    gateway: PresentationGatewaySpy
    editor: _RejectedProposal
    executor: PresentationTransformExecutor


@pytest.fixture
def rejected_scenario() -> _RejectedScenario:
    gateway = PresentationGatewaySpy(mime_type=MIME_SLIDE)
    editor = _RejectedProposal()
    executor = PresentationTransformExecutor(
        gateway=gateway,
        generator=editor,
        logo_uri="https://wiki.sarj.ai/brand/sarj-logo-light.png",
    )
    return _RejectedScenario(gateway, editor, executor)


@pytest.mark.parametrize("readback_matches", [False, True])
async def test_rejected_proposal_requires_review_but_preservation_follows_actual_readback(
    rejected_scenario: _RejectedScenario, *, readback_matches: bool
):
    gateway, editor, executor = rejected_scenario
    text = (
        "The researchers generally observed males singing at night before pairing and "
        "interpreted this pattern as evidence of mate attraction."
    )
    for presentation in (gateway.snapshot.presentation, gateway.output_presentation):
        assert presentation is not None
        run = presentation["slides"][0]["pageElements"][0]["shape"]["text"]["textElements"][1]
        run["textRun"]["content"] = text + "\n"
        run["endIndex"] = len(text) + 1
    gateway.verification_content_preserved = readback_matches
    result = await executor.execute(
        operation_id=uuid.uuid7(), request=_request(output_strategy=OutputStrategy.COPY)
    )
    assert editor.calls == 1
    assert result.applied_patch_count == 0
    assert result.validation.unsafe_patch_count == 1
    assert result.validation.protected_content_preserved is readback_matches
    assert result.needs_review
    assert "scope_qualifier_changed" in {finding.code for finding in result.findings}
    assert ("post_write_content_mismatch" in {finding.code for finding in result.findings}) is (
        not readback_matches
    )
