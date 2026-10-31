from __future__ import annotations

from collections.abc import Callable
from typing import override

import pytest

from app.document_transform.errors import ModelUnavailableError
from app.document_transform.ir import DocumentIR, parse_markdown
from app.document_transform.models import PatchGenerator, RewriteReview
from app.document_transform.planner import TextTransformPlanner
from app.document_transform.safety import validate_patches
from app.document_transform.schemas import (
    DocumentTransformRequest,
    MarkdownSource,
    TextPatch,
    TransformFinding,
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


class _RepairEditor(_Editor):
    def __init__(self, replacement: str = CONCISE, *, approve: bool = True) -> None:
        super().__init__(approve=approve)
        self.replacement = replacement
        self.repair_block_id: str | None = None
        self.repair_sha256: str | None = None
        self.repair_calls = 0
        self.unavailable = False

    @override
    async def generate(self, document: DocumentIR, *, phase: TransformPhase) -> list[TextPatch]:
        proposals = await super().generate(document, phase=phase)
        return [
            patch.model_copy(update={"replacement": patch.replacement.replace(" can ", " will ")})
            for patch in proposals
        ]

    @override
    async def repair(
        self,
        document: DocumentIR,
        patches: list[TextPatch],
        findings: tuple[TransformFinding, ...],
    ) -> list[TextPatch]:
        self.repair_calls += 1
        assert len(findings) == 1
        assert any(patch.block_id == findings[0].block_id for patch in patches)
        assert findings[0].code == "force_language_changed"
        if self.unavailable:
            raise ModelUnavailableError
        block = document.blocks[0]
        return [
            TextPatch(
                block_id=self.repair_block_id or block.id,
                expected_sha256=self.repair_sha256 or block.sha256,
                replacement=self.replacement,
            )
        ]


class _MultiBlockRepairEditor(_RepairEditor):
    @override
    async def generate(self, document: DocumentIR, *, phase: TransformPhase) -> list[TextPatch]:
        self.phases.append(phase)
        return [
            TextPatch(
                block_id=block.id,
                expected_sha256=block.sha256,
                replacement=CONCISE.replace(" can ", " will ") if index == 0 else CONCISE,
            )
            for index, block in enumerate(document.blocks)
            if block.text.strip()
        ]


class _SemanticRepairEditor(_Editor):
    def __init__(self) -> None:
        super().__init__()
        self.repair_calls = 0
        self.final_approve = True
        self.feedback: tuple[TransformFinding, ...] = ()

    @override
    async def generate(self, document: DocumentIR, *, phase: TransformPhase) -> list[TextPatch]:
        proposals = await super().generate(document, phase=phase)
        return [
            patch.model_copy(update={"replacement": patch.replacement.replace("unseen ", "")})
            for patch in proposals
        ]

    @override
    async def review(self, document: DocumentIR, patches: list[TextPatch]) -> RewriteReview:
        assert all(patch.expected_sha256 == document.blocks[0].sha256 for patch in patches)
        self.reviewed.extend(patches)
        if self.repair_calls and self.final_approve:
            return RewriteReview(
                approved=frozenset(patch.block_id for patch in patches), rejected={}
            )
        return RewriteReview(
            approved=frozenset(),
            rejected={
                patch.block_id: "The proposed rewrite omits the bird remaining hidden from observers."
                for patch in patches
            },
        )

    @override
    async def repair(
        self,
        document: DocumentIR,
        patches: list[TextPatch],
        findings: tuple[TransformFinding, ...],
    ) -> list[TextPatch]:
        self.repair_calls += 1
        self.feedback = findings
        block = document.blocks[0]
        assert patches[0].block_id == block.id
        return [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=CONCISE)]


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


@pytest.fixture
def repair_editor_factory() -> Callable[[], _RepairEditor]:
    return _RepairEditor


@pytest.fixture
def multi_block_editor() -> _MultiBlockRepairEditor:
    return _MultiBlockRepairEditor()


@pytest.fixture
def semantic_editor_factory() -> Callable[[], _SemanticRepairEditor]:
    return _SemanticRepairEditor


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


@pytest.mark.parametrize("approve", [False, True])
async def test_repaired_prose_still_requires_independent_meaning_review(
    repair_editor_factory: Callable[[], _RepairEditor], *, approve: bool
) -> None:
    editor = repair_editor_factory()
    editor.approve = approve
    outcome = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        parse_markdown(SOURCE, title="Bird"), _request()
    )
    assert editor.repair_calls == 1
    assert editor.reviewed[0].replacement == CONCISE
    assert outcome.document.blocks[0].text == (CONCISE if approve else SOURCE)
    assert outcome.model_count == int(approve)
    assert [finding.code for finding in outcome.findings] == (
        [] if approve else ["semantic_review_rejected"]
    )


@pytest.mark.parametrize("failure", ["unsafe", "unknown", "stale", "unavailable"])
async def test_failed_repair_is_bounded_and_never_changes_the_source(
    failure: str, repair_editor_factory: Callable[[], _RepairEditor]
) -> None:
    editor = repair_editor_factory()
    editor.replacement = CONCISE.replace(" can ", " will ") if failure == "unsafe" else CONCISE
    editor.repair_block_id = "outside-the-rejected-source" if failure == "unknown" else None
    editor.repair_sha256 = "0" * 64 if failure == "stale" else None
    editor.unavailable = failure == "unavailable"
    outcome = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        parse_markdown(SOURCE, title="Bird"), _request()
    )
    assert editor.repair_calls == 1
    assert not editor.reviewed
    assert outcome.document.blocks[0].text == SOURCE
    assert outcome.model_count == 0
    codes = [finding.code for finding in outcome.findings]
    assert codes.count("force_language_changed") == 1
    if failure in {"unknown", "stale"}:
        assert ("unknown_block" if failure == "unknown" else "stale_block") in codes


@pytest.mark.parametrize("unavailable", [False, True])
async def test_repair_cannot_overwrite_an_already_accepted_edit(
    multi_block_editor: _MultiBlockRepairEditor, *, unavailable: bool
) -> None:
    document = parse_markdown(SOURCE + "\n\n" + SOURCE, title="Bird")
    multi_block_editor.repair_block_id = document.blocks[-1].id
    multi_block_editor.replacement = "The common nightingale can sing hidden in dense scrub."
    multi_block_editor.unavailable = unavailable
    outcome = await TextTransformPlanner(
        generator=multi_block_editor, allow_unslop_model=True
    ).transform(document, _request())
    assert [block.text for block in outcome.document.blocks if block.text.strip()] == [
        SOURCE,
        CONCISE,
    ]
    assert multi_block_editor.repair_calls == 1
    assert [patch.block_id for patch in multi_block_editor.reviewed] == [document.blocks[-1].id]
    assert outcome.model_count == 1


@pytest.mark.parametrize("final_approve", [False, True])
async def test_review_critique_guides_one_repair_and_the_repair_is_reviewed_again(
    semantic_editor_factory: Callable[[], _SemanticRepairEditor], *, final_approve: bool
) -> None:
    editor = semantic_editor_factory()
    editor.final_approve = final_approve
    outcome = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        parse_markdown(SOURCE, title="Bird"), _request(TransformMode.BOTH)
    )
    assert editor.repair_calls == 1
    assert editor.feedback[0].code == "semantic_review_rejected"
    assert "remaining hidden" in editor.feedback[0].detail
    assert [patch.replacement for patch in editor.reviewed] == [
        CONCISE.replace("unseen ", ""),
        CONCISE,
    ]
    assert outcome.document.blocks[0].text == (CONCISE if final_approve else SOURCE)
    assert outcome.model_count == int(final_approve)
    assert [finding.code for finding in outcome.findings] == (
        [] if final_approve else ["semantic_review_rejected"]
    )


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
            "The bird could migrate across Europe.",
            "The bird can migrate across Europe.",
            "force_language_changed",
        ),
        (
            "The bird would migrate across Europe.",
            "The bird will migrate across Europe.",
            "force_language_changed",
        ),
        (
            "The study measured 533 songs from each of 19 males.",
            "The study measured 533 songs across 19 males.",
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
