from datetime import timedelta
import json
from typing import override

import pytest

from app.document_transform import models as transform_models
from app.document_transform.errors import ModelUnavailableError
from app.document_transform.ir import DocumentIR, parse_markdown
from app.document_transform.models import PatchGenerator, RewriteReview
from app.document_transform.planner import TextTransformPlanner
from app.document_transform.safety import validate_patches
from app.document_transform.schemas import (
    DocumentTransformRequest,
    MarkdownSource,
    TextPatch,
    TransformMode,
    TransformPhase,
)
from app.document_transform.unslop import MAX_EDITORIAL_PASSES
from app.identifiers import FileId
from app.performance import provider_retry_budget


class _RefiningEditor(PatchGenerator):
    def __init__(self, drafts: tuple[str, ...], *, outage_after: int | None = None) -> None:
        self.drafts = drafts
        self.outage_after = outage_after
        self.prompts: list[str] = []
        self.sources: list[str] = []
        self.calls = 0
        self.reject_after: int | None = None

    @property
    @override
    def model_ids(self) -> tuple[str, ...]:
        return ("test-pro-editor",)

    @override
    async def generate(self, document: DocumentIR, *, phase: TransformPhase) -> list[TextPatch]:
        self.calls += 1
        if self.outage_after is not None and self.calls > self.outage_after:
            raise ModelUnavailableError
        self.prompts.append(transform_models._prompt(document))
        block = document.blocks[0]
        return [
            TextPatch(
                block_id=block.id,
                expected_sha256=block.sha256,
                replacement=self.drafts[min(self.calls - 1, len(self.drafts) - 1)],
            )
        ]

    @override
    async def review(self, document: DocumentIR, patches: list[TextPatch]) -> RewriteReview:
        self.sources.append(document.blocks[0].text)
        assert all(patch.expected_sha256 == document.blocks[0].sha256 for patch in patches)
        if self.reject_after is not None and self.calls > self.reject_after:
            return RewriteReview(
                frozenset(),
                {patch.block_id: "The rewrite loses a source fact" for patch in patches},
            )
        return RewriteReview(frozenset(patch.block_id for patch in patches), {})


def _request(source: str) -> DocumentTransformRequest:
    return DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown", name="Editing", content=source, destination_folder_id=FileId("folder")
        ),
        mode=TransformMode.UNSLOP,
    )


async def test_refinement_runs_all_100_improving_passes_without_compounding_the_target() -> None:
    fact = "The team reviews and approves the release document before publication."
    source = fact + " padding" * 200
    drafts = tuple(fact + " padding" * (199 - index) for index in range(MAX_EDITORIAL_PASSES))
    editor = _RefiningEditor(drafts)
    original = parse_markdown(source, title="Editing")
    outcome = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        original, _request(source)
    )
    assert editor.calls == MAX_EDITORIAL_PASSES == 100
    assert outcome.document.blocks[0].text == drafts[-1]
    assert editor.sources == [source] * MAX_EDITORIAL_PASSES
    payloads = [
        json.loads(next(line for line in prompt.splitlines() if line.startswith("[{")))
        for prompt in editor.prompts
    ]
    targets = [payload[0]["concision_target"] for payload in payloads]
    assert all(target == targets[0] for target in targets)
    assert targets[0]["source_words"] == len(source.split())
    assert all(payload[0]["original_text"] == source for payload in payloads)
    assert payloads[1][0]["text"] == drafts[0]
    assert original.blocks[0].text == source


async def test_refinement_continues_beyond_the_word_target_and_stops_at_convergence() -> None:
    source = "The release document was reviewed and approved by the team." + " padding" * 80
    first = "The release document was reviewed and approved by the team."
    final = "The team reviewed and approved the release document."
    editor = _RefiningEditor((first, final, final))
    outcome = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        parse_markdown(source, title="Editing"), _request(source)
    )
    assert editor.calls == 3
    assert outcome.document.blocks[0].text == final
    assert outcome.model_count == 2
    assert not outcome.findings
    assert editor.sources == [source, source]


@pytest.mark.parametrize("failure", ["outage", "factual_loss", "longer", "equal_length_cycle"])
async def test_optional_pass_cannot_discard_or_regress_a_reviewed_draft(failure: str) -> None:
    source = "The team reviewed the release document and then approved the release document."
    first = "The team reviewed and approved the release document."
    next_draft = {
        "outage": first,
        "factual_loss": "The team rejected the release document.",
        "longer": source,
        "equal_length_cycle": "The team approved and reviewed the release document.",
    }[failure]
    editor = _RefiningEditor((first, next_draft), outage_after=1 if failure == "outage" else None)
    editor.reject_after = 1 if failure == "factual_loss" else None
    outcome = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        parse_markdown(source, title="Editing"), _request(source)
    )
    assert editor.calls == 2
    assert outcome.document.blocks[0].text == first
    assert not outcome.findings


async def test_optional_pass_reserves_time_for_verification_and_writes() -> None:
    source = "The release document was reviewed and approved by the team."
    final = "The team reviewed and approved the release document."
    editor = _RefiningEditor((final,))
    with provider_retry_budget(timedelta(seconds=170)):
        outcome = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
            parse_markdown(source, title="Editing"), _request(source)
        )
    assert editor.calls == 1
    assert outcome.document.blocks[0].text == final


_DOCUMENT_SUBJECTS = (
    "This document",
    "This note",
    "The working note",
    "The guide",
    "This report",
    "The article",
    "This section",
    "This page",
    "The overview",
    "Our document",
)
_WRITING_ACTIONS = (
    ("is intended to collect", "collects"),
    ("aims to collect", "collects"),
    ("seeks to summarize", "summarizes"),
    ("is designed to explain", "explains"),
    ("is intended to describe", "describes"),
    ("aims to present", "presents"),
    ("seeks to consolidate", "consolidates"),
    ("is designed to outline", "outlines"),
    ("is intended to list", "lists"),
    ("aims to cover", "covers"),
)


@pytest.mark.parametrize("subject", _DOCUMENT_SUBJECTS)
@pytest.mark.parametrize(("scaffold", "active"), _WRITING_ACTIONS)
def test_100_document_preambles_allow_direct_active_prose(
    subject: str, scaffold: str, active: str
) -> None:
    source = f"{subject} {scaffold} latency estimates from the Google SRE reference so the same information can be read in one place."
    replacement = f"{subject} {active} latency estimates from the Google SRE reference."
    document = parse_markdown(source, title="Latency")
    block = document.blocks[0]
    result = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )
    assert result.accepted == {block.id: replacement}
    assert not result.findings


@pytest.mark.parametrize(
    "source",
    [
        "The service is intended to collect latency estimates from the Google SRE reference.",
        "The release document aims to reduce outages across the platform.",
        'The instruction says "This note is intended to collect estimates".',
        "The release document may collect latency estimates from the Google SRE reference.",
    ],
)
def test_real_intentions_uncertainty_and_quoted_scaffolding_remain_protected(source: str) -> None:
    original = parse_markdown(source, title="Scope")
    block = original.blocks[0]
    replacement = (
        source.replace("is intended to collect", "collects")
        .replace("aims to reduce", "reduces")
        .replace("may collect", "collects")
    )
    result = validate_patches(
        original,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )
    assert not result.accepted
    assert result.findings


def test_document_preamble_exception_does_not_relax_a_different_sentences_real_goal() -> None:
    source = "This note aims to describe latency estimates. The service stores estimates so the same information can be accessed in one place."
    replacement = (
        "This note describes latency estimates. The service stores estimates in one place."
    )
    document = parse_markdown(source, title="Scope")
    block = document.blocks[0]
    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )
    assert not decision.accepted
    assert decision.findings[0].code == "force_language_changed"


@pytest.mark.parametrize("retain_intention", [True, False])
def test_cited_reference_allows_direct_note_prose_while_service_intention_stays_protected(
    *, retain_intention: bool
) -> None:
    source = (
        "This note is intended to bring together latency estimates from the Google SRE "
        "reference so the same information can be read in one place. The figures are rules "
        "of thumb, rather than current benchmarks. The calibration reading was measured "
        "by the team at 20 ℃ on 2026-11-11. The service is intended to reduce retry traffic."
    )
    replacement = (
        "Google SRE reference latency estimates are rules of thumb, rather than current "
        "benchmarks. The team measured the calibration reading at 20 ℃ on 2026-11-11. "
        + (
            "The service is intended to reduce retry traffic."
            if retain_intention
            else "The service reduces retry traffic."
        )
    )
    document = parse_markdown(source, title="Reference source")
    block = document.blocks[0]
    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert bool(decision.accepted) is retain_intention
    if not retain_intention:
        assert decision.findings[0].code == "force_language_changed"
