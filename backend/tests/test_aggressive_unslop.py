from __future__ import annotations

from typing import override

import pytest

from app.document_transform.ir import DocumentIR, parse_markdown
from app.document_transform.models import PatchGenerator
from app.document_transform.planner import TextTransformPlanner
from app.document_transform.safety import validate_patches
from app.document_transform.schemas import (
    DocumentTransformRequest,
    MarkdownSource,
    TextPatch,
    TransformMode,
    TransformPhase,
)
from app.document_transform.unslop import scan_unslop_candidates
from app.identifiers import FileId


SOURCE = (
    "In an attempt to provide a fuller picture, the common nightingale can sing from dense "
    "scrub while remaining hidden from observers. This is an important point to keep in mind "
    "when thinking about its habitat."
)
CONCISE = "The common nightingale can sing unseen from dense scrub."


class _Editor(PatchGenerator):
    def __init__(self, *, approve: bool = True) -> None:
        self.approve = approve
        self.phases: list[TransformPhase] = []
        self.reviewed: list[TextPatch] = []

    @property
    @override
    def model_ids(self) -> tuple[str, ...]:
        return ("test-editor",)

    @override
    async def generate(self, document: DocumentIR, *, phase: TransformPhase) -> list[TextPatch]:
        self.phases.append(phase)
        block = document.blocks[0]
        return [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=CONCISE)]

    @override
    async def verify(self, document: DocumentIR, patches: list[TextPatch]) -> frozenset[str]:
        assert all(patch.expected_sha256 == document.blocks[0].sha256 for patch in patches)
        self.reviewed.extend(patches)
        return frozenset(patch.block_id for patch in patches) if self.approve else frozenset()


def _request(mode: TransformMode = TransformMode.UNSLOP) -> DocumentTransformRequest:
    return DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown", content=SOURCE, name="Article", destination_folder_id=FileId("folder")
        ),
        mode=mode,
    )


@pytest.fixture
def editor() -> _Editor:
    return _Editor()


async def test_aggressive_rewrite_reviews_prose_without_a_scanner_match(editor: _Editor) -> None:
    original = parse_markdown(SOURCE, title="Nightingale")
    assert not scan_unslop_candidates(original)
    result = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        original, _request()
    )
    assert result.document.blocks[0].text == CONCISE
    assert len(CONCISE.split()) < len(SOURCE.split()) / 2
    assert result.model_count == result.applied_count == 1
    assert result.llm_invoked
    assert editor.phases == [TransformPhase.UNSLOP]
    assert editor.reviewed[0].expected_sha256 == original.blocks[0].sha256
    assert original.blocks[0].text == SOURCE


async def test_semantic_review_rejection_keeps_source_and_requires_review(editor: _Editor) -> None:
    original = parse_markdown(SOURCE, title="Nightingale")
    editor.approve = False
    result = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        original, _request()
    )
    assert result.document == original
    assert result.applied_count == 0
    assert [finding.code for finding in result.findings] == ["semantic_review_rejected"]


async def test_both_runs_rewriting_without_allowing_a_format_wording_pass(editor: _Editor) -> None:
    result = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        parse_markdown(SOURCE, title="Nightingale"), _request(TransformMode.BOTH)
    )
    assert result.document.blocks[0].text == CONCISE
    assert editor.phases == [TransformPhase.UNSLOP]


@pytest.mark.parametrize(
    ("source", "replacement", "code"),
    [
        (
            "The team counted 19 males in Europe.",
            "The team counted 20 males in Europe.",
            "protected_content_changed",
        ),
        (
            "The bird may migrate across Europe.",
            "The bird migrates across Europe.",
            "force_language_changed",
        ),
        (
            "The team studied birds in Europe.",
            "The team studied birds in Asia.",
            "proper_noun_changed",
        ),
        (
            'The observer wrote "quiet" in the record.',
            'The observer wrote "loud" in the record.',
            "quoted_content_changed",
        ),
        (
            "The bird sings in scrub.",
            "The bird sings in scrub throughout the entire day.",
            "concision_expanded",
        ),
        ("The bird sings in scrub.", "The bird sings\nin scrub.", "paragraph_structure_changed"),
    ],
)
def test_aggressive_rewrites_keep_hard_content_guards(
    source: str, replacement: str, code: str
) -> None:
    document = parse_markdown(source, title="Facts")
    block = document.blocks[0]
    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )
    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == [code]
