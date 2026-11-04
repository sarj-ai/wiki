from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import override

import pytest

from app.document_transform.errors import ModelUnavailableError
from app.document_transform.ir import BlockKind, DocumentIR, parse_markdown
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
CONCISION_FACT = "The sensor may record temperature at night."
CONCISION_PADDING = " The explanation describes the discussion in expanded wording before returning to the description."
CONCISION_SOURCE = CONCISION_FACT + CONCISION_PADDING * 4


class _Editor(PatchGenerator):
    def __init__(self, *, approve: bool = True) -> None:
        self.approve = approve
        self.phases: list[TransformPhase] = []
        self.reviewed: list[TextPatch] = []
        self.initial_review_unavailable = False

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
        if self.initial_review_unavailable:
            raise ModelUnavailableError
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
        self.review_unavailable = False

    @override
    async def verify(self, document: DocumentIR, patches: list[TextPatch]) -> frozenset[str]:
        if self.review_unavailable and self.repair_calls:
            raise ModelUnavailableError
        return await super().verify(document, patches)

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


class _ConcisionEditor(_RepairEditor):
    def __init__(self, initial: str, replacement: str = CONCISION_FACT) -> None:
        super().__init__(replacement=replacement)
        self.initial = initial
        self.feedback: tuple[TransformFinding, ...] = ()

    @override
    async def generate(self, document: DocumentIR, *, phase: TransformPhase) -> list[TextPatch]:
        self.phases.append(phase)
        block = document.blocks[0]
        return [
            TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=self.initial)
        ]

    @override
    async def repair(
        self, document: DocumentIR, patches: list[TextPatch], findings: tuple[TransformFinding, ...]
    ) -> list[TextPatch]:
        self.repair_calls += 1
        self.feedback = findings
        if self.unavailable:
            raise ModelUnavailableError
        block = document.blocks[0]
        assert patches[0].expected_sha256 == block.sha256
        return [
            TextPatch(
                block_id=self.repair_block_id or block.id,
                expected_sha256=self.repair_sha256 or block.sha256,
                replacement=self.replacement,
            )
        ]


def _request(
    mode: TransformMode = TransformMode.UNSLOP, *, content: str = SOURCE
) -> DocumentTransformRequest:
    return DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown", content=content, name="Article", destination_folder_id=FileId("folder")
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
def concision_editor_factory() -> Callable[[str, str], _ConcisionEditor]:
    return _ConcisionEditor


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


async def test_initial_meaning_review_outage_fails_closed(editor: _Editor) -> None:
    document = parse_markdown(SOURCE, title="Bird")
    editor.initial_review_unavailable = True
    with pytest.raises(ModelUnavailableError):
        await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
            document, _request()
        )
    assert document.blocks[0].text == SOURCE
    assert editor.reviewed == []


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


@pytest.mark.parametrize("with_approved_block", [False, True])
async def test_optional_repair_review_outage_retains_verified_edits_and_original_rejected_prose(
    multi_block_editor: _MultiBlockRepairEditor, *, with_approved_block: bool
) -> None:
    text = SOURCE + "\n\n" + SOURCE if with_approved_block else SOURCE
    document = parse_markdown(text, title="Bird")
    multi_block_editor.review_unavailable = True
    outcome = await TextTransformPlanner(
        generator=multi_block_editor, allow_unslop_model=True
    ).transform(document, _request())
    expected = [SOURCE, CONCISE] if with_approved_block else [SOURCE]
    assert [block.text for block in outcome.document.blocks if block.text.strip()] == expected
    assert outcome.applied_count == int(with_approved_block)
    assert multi_block_editor.repair_calls == 1
    assert len(multi_block_editor.reviewed) == int(with_approved_block)
    assert [finding.code for finding in outcome.findings] == ["force_language_changed"]


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
            "This study recorded 19 birds at the first site and 19 birds at the second site.",
            "This study recorded 19 birds across the first and second sites.",
            "protected_content_changed",
        ),
        (
            "Researchers recorded 533 songs from each of 19 males during the survey.",
            "During the survey, researchers recorded 533 songs from 19 males.",
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


@pytest.mark.parametrize("aggressive", [False, True])
def test_only_reviewed_aggressive_rewrites_can_reorder_retained_literals(
    *, aggressive: bool
) -> None:
    source = "During April, the survey can include 19 males and may record 533 songs."
    replacement = "During April, the survey may record 533 songs and can include 19 males."
    document = parse_markdown(source, title="Survey")
    block = document.blocks[0]
    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=aggressive,
    )
    assert decision.accepted == ({block.id: replacement} if aggressive else {})
    assert [finding.code for finding in decision.findings] == (
        [] if aggressive else ["protected_content_changed"]
    )


@pytest.mark.parametrize("unchanged", [False, True])
async def test_verbose_or_unchanged_prose_enters_one_quality_repair(
    concision_editor_factory: Callable[[str, str], _ConcisionEditor], *, unchanged: bool
) -> None:
    initial = CONCISION_SOURCE if unchanged else CONCISION_SOURCE.replace(" expanded", "", 1)
    editor = concision_editor_factory(initial, CONCISION_FACT)
    document = parse_markdown(CONCISION_SOURCE, title="Sensor")

    result = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        document, _request(content=CONCISION_SOURCE)
    )

    assert editor.repair_calls == 1
    assert [finding.code for finding in editor.feedback] == ["concision_target_unmet"]
    assert "source 59 words" in editor.feedback[0].detail
    assert "maximum 24" in editor.feedback[0].detail
    assert [patch.replacement for patch in editor.reviewed] == [CONCISION_FACT]
    assert result.document.blocks[0].text == CONCISION_FACT
    assert result.applied_count == result.model_count == 1
    assert not result.findings
    assert document.blocks[0].text == CONCISION_SOURCE


@pytest.mark.parametrize(
    "failure", ["unchanged", "verbose", "unavailable", "stale", "unknown", "semantic"]
)
async def test_failed_quality_repair_is_bounded_and_preserves_original(
    failure: str, concision_editor_factory: Callable[[str, str], _ConcisionEditor]
) -> None:
    replacement = {
        "unchanged": CONCISION_SOURCE,
        "verbose": CONCISION_SOURCE.replace(" expanded", "", 1),
        "semantic": CONCISION_FACT.replace("temperature", "pressure"),
    }.get(failure, CONCISION_FACT)
    editor = concision_editor_factory(CONCISION_SOURCE, replacement)
    editor.unavailable = failure == "unavailable"
    editor.approve = failure != "semantic"
    editor.repair_sha256 = "0" * 64 if failure == "stale" else None
    editor.repair_block_id = "outside-current-source" if failure == "unknown" else None
    document = parse_markdown(CONCISION_SOURCE, title="Sensor")

    result = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        document, _request(content=CONCISION_SOURCE)
    )

    assert editor.repair_calls == 1
    assert result.document == document
    assert result.applied_count == result.model_count == 0
    assert result.llm_invoked
    assert result.findings
    assert (
        "concision_target_unmet" in {finding.code for finding in result.findings}
        or failure == "semantic"
    )


@pytest.mark.parametrize(
    ("source_words", "proposed_words", "unmet"),
    [(39, 39, False), (40, 16, False), (40, 17, True), (41, 17, False), (41, 18, True)],
)
def test_quality_word_boundary_is_enforced_without_truncation(
    source_words: int, proposed_words: int, *, unmet: bool
) -> None:
    source = " ".join(["material"] * source_words)
    replacement = " ".join(["material"] * proposed_words)
    document = parse_markdown(source, title="Boundary")
    block = document.blocks[0]

    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert [finding.code for finding in decision.findings] == (
        ["concision_target_unmet"] if unmet else []
    )
    assert decision.accepted == ({} if unmet or replacement == source else {block.id: replacement})
    assert block.text == source


def test_quality_budget_excludes_the_exact_protected_suffix() -> None:
    suffix = " [41]"
    original = parse_markdown(" ".join(["material"] * 40) + suffix, title="Citation")
    block = replace(original.blocks[0], protected_suffix=suffix)
    document = replace(original, blocks=(block,))
    replacement = " ".join(["material"] * 16) + suffix

    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert decision.accepted == {block.id: replacement}
    assert not decision.findings


@pytest.mark.parametrize(
    ("replacement", "code"),
    [
        (
            'The sensor must record 15 samples and retain "calibration" notes.',
            "force_language_changed",
        ),
        (
            'The sensor may record 14 samples and retain "calibration" notes.',
            "protected_content_changed",
        ),
        (
            'The sensor may record 15 samples and retain "adjustment" notes.',
            "quoted_content_changed",
        ),
    ],
)
def test_short_word_target_never_overrides_hard_guards(replacement: str, code: str) -> None:
    source = (
        'The sensor may record 15 samples and retain "calibration" notes.' + CONCISION_PADDING * 4
    )
    document = parse_markdown(source, title="Calibration")
    block = document.blocks[0]

    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == [code]


async def test_dense_protected_facts_remain_when_the_target_cannot_be_met_safely(
    concision_editor_factory: Callable[[str, str], _ConcisionEditor],
) -> None:
    source = (
        "The record may retain these measurements: " + ", ".join(map(str, range(101, 141))) + "."
    )
    document = parse_markdown(source, title="Measurements")
    editor = concision_editor_factory(source, source)

    result = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        document, _request(content=source)
    )

    assert editor.repair_calls == 1
    assert result.document == document
    assert not result.applied_count
    assert [finding.code for finding in result.findings] == ["concision_target_unmet"]


@pytest.mark.parametrize(
    "kind", [BlockKind.HEADING, BlockKind.QUOTE, BlockKind.CODE, BlockKind.TABLE]
)
def test_nonprose_blocks_keep_their_existing_safety_boundary(kind: BlockKind) -> None:
    original = parse_markdown(CONCISION_SOURCE, title="Scope")
    block = replace(original.blocks[0], kind=kind)
    document = replace(original, blocks=(block,))

    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=block.text)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    expected = [] if kind is BlockKind.HEADING else ["protected_block"]
    assert [finding.code for finding in decision.findings] == expected


def test_formatting_has_no_concision_target_for_an_unchanged_long_block() -> None:
    document = parse_markdown(CONCISION_SOURCE, title="Format")
    block = document.blocks[0]

    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=block.text)],
        phase=TransformPhase.FORMAT,
        aggressive_unslop=True,
    )

    assert not decision.accepted
    assert not decision.findings
