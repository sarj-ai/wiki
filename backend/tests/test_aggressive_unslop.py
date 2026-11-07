from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import override

import pytest

from app.document_transform.errors import ConcisionTargetUnmetError, ModelUnavailableError
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
from app.document_transform.unslop import (
    MAX_MODEL_EDITABLE_BLOCK_CHARS,
    concision_target,
    model_editable_blocks,
    scan_unslop_candidates,
)
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


class _DocumentBudgetEditor(PatchGenerator):
    def __init__(self, initial: dict[str, str], repaired: dict[str, str]) -> None:
        self.initial = initial
        self.repaired = repaired
        self.repair_calls = 0
        self.repair_unavailable = False
        self.feedback: tuple[TransformFinding, ...] = ()
        self.rejected: set[str] = set()
        self.reviewed: list[TextPatch] = []
        self.generated: list[DocumentIR] = []

    @property
    @override
    def model_ids(self) -> tuple[str, ...]:
        return ("test-document-editor",)

    @override
    async def generate(self, document: DocumentIR, *, phase: TransformPhase) -> list[TextPatch]:
        self.generated.append(document)
        assert phase is TransformPhase.UNSLOP
        return _budget_patches(document, self.initial)

    @override
    async def repair(
        self, document: DocumentIR, patches: list[TextPatch], findings: tuple[TransformFinding, ...]
    ) -> list[TextPatch]:
        self.repair_calls += 1
        self.feedback = findings
        assert all(patch.block_id in self.initial for patch in patches)
        if self.repair_unavailable:
            raise ModelUnavailableError
        return _budget_patches(document, self.repaired)

    @override
    async def review(self, document: DocumentIR, patches: list[TextPatch]) -> RewriteReview:
        sources = {block.id: block for block in document.blocks}
        assert all(patch.expected_sha256 == sources[patch.block_id].sha256 for patch in patches)
        self.reviewed.extend(patches)
        return RewriteReview(
            approved=frozenset(
                patch.block_id for patch in patches if patch.block_id not in self.rejected
            ),
            rejected=dict.fromkeys(self.rejected, "The proposal lost a source fact"),
        )


def _budget_patches(document: DocumentIR, replacements: dict[str, str]) -> list[TextPatch]:
    return [
        TextPatch(
            block_id=block.id, expected_sha256=block.sha256, replacement=replacements[block.id]
        )
        for block in document.blocks
        if block.id in replacements
    ]


@dataclass(frozen=True, slots=True)
class _ConcisionFixture:
    document: DocumentIR
    replacements: dict[str, str]


def _two_concision_blocks() -> _ConcisionFixture:
    dense = (
        "The sensor records temperature at night and stores each reading in the local log. "
        "The operator checks the log before scheduled maintenance and compares readings with "
        "the previous inspection. The device retains calibration notes beside the measurements "
        "and records the test location. The operator sends the log to the laboratory after "
        "nightly inspection, and the laboratory archives the received record."
    )
    sparse = (
        "The logger records temperature at night, stores the readings in its archive, "
        "and sends the complete log to the laboratory."
    )
    padding = " The account restates this description in extended and repetitive wording."
    document = parse_markdown(dense + padding * 4 + "\n\n" + sparse + padding * 8, title="Logs")
    blocks = [block for block in document.blocks if block.text]
    assert [len(block.text.split()) for block in blocks] == [100, 100]
    assert [len(text.split()) for text in (dense, sparse)] == [60, 20]
    return _ConcisionFixture(
        document=document, replacements={blocks[0].id: dense, blocks[1].id: sparse}
    )


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
    assert "maximum 23" in editor.feedback[0].detail
    assert [patch.replacement for patch in editor.reviewed] == ([] if unchanged else [initial]) + [
        CONCISION_FACT
    ]
    assert result.document.blocks[0].text == CONCISION_FACT
    assert result.applied_count == result.model_count == 1
    assert not result.findings
    assert document.blocks[0].text == CONCISION_SOURCE


@pytest.mark.parametrize("failure", ["unchanged", "verbose", "stale", "unknown", "semantic"])
async def test_failed_quality_repair_is_bounded_and_preserves_original(
    failure: str, concision_editor_factory: Callable[[str, str], _ConcisionEditor]
) -> None:
    replacement = {
        "unchanged": CONCISION_SOURCE,
        "verbose": CONCISION_SOURCE.replace(" expanded", "", 1),
        "semantic": CONCISION_FACT.replace("temperature", "pressure"),
    }.get(failure, CONCISION_FACT)
    editor = concision_editor_factory(CONCISION_SOURCE, replacement)
    editor.approve = failure != "semantic"
    editor.repair_sha256 = "0" * 64 if failure == "stale" else None
    editor.repair_block_id = "outside-current-source" if failure == "unknown" else None
    document = parse_markdown(CONCISION_SOURCE, title="Sensor")

    with pytest.raises(ConcisionTargetUnmetError) as failure_info:
        await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
            document, _request(content=CONCISION_SOURCE)
        )

    assert editor.repair_calls == 1
    assert document.blocks[0].text == CONCISION_SOURCE
    assert editor.phases == [TransformPhase.UNSLOP]
    assert failure_info.value.code == "document_transform_failed"
    assert "no transformed output" in failure_info.value.public_detail
    assert [finding.code for finding in editor.feedback] == ["concision_target_unmet"]


@pytest.mark.parametrize("stage", ["proposal", "review"])
async def test_unavailable_required_repair_preserves_the_source_and_retryable_error(
    stage: str, concision_editor_factory: Callable[[str, str], _ConcisionEditor]
) -> None:
    editor = concision_editor_factory(CONCISION_SOURCE, CONCISION_FACT)
    editor.unavailable = stage == "proposal"
    editor.review_unavailable = stage == "review"
    document = parse_markdown(CONCISION_SOURCE, title="Sensor")

    with pytest.raises(ModelUnavailableError) as failure_info:
        await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
            document, _request(content=CONCISION_SOURCE)
        )

    assert failure_info.value.code == "model_unavailable"
    assert editor.repair_calls == 1
    assert document.blocks[0].text == CONCISION_SOURCE
    assert editor.phases == [TransformPhase.UNSLOP]
    assert [finding.code for finding in editor.feedback] == ["concision_target_unmet"]


async def test_optional_repair_outage_keeps_an_already_concise_verified_result() -> None:
    short = "The bird may sing unseen beside the path near the stream."
    document = parse_markdown(CONCISION_SOURCE + "\n\n" + short, title="Sensor and bird")
    first, separator, last = document.blocks
    initial = {
        first.id: CONCISION_FACT,
        last.id: "The bird must sing unseen beside the path near the stream.",
    }
    editor = _DocumentBudgetEditor(initial, {})
    editor.repair_unavailable = True

    result = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        document, _request(content=CONCISION_SOURCE + "\n\n" + short)
    )

    assert concision_target(first) is not None
    assert concision_target(last) is None
    assert last in model_editable_blocks(document)
    assert separator.kind is BlockKind.BLANK
    assert not separator.text
    assert [block.text for block in result.document.blocks] == [CONCISION_FACT, "", short]
    assert first.text == CONCISION_SOURCE
    assert last.text == short
    assert result.model_count == 1
    assert editor.repair_calls == 1
    assert [finding.code for finding in editor.feedback] == ["force_language_changed"]
    assert [finding.code for finding in result.findings] == ["force_language_changed"]


@pytest.mark.parametrize(
    ("source_words", "proposed_words", "unmet"),
    [(39, 39, False), (40, 16, False), (40, 17, True), (41, 16, False), (41, 17, True)],
)
async def test_quality_word_boundary_is_enforced_without_truncation(
    source_words: int,
    proposed_words: int,
    concision_editor_factory: Callable[[str, str], _ConcisionEditor],
    *,
    unmet: bool,
) -> None:
    source = " ".join(["material"] * source_words)
    replacement = " ".join(["material"] * proposed_words)
    document = parse_markdown(source, title="Boundary")
    editor = concision_editor_factory(replacement, replacement)
    planner = TextTransformPlanner(generator=editor, allow_unslop_model=True)
    if unmet:
        with pytest.raises(ConcisionTargetUnmetError):
            await planner.transform(document, _request(content=source))
        assert editor.repair_calls == 1
    else:
        result = await planner.transform(document, _request(content=source))
        assert result.document.blocks[0].text == replacement
        assert result.model_count == int(replacement != source)
        assert not result.findings
        assert editor.repair_calls == 0
    assert document.blocks[0].text == source


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

    with pytest.raises(ConcisionTargetUnmetError):
        await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
            document, _request(content=source)
        )

    assert editor.repair_calls == 1
    assert document.blocks[0].text == source
    assert [finding.code for finding in editor.feedback] == ["concision_target_unmet"]


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


async def test_document_budget_allows_a_dense_block_to_use_another_blocks_savings() -> None:
    fixture = _two_concision_blocks()
    document, faithful = fixture.document, fixture.replacements
    editor = _DocumentBudgetEditor(faithful, {})

    result = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        document, _request()
    )

    assert [len(block.text.split()) for block in result.document.blocks if block.text] == [60, 20]
    assert result.document == document.replace_blocks(faithful)
    assert result.model_count == 2
    assert editor.repair_calls == 0
    assert {patch.block_id for patch in editor.reviewed} == faithful.keys()
    assert not result.findings


async def test_global_overage_repairs_already_reviewed_blocks_only_once() -> None:
    fixture = _two_concision_blocks()
    document, faithful = fixture.document, fixture.replacements
    second_id = next(reversed(faithful))
    initial = {
        **faithful,
        second_id: faithful[second_id]
        + " The account restates this description in extended and repetitive wording." * 2,
    }
    editor = _DocumentBudgetEditor(initial, faithful)

    result = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        document, _request()
    )

    assert editor.repair_calls == 1
    assert {finding.block_id for finding in editor.feedback} == faithful.keys()
    assert all(finding.code == "concision_target_unmet" for finding in editor.feedback)
    assert all(
        "source 200 words; proposal 100 words; maximum 80" in item.detail
        for item in editor.feedback
    )
    assert [patch.block_id for patch in editor.reviewed].count(second_id) == 2
    assert result.document == document.replace_blocks(faithful)
    assert not result.findings


@pytest.mark.parametrize("retained", ["unchanged", "semantic", "missing"])
async def test_original_retained_prose_cannot_disappear_from_the_global_total(
    retained: str,
) -> None:
    fixture = _two_concision_blocks()
    document, faithful = fixture.document, fixture.replacements
    first = document.blocks[0]
    initial = dict(faithful)
    repaired = {**faithful, first.id: first.text}
    if retained == "unchanged":
        initial[first.id] = first.text
    elif retained == "missing":
        initial = {
            block_id: replacement
            for block_id, replacement in initial.items()
            if block_id != first.id
        }
    editor = _DocumentBudgetEditor(initial, repaired)
    if retained == "semantic":
        editor.rejected.add(first.id)

    with pytest.raises(ConcisionTargetUnmetError):
        await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
            document, _request()
        )

    assert editor.repair_calls == 1
    assert any("proposal 120 words; maximum 80" in finding.detail for finding in editor.feedback)
    assert document.blocks[0].text == first.text


async def test_original_concision_denominator_survives_deterministic_prefix_deletion() -> None:
    source = "It is important to note that " + CONCISION_SOURCE
    replacement = (
        CONCISION_FACT
        + " The explanation repeats the description with extended redundant wording." * 2
    )
    document = parse_markdown(source, title="Sensor")
    block = document.blocks[0]
    editor = _DocumentBudgetEditor({block.id: replacement}, {})

    result = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        document, _request(content=source)
    )

    assert len(source.split()) == 65
    assert editor.generated[0].blocks[0].text == CONCISION_SOURCE
    assert len(replacement.split()) == 25
    assert result.document.blocks[0].text == replacement
    assert result.deterministic_count == result.model_count == 1
    assert editor.repair_calls == 0
    assert not result.findings


@pytest.mark.parametrize("proposed_words", [16, 17], ids=["at-cap", "over-cap"])
async def test_frozen_eligibility_and_exact_suffix_survive_shortening(
    proposed_words: int,
    concision_editor_factory: Callable[[str, str], _ConcisionEditor],
) -> None:
    suffix = " [41] [42]"
    source = " ".join(["reading"] * 40)
    original = parse_markdown(source + suffix, title="Native citations")
    block = replace(original.blocks[0], protected_suffix=suffix)
    document = replace(original, blocks=(block,))
    replacement = " ".join(["reading"] * proposed_words) + suffix
    editor = concision_editor_factory(replacement, replacement)
    planner = TextTransformPlanner(generator=editor, allow_unslop_model=True)

    assert concision_target(block) is not None
    if proposed_words == 17:
        with pytest.raises(ConcisionTargetUnmetError):
            await planner.transform(document, _request(content=block.text))
        assert editor.repair_calls == 1
        assert "source 40 words; proposal 17 words; maximum 16" in editor.feedback[0].detail
    else:
        result = await planner.transform(document, _request(content=block.text))
        assert result.document.blocks[0].text == replacement
        assert editor.repair_calls == 0
        assert not result.findings
    assert document.blocks[0].text.endswith(suffix)


@pytest.mark.parametrize(
    "excluded",
    ["short", "frozen", "oversized", "heading", "quote", "code", "table"],
)
async def test_previously_excluded_blocks_do_not_gain_a_document_quota(excluded: str) -> None:
    document = parse_markdown(CONCISION_SOURCE, title="Eligibility")
    long_block = document.blocks[0]
    other = replace(long_block, id="outside-budget")
    match excluded:
        case "short":
            other = replace(other, text=CONCISE)
        case "frozen":
            other = replace(other, editable=False)
        case "oversized":
            other = replace(other, text="reading " * (MAX_MODEL_EDITABLE_BLOCK_CHARS // 8 + 1))
        case _:
            other = replace(other, kind=BlockKind(excluded))
    document = replace(document, blocks=(long_block, other))
    editor = _DocumentBudgetEditor({long_block.id: CONCISION_FACT}, {})

    result = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        document, _request()
    )

    assert concision_target(other) is None
    assert result.document.blocks[0].text == CONCISION_FACT
    assert result.document.blocks[1] == other
    assert editor.repair_calls == 0


async def test_originally_short_slides_remain_a_noop_without_a_global_target() -> None:
    document = replace(parse_markdown(CONCISE, title="Bird"), source_format="google_slides")
    block = document.blocks[0]
    editor = _DocumentBudgetEditor({block.id: block.text}, {})

    result = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        document, _request()
    )

    assert concision_target(block) is None
    assert result.document == document
    assert result.applied_count == 0
    assert editor.repair_calls == 0
    assert not result.findings


async def test_failed_document_target_stops_before_the_format_phase(
    concision_editor_factory: Callable[[str, str], _ConcisionEditor],
) -> None:
    document = parse_markdown(CONCISION_SOURCE, title="Unchanged")
    editor = concision_editor_factory(CONCISION_SOURCE, CONCISION_SOURCE)

    with pytest.raises(ConcisionTargetUnmetError):
        await TextTransformPlanner(
            generator=editor, allow_unslop_model=True, allow_format_text_edits=True
        ).transform(document, _request(TransformMode.BOTH, content=CONCISION_SOURCE))

    assert editor.phases == [TransformPhase.UNSLOP]
    assert editor.repair_calls == 1
    assert document.blocks[0].text == CONCISION_SOURCE
