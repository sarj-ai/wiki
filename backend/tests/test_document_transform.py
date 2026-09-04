from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, NamedTuple, override
import uuid

import pytest

from app.document_transform import google_gateway as google_gateway_module
from app.document_transform.brand import SarjBrandProfile, build_copy_mutations
from app.document_transform.errors import (
    CheckpointTooLargeError,
    IncompatiblePendingOutputError,
    SourceTooLargeError,
    UnsupportedSourceError,
)
from app.document_transform.executor import DocumentTransformExecutor
from app.document_transform.gateway import (
    DocumentGateway,
    InPlaceInspection,
    InPlaceStageResult,
    OutputDraft,
    OutputVerification,
    SourceSnapshot,
    TabHeader,
)
from app.document_transform.google_gateway import GoogleDocumentGateway, verify_brand_application
from app.document_transform.ir import (
    BlockKind,
    DocumentIR,
    SourceRange,
    TextBlock,
    parse_google_document,
    parse_markdown,
    rewrite_markdown,
    utf16_length,
)
from app.document_transform.models import PatchGenerator
from app.document_transform.safety import validate_patches
from app.document_transform.schemas import (
    DocumentOutput,
    DocumentTransformRequest,
    DriveFileSource,
    InPlaceCheckpoint,
    MarkdownSource,
    OutputStrategy,
    TextPatch,
    TransformMode,
    TransformPhase,
)
from app.document_transform.unslop import scan_unslop_candidates
from app.identifiers import DocsRevisionId, DocsSegmentId, DocsTabId, FileId, TaskId
from app.tasks import service as task_service_module


class _Generator(PatchGenerator):
    def __init__(self, replacements: dict[TransformPhase, str] | None = None) -> None:
        self.replacements = replacements or {}
        self.calls: list[TransformPhase] = []

    @property
    @override
    def model_ids(self) -> tuple[str, ...]:
        return ("gemini-3.1-flash-lite", "gemini-3.8-flash")

    @override
    async def generate(self, document: DocumentIR, *, phase: TransformPhase) -> list[TextPatch]:
        self.calls.append(phase)
        replacement = self.replacements.get(phase)
        if replacement is None:
            return []
        block = next(block for block in document.blocks if block.editable)
        return [
            TextPatch(
                block_id=block.id,
                expected_sha256=block.sha256,
                replacement=replacement,
                rationale="focused edit",
            )
        ]


class _PatchFixture(NamedTuple):
    document: DocumentIR
    patch: TextPatch


def _unslop_patch(source: str, replacement: str) -> _PatchFixture:
    document = parse_markdown(source, title="Safety")
    block = next(block for block in document.blocks if block.editable)
    return _PatchFixture(
        document=document,
        patch=TextPatch(
            block_id=block.id,
            expected_sha256=block.sha256,
            replacement=replacement,
            rationale="adversarial test",
        ),
    )


def test_unslop_rejects_changed_names_and_factual_predicate() -> None:
    document, patch = _unslop_patch(
        "Moreover, Alice approved the acquisition by Northstar.",
        "Moreover, Bob rejected the acquisition by Northstar.",
    )

    decision = validate_patches(document, [patch], phase=TransformPhase.UNSLOP)

    assert decision.accepted == {}
    assert decision.findings[0].code in {
        "proper_noun_changed",
        "noncandidate_content_changed",
    }


def test_unslop_rejects_edit_to_sentence_without_candidate() -> None:
    document, patch = _unslop_patch(
        "Moreover, we reviewed the plan. The board approved option A.",
        "We reviewed the plan. The board rejected option A.",
    )

    decision = validate_patches(document, [patch], phase=TransformPhase.UNSLOP)

    assert decision.accepted == {}
    assert decision.findings[0].code == "sentence_locality_changed"


@pytest.mark.parametrize(
    ("source", "replacement"),
    [
        ("Moreover, we reviewed the plan.", "We reviewed the plan."),
        (
            "Here's the thing: we're reviewing the user's plan.",
            "We're reviewing the user's plan.",
        ),
    ],
    ids=["transition", "contractions"],
)
def test_unslop_accepts_candidate_only_deletion(source: str, replacement: str) -> None:
    document, patch = _unslop_patch(source, replacement)

    decision = validate_patches(document, [patch], phase=TransformPhase.UNSLOP)

    assert decision.accepted == {"b0": replacement}


@pytest.mark.parametrize(
    ("source", "replacement"),
    [
        ("Moreover, we reviewed the plan.", "We we reviewed the plan."),
        ("Moreover, Alice approved the plan.", "Alice Alice approved the plan."),
        ("Moreover, revenue was $47.3M.", "Revenue was 47.3M."),
        ("Moreover, adoption reached 23%.", "Adoption reached 23."),
    ],
)
def test_unslop_rejects_insertions_duplication_and_numeric_symbol_loss(
    source: str,
    replacement: str,
) -> None:
    document, patch = _unslop_patch(source, replacement)

    decision = validate_patches(document, [patch], phase=TransformPhase.UNSLOP)

    assert decision.accepted == {}


@pytest.mark.parametrize("name", ["eBay", "iPhone", "macOS", "npm"])
def test_unslop_never_recapitalizes_brand_or_identifier(name: str) -> None:
    document, patch = _unslop_patch(
        f"Moreover, {name} approved the plan.",
        f"{name[0].upper() + name[1:]} approved the plan.",
    )

    decision = validate_patches(document, [patch], phase=TransformPhase.UNSLOP)

    assert decision.accepted == {}


def test_unslop_protects_markdown_blockquotes() -> None:
    document = parse_markdown(
        "> Additionally, this quoted example must stay exact.",
        title="Quoted source",
    )
    block = next(block for block in document.blocks if block.text)
    patch = TextPatch(
        block_id=block.id,
        expected_sha256=block.sha256,
        replacement="This quoted example must stay exact.",
    )

    decision = validate_patches(document, [patch], phase=TransformPhase.UNSLOP)

    assert decision.accepted == {}
    assert decision.findings[0].code == "protected_block"


@pytest.mark.parametrize(
    "source",
    [
        ">Additionally, preserve this quotation.",
        "~~~text\nAdditionally, preserve this sample.\n~~~",
        "    Additionally, preserve this indented code.",
    ],
)
def test_markdown_structural_regions_are_not_unslop_candidates(source: str) -> None:
    document = parse_markdown(source, title="Protected examples")

    assert scan_unslop_candidates(document) == ()


def test_unslop_scanner_masks_quoted_examples_and_protects_domain_terms() -> None:
    quoted = parse_markdown(
        'The guide says "It is important to note this bad example."',
        title="Guide",
    )
    technical = parse_markdown(
        "The robust hash verification rejected the stale block.",
        title="Design",
    )

    assert scan_unslop_candidates(quoted) == ()
    assert scan_unslop_candidates(technical) == ()


def test_unslop_scanner_masks_single_quoted_examples_but_not_contractions() -> None:
    quoted = parse_markdown(
        "The guide labels 'It is important to note that' as filler.",
        title="Guide",
    )
    contraction = parse_markdown("Here's the thing: we're ready.", title="Draft")

    assert scan_unslop_candidates(quoted) == ()
    assert {candidate.code for candidate in scan_unslop_candidates(contraction)} == {
        "throat_clearing"
    }


def test_literal_and_legal_uses_are_not_candidates() -> None:
    document = parse_markdown(
        "The team will delve into the mountain. The guard stands as the anthem plays. "
        "Additionally, the agreement remains subject to Section 4.",
        title="Literal and legal examples",
    )

    assert scan_unslop_candidates(document) == ()


@pytest.mark.parametrize(
    "source",
    [
        "Whether it is worth noting that taxes rose is disputed.",
        "She asked whether it is important to note that the deadline moved.",
        "The phrase here's the thing: is common in speech.",
    ],
)
def test_embedded_meta_phrases_are_never_unslop_candidates(source: str) -> None:
    assert scan_unslop_candidates(parse_markdown(source, title="Quoted claim")) == ()


@pytest.mark.parametrize(
    "source",
    [
        "```text\nIt is important to note this example.\n```not-a-close\n```",
        "---\ntitle: It is important to note this value\n---\nApproved prose.",
        "Label | Detail\n--- | ---\nA | It is important to note this value",
    ],
    ids=["fence", "frontmatter", "table-without-outer-pipes"],
)
def test_markdown_metadata_and_structures_are_not_unslop_candidates(source: str) -> None:
    assert scan_unslop_candidates(parse_markdown(source, title="Protected")) == ()


def test_raw_html_markdown_is_rejected_before_transform() -> None:
    with pytest.raises(UnsupportedSourceError):
        parse_markdown("<!-- It is important to note this -->", title="Raw HTML")


def test_markdown_block_count_is_bounded_before_model_or_drive_work() -> None:
    source = "\n".join(f"Paragraph {index}" for index in range(751))

    with pytest.raises(SourceTooLargeError):
        parse_markdown(source, title="Too many blocks")


def test_native_google_doc_block_count_is_bounded_before_output_creation() -> None:
    content = [
        {
            "startIndex": index * 2 + 1,
            "endIndex": index * 2 + 3,
            "paragraph": {"elements": [{"textRun": {"content": "x\n"}}]},
        }
        for index in range(751)
    ]
    document = {
        "revisionId": "revision-1",
        "tabs": [
            {
                "tabProperties": {"tabId": "tab-a"},
                "documentTab": {"body": {"content": content}},
                "childTabs": [],
            }
        ],
    }

    with pytest.raises(SourceTooLargeError):
        parse_google_document(document, title="Too many blocks")


async def test_native_mutation_budget_is_checked_before_copy_creation() -> None:
    content: list[dict[str, Any]] = []
    cursor = 1
    paragraph = "Moreover, we approved.\n"
    for _ in range(750):
        end = cursor + utf16_length(paragraph)
        content.append(
            {
                "startIndex": cursor,
                "endIndex": end,
                "paragraph": {"elements": [{"textRun": {"content": paragraph}}]},
            }
        )
        cursor = end
    document = {
        "revisionId": "source-revision-1",
        "tabs": [
            {
                "tabProperties": {"tabId": "tab-a"},
                "documentTab": {"body": {"content": content}},
                "childTabs": [],
            }
        ],
    }
    gateway = _Gateway(
        SourceSnapshot(
            title="Large review",
            parent_folder_id=FileId("folder"),
            modified_time="2026-09-03T00:00:00Z",
            revision_id=DocsRevisionId("source-revision-1"),
            media_type="application/vnd.google-apps.document",
            google_document=document,
        )
    )
    request = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file",
            file_id=FileId("source"),
            expected_modified_time="2026-09-03T00:00:00Z",
        ),
        mode=TransformMode.BOTH,
    )

    with pytest.raises(SourceTooLargeError):
        await DocumentTransformExecutor(gateway=gateway, generator=_Generator()).execute(
            operation_id=uuid.uuid7(),
            request=request,
        )

    assert gateway.brand_prepares == 0
    assert gateway.draft is None


def test_unslop_scanner_catches_high_signal_upstream_phrases() -> None:
    document = parse_markdown(
        "Here's the thing: at the end of the day, this is a game changer.",
        title="Draft",
    )

    codes = {candidate.code for candidate in scan_unslop_candidates(document)}

    assert {"throat_clearing", "filler", "jargon"} <= codes


class _Gateway(DocumentGateway):
    def __init__(
        self,
        snapshot: SourceSnapshot | None = None,
        *,
        verification: OutputVerification | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.verification = verification or OutputVerification(
            content_preserved=True,
            structure_preserved=True,
        )
        self.draft: OutputDraft | None = None
        self.requests: list[dict[str, Any]] = []
        self.applies = 0
        self.completed_result: dict[str, Any] | None = None
        self.brand_prepares = 0
        self.imported_docx: bytes | None = None

    @override
    async def load_drive_source(self, source: DriveFileSource) -> SourceSnapshot:
        assert self.snapshot is not None
        return self.snapshot

    @override
    async def recover_output(self, operation_id: uuid.UUID) -> OutputDraft | None:
        return self.draft

    @override
    async def source_is_unchanged(
        self,
        source: DriveFileSource,
        *,
        expected_modified_time: str,
        expected_revision_id: DocsRevisionId | None,
    ) -> bool:
        return True

    @override
    async def create_from_drive_copy(
        self,
        *,
        operation_id: uuid.UUID,
        source: DriveFileSource,
        source_sha256: str,
        output_name: str,
    ) -> OutputDraft:
        self.brand_prepares += 1
        return self._create(source_sha256, output_name)

    @override
    async def create_from_drive_markdown(
        self,
        *,
        operation_id: uuid.UUID,
        source: DriveFileSource,
        source_sha256: str,
        output_name: str,
        parent_folder_id: FileId,
        imported_docx: bytes | None = None,
    ) -> OutputDraft:
        self.imported_docx = imported_docx
        return self._create(source_sha256, output_name)

    @override
    async def create_from_markdown(
        self,
        *,
        operation_id: uuid.UUID,
        source: MarkdownSource,
        source_sha256: str,
        output_name: str,
        imported_docx: bytes | None = None,
    ) -> OutputDraft:
        self.imported_docx = imported_docx
        return self._create(source_sha256, output_name)

    @override
    async def prepare_brand_shell(
        self,
        *,
        operation_id: uuid.UUID,
        draft: OutputDraft,
    ) -> OutputDraft:
        self.draft = OutputDraft(
            output=draft.output,
            document_id=draft.document_id,
            tab_id=draft.tab_id,
            revision_id=draft.revision_id,
            source_sha256=draft.source_sha256,
            headers=(
                TabHeader(
                    tab_id=DocsTabId("tab-1"),
                    header_id=DocsSegmentId("header-1"),
                ),
            ),
            logo_uri="https://assets.example.test/sarj-logo.png",
        )
        return self.draft

    def _create(self, source_sha256: str, output_name: str) -> OutputDraft:
        self.draft = OutputDraft(
            output=DocumentOutput(
                file_id=FileId("output"),
                name=output_name,
                parent_folder_id=FileId("folder"),
            ),
            document_id=FileId("output"),
            tab_id=DocsTabId("tab-1"),
            revision_id=DocsRevisionId("output-revision-1"),
            source_sha256=source_sha256,
        )
        return self.draft

    @override
    async def apply_requests_once(
        self,
        *,
        operation_id: uuid.UUID,
        draft: OutputDraft,
        mutation_sha256: str,
        requests: list[dict[str, Any]],
        required_revision_id: DocsRevisionId,
    ) -> OutputDraft:
        assert required_revision_id == "output-revision-1"
        assert mutation_sha256
        self.applies += 1
        self.requests = requests
        self.draft = OutputDraft(
            output=draft.output,
            document_id=draft.document_id,
            tab_id=draft.tab_id,
            revision_id=DocsRevisionId("output-revision-2"),
            source_sha256=draft.source_sha256,
            headers=draft.headers,
            logo_uri=draft.logo_uri,
        )
        return self.draft

    @override
    async def verify_output(
        self,
        *,
        draft: OutputDraft,
        source: SourceSnapshot,
        expected: DocumentIR,
        verify_brand: bool,
    ) -> OutputVerification:
        del draft, source, expected, verify_brand
        return self.verification

    @override
    async def complete(
        self,
        *,
        operation_id: uuid.UUID,
        draft: OutputDraft,
        result: dict[str, Any],
    ) -> None:
        self.completed_result = result
        self.draft = OutputDraft(
            output=draft.output,
            document_id=draft.document_id,
            tab_id=draft.tab_id,
            revision_id=draft.revision_id,
            source_sha256=draft.source_sha256,
            headers=draft.headers,
            logo_uri=draft.logo_uri,
            completed=True,
            result=result,
            brand_warnings=draft.brand_warnings,
        )


async def test_markdown_both_runs_unslop_then_format_and_creates_sibling() -> None:
    gateway = _Gateway()
    generator = _Generator(
        {
            TransformPhase.UNSLOP: "We reviewed the options.",
            TransformPhase.FORMAT: "We reviewed the options carefully.",
        }
    )
    request = DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown",
            content="It is important to note that we reviewed the options.",
            name="Review",
            destination_folder_id=FileId("folder"),
        ),
        mode=TransformMode.BOTH,
    )

    result = await DocumentTransformExecutor(gateway=gateway, generator=generator).execute(
        operation_id=uuid.uuid7(), request=request
    )

    assert generator.calls == []
    assert result.output.name == "Review — Sarj polished"
    assert result.applied_patch_count == 1
    assert result.tool_versions["unslop_version"] == "2.3.0"
    assert result.validation.structure_preserved is False
    assert {finding.code for finding in result.findings} == {
        "logo_unavailable",
        "structure_verification_required",
    }
    assert result.needs_review is True
    assert gateway.imported_docx is not None
    assert gateway.imported_docx.startswith(b"PK")
    assert gateway.requests == []


async def test_unslop_clean_document_is_exact_content_noop_without_model_call() -> None:
    gateway = _Gateway()
    generator = _Generator()
    request = DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown",
            content="The team approved the proposal.",
            name="Decision",
            destination_folder_id=FileId("folder"),
        ),
        mode=TransformMode.UNSLOP,
    )

    result = await DocumentTransformExecutor(gateway=gateway, generator=generator).execute(
        operation_id=uuid.uuid7(), request=request
    )

    assert generator.calls == []
    assert result.applied_patch_count == 0
    assert gateway.requests[0]["insertText"]["text"] == "The team approved the proposal.\n"
    assert gateway.brand_prepares == 0
    assert "updateDocumentStyle" not in str(gateway.requests)
    assert "insertInlineImage" not in str(gateway.requests)


async def test_completed_output_recovery_never_reruns_model_or_mutations() -> None:
    gateway = _Gateway()
    generator = _Generator({TransformPhase.UNSLOP: "Changed text."})
    operation_id = uuid.uuid7()
    source = MarkdownSource(
        kind="markdown",
        content="The board approved option A.",
        name="Decision",
        destination_folder_id=FileId("folder"),
    )
    request = DocumentTransformRequest(source=source, mode=TransformMode.UNSLOP)
    first = await DocumentTransformExecutor(gateway=gateway, generator=generator).execute(
        operation_id=operation_id,
        request=request,
    )
    assert gateway.draft is not None
    gateway.draft = OutputDraft(
        output=gateway.draft.output,
        document_id=gateway.draft.document_id,
        tab_id=gateway.draft.tab_id,
        revision_id=gateway.draft.revision_id,
        source_sha256=first.source_sha256,
        completed=True,
    )
    generator.calls.clear()
    gateway.applies = 0

    result = await DocumentTransformExecutor(gateway=gateway, generator=generator).execute(
        operation_id=operation_id,
        request=request,
    )

    assert generator.calls == []
    assert gateway.applies == 0
    assert result.output.name == first.output.name
    assert result.needs_review is True
    assert result.findings[0].code == "recovered_completed_output"


async def test_force_language_change_is_rejected_and_requires_review() -> None:
    gateway = _Gateway()
    generator = _Generator(
        {TransformPhase.UNSLOP: "This is a game changer: teams may encrypt all records."}
    )
    request = DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown",
            content="This is a game changer: teams must encrypt all records.",
            name="Policy",
            destination_folder_id=FileId("folder"),
        ),
        mode=TransformMode.UNSLOP,
    )

    result = await DocumentTransformExecutor(gateway=gateway, generator=generator).execute(
        operation_id=uuid.uuid7(), request=request
    )

    assert result.applied_patch_count == 0
    assert result.needs_review is True
    assert result.findings[0].code == "force_language_changed"
    assert "must encrypt" in str(gateway.requests[0])


async def test_completed_operation_is_replayed_without_second_mutation() -> None:
    gateway = _Gateway()
    generator = _Generator()
    executor = DocumentTransformExecutor(gateway=gateway, generator=generator)
    request = DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown",
            content="A concise source.",
            name="Source",
            destination_folder_id=FileId("folder"),
        ),
        mode=TransformMode.FORMAT,
    )
    operation_id = uuid.uuid7()

    first = await executor.execute(operation_id=operation_id, request=request)
    second = await executor.execute(operation_id=operation_id, request=request)

    assert first == second
    assert gateway.applies == 1


@pytest.mark.parametrize(
    ("mode", "verification", "expected_codes"),
    [
        (
            TransformMode.UNSLOP,
            OutputVerification(content_preserved=False, structure_preserved=False),
            {"post_write_content_mismatch", "post_write_structure_mismatch"},
        ),
        (
            TransformMode.FORMAT,
            OutputVerification(
                content_preserved=True,
                structure_preserved=True,
                brand_applied=False,
            ),
            {"post_write_brand_mismatch"},
        ),
    ],
    ids=["content-and-structure", "brand"],
)
async def test_native_doc_post_write_mismatch_is_reported_for_review(
    mode: TransformMode,
    verification: OutputVerification,
    expected_codes: set[str],
) -> None:
    document = {
        "revisionId": "source-revision-1",
        "tabs": [
            {
                "tabProperties": {"tabId": "tab-1"},
                "documentTab": {
                    "body": {
                        "content": [
                            {
                                "startIndex": 1,
                                "endIndex": 29,
                                "paragraph": {
                                    "elements": [
                                        {"textRun": {"content": "The team approved the plan.\n"}}
                                    ]
                                },
                            }
                        ]
                    }
                },
                "childTabs": [],
            }
        ],
    }
    gateway = _Gateway(
        SourceSnapshot(
            title="Decision",
            parent_folder_id=FileId("folder"),
            modified_time="2026-09-03T00:00:00Z",
            revision_id=DocsRevisionId("source-revision-1"),
            media_type="application/vnd.google-apps.document",
            google_document=document,
        ),
        verification=verification,
    )
    request = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file",
            file_id=FileId("source"),
            expected_modified_time="2026-09-03T00:00:00Z",
        ),
        mode=mode,
    )

    result = await DocumentTransformExecutor(gateway=gateway, generator=_Generator()).execute(
        operation_id=uuid.uuid7(),
        request=request,
    )

    assert result.needs_review is True
    assert result.validation.protected_content_preserved is verification.content_preserved
    assert result.validation.structure_preserved is verification.structure_preserved
    assert {finding.code for finding in result.findings} == expected_codes


def test_google_doc_ir_uses_tab_ranges_and_locks_rich_text() -> None:
    document = {
        "revisionId": "revision-1",
        "tabs": [
            {
                "tabProperties": {"tabId": "tab-a"},
                "documentTab": {
                    "body": {
                        "content": [
                            {
                                "startIndex": 1,
                                "endIndex": 8,
                                "paragraph": {
                                    "paragraphStyle": {"namedStyleType": "HEADING_1"},
                                    "elements": [{"textRun": {"content": "Roadmap\n"}}],
                                },
                            },
                            {
                                "startIndex": 8,
                                "endIndex": 17,
                                "paragraph": {
                                    "elements": [
                                        {
                                            "textRun": {
                                                "content": "See ",
                                                "textStyle": {},
                                            }
                                        },
                                        {
                                            "textRun": {
                                                "content": "source\n",
                                                "textStyle": {"link": {"url": "https://sarj.ai"}},
                                            }
                                        },
                                    ]
                                },
                            },
                        ]
                    }
                },
                "childTabs": [],
            }
        ],
    }

    parsed = parse_google_document(document, title="Review")

    assert parsed.blocks[0].source_range is not None
    assert parsed.blocks[0].source_range.tab_id == "tab-a"
    assert parsed.blocks[0].level == 1
    assert parsed.blocks[1].editable is False


def test_google_doc_single_font_styled_run_remains_editable() -> None:
    document = {
        "revisionId": "revision-1",
        "tabs": [
            {
                "tabProperties": {"tabId": "tab-a"},
                "documentTab": {
                    "body": {
                        "content": [
                            {
                                "startIndex": 1,
                                "endIndex": 33,
                                "paragraph": {
                                    "elements": [
                                        {
                                            "textRun": {
                                                "content": "Additionally, review the plan.\n",
                                                "textStyle": {
                                                    "weightedFontFamily": {"fontFamily": "Nunito"}
                                                },
                                            }
                                        }
                                    ]
                                },
                            }
                        ]
                    }
                },
                "childTabs": [],
            }
        ],
    }

    parsed = parse_google_document(document, title="Review")

    assert parsed.blocks[0].editable is True


def test_scanner_reports_candidates_but_never_changes_text() -> None:
    source = "In today's fast-paced market, this robust plan serves as a testament to the team."
    document = parse_markdown(source, title="Review")

    candidates = scan_unslop_candidates(document)

    assert {candidate.code for candidate in candidates} == {
        "canned_intro",
        "inflated_phrase",
    }
    assert document.blocks[0].text == source
    assert utf16_length("A😀") == 3


def test_markdown_rewrite_tracks_unchanged_repeated_blocks() -> None:
    source = "Repeated sentence.\nRepeated sentence.\n"
    original = parse_markdown(source, title="Repeated")
    transformed = original.replace_blocks({"b1": "A focused replacement."})

    assert rewrite_markdown(source, original, transformed) == (
        "Repeated sentence.\nA focused replacement.\n"
    )


def test_copy_styles_use_post_edit_utf16_ranges_for_two_changed_paragraphs() -> None:
    original = DocumentIR(
        title="Emoji review",
        source_format="google_doc",
        blocks=(
            TextBlock(
                id="b0",
                kind=BlockKind.PARAGRAPH,
                text="😀 alpha",
                source_range=SourceRange(1, 9, DocsTabId("tab-a")),
            ),
            TextBlock(
                id="b1",
                kind=BlockKind.PARAGRAPH,
                text="second paragraph",
                source_range=SourceRange(10, 26, DocsTabId("tab-a")),
            ),
        ),
    )
    transformed = original.replace_blocks({"b0": "😀 expanded alpha", "b1": "second"})

    requests = build_copy_mutations(
        original,
        transformed,
        profile=SarjBrandProfile(),
        headers=(
            TabHeader(
                tab_id=DocsTabId("tab-a"),
                header_id=DocsSegmentId("header-a"),
            ),
        ),
        logo_uri="https://assets.example.test/sarj-logo.png",
    )
    style_ranges = [
        request["updateTextStyle"]["range"]
        for request in requests
        if "updateTextStyle" in request and "segmentId" not in request["updateTextStyle"]["range"]
    ]

    assert style_ranges == [
        {"startIndex": 1, "endIndex": 18, "tabId": "tab-a"},
        {"startIndex": 19, "endIndex": 25, "tabId": "tab-a"},
    ]
    assert all("insertPageNumber" not in request for request in requests)


def test_copy_branding_skips_locked_rich_text_blocks() -> None:
    locked = TextBlock(
        id="b0",
        kind=BlockKind.PARAGRAPH,
        text="Linked or suggested text",
        source_range=SourceRange(1, 25, DocsTabId("tab-a")),
        editable=False,
    )
    document = DocumentIR(
        title="Protected",
        blocks=(locked,),
        source_format="google_doc",
        tab_ids=(DocsTabId("tab-a"),),
    )

    requests = build_copy_mutations(
        document,
        document,
        profile=SarjBrandProfile(),
        headers=(),
        logo_uri="",
    )

    assert all("updateTextStyle" not in request for request in requests)
    assert all("updateParagraphStyle" not in request for request in requests)


@pytest.mark.parametrize("level", [4, 5, 6])
def test_copy_branding_preserves_deeper_heading_levels(level: int) -> None:
    heading = TextBlock(
        id="b0",
        kind=BlockKind.HEADING,
        text="Deep heading",
        level=level,
        source_range=SourceRange(1, 13, DocsTabId("tab-a")),
    )
    document = DocumentIR(
        title="Hierarchy",
        blocks=(heading,),
        source_format="google_doc",
        tab_ids=(DocsTabId("tab-a"),),
    )

    requests = build_copy_mutations(
        document,
        document,
        profile=SarjBrandProfile(),
        headers=(),
        logo_uri="",
    )
    paragraph_styles = [
        request["updateParagraphStyle"]["paragraphStyle"]
        for request in requests
        if "updateParagraphStyle" in request
    ]

    assert paragraph_styles[0]["namedStyleType"] == f"HEADING_{level}"
    assert paragraph_styles[0]["keepWithNext"] is True


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("spaceAbove", {"magnitude": 0, "unit": "PT"}),
        ("spaceBelow", {"magnitude": 0, "unit": "PT"}),
        ("keepWithNext", True),
    ],
)
def test_post_write_brand_verification_requires_paragraph_spacing(
    field: str,
    invalid_value: object,
) -> None:
    document = _branded_google_document()
    parsed = parse_google_document(document, title="Decision")
    assert verify_brand_application(document, parsed) is True
    changed = deepcopy(document)
    paragraph_style = changed["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"][
        "paragraphStyle"
    ]
    paragraph_style[field] = invalid_value

    assert (
        verify_brand_application(changed, parse_google_document(changed, title="Decision")) is False
    )


def test_post_write_brand_verification_requires_canonical_logo_source() -> None:
    document = _branded_google_document()
    document["tabs"][0]["documentTab"]["inlineObjects"]["logo-1"]["inlineObjectProperties"][
        "embeddedObject"
    ]["imageProperties"]["sourceUri"] = "https://example.test/wrong.png"

    assert (
        verify_brand_application(document, parse_google_document(document, title="Decision"))
        is False
    )


def test_copy_branding_styles_header_deterministically() -> None:
    document = DocumentIR(
        title="Decision",
        blocks=(),
        source_format="google_doc",
        tab_ids=(DocsTabId("tab-a"),),
    )

    requests = build_copy_mutations(
        document,
        document,
        profile=SarjBrandProfile(),
        headers=(TabHeader(tab_id=DocsTabId("tab-a"), header_id=DocsSegmentId("header-a")),),
        logo_uri="https://wiki.sarj.ai/brand/sarj-logo-light.png",
    )

    header_text_style = next(
        request["updateTextStyle"]
        for request in requests
        if "updateTextStyle" in request
        and request["updateTextStyle"]["range"].get("segmentId") == "header-a"
    )
    assert header_text_style["range"] == {
        "startIndex": 0,
        "endIndex": 9,
        "segmentId": "header-a",
        "tabId": "tab-a",
    }
    assert header_text_style["textStyle"] == {
        "weightedFontFamily": {"fontFamily": "Nunito"},
        "fontSize": {"magnitude": 9, "unit": "PT"},
        "foregroundColor": {
            "color": {
                "rgbColor": {
                    "red": 115 / 255,
                    "green": 115 / 255,
                    "blue": 115 / 255,
                }
            }
        },
        "bold": True,
    }
    header_paragraph_style = next(
        request["updateParagraphStyle"]
        for request in requests
        if "updateParagraphStyle" in request
        and request["updateParagraphStyle"]["range"].get("segmentId") == "header-a"
    )
    assert header_paragraph_style["range"]["endIndex"] == 10
    assert header_paragraph_style["paragraphStyle"] == {
        "alignment": "END",
        "lineSpacing": 100,
        "spaceAbove": {"magnitude": 0, "unit": "PT"},
        "spaceBelow": {"magnitude": 0, "unit": "PT"},
        "keepWithNext": False,
        "namedStyleType": "NORMAL_TEXT",
    }


def test_copy_branding_normalizes_header_title_and_uses_utf16_indexes() -> None:
    document = DocumentIR(
        title="Decision 😀\nBrief",
        blocks=(),
        source_format="google_doc",
        tab_ids=(DocsTabId("tab-a"),),
    )

    requests = build_copy_mutations(
        document,
        document,
        profile=SarjBrandProfile(),
        headers=(TabHeader(tab_id=DocsTabId("tab-a"), header_id=DocsSegmentId("header-a")),),
        logo_uri="https://wiki.sarj.ai/brand/sarj-logo-light.png",
    )

    header_requests = [
        request
        for request in requests
        if next(iter(request.values())).get("endOfSegmentLocation", {}).get("segmentId")
        == "header-a"
        or next(iter(request.values())).get("range", {}).get("segmentId") == "header-a"
    ]
    assert [next(iter(request)) for request in header_requests] == [
        "insertText",
        "insertInlineImage",
        "updateTextStyle",
        "updateParagraphStyle",
    ]
    assert header_requests[0]["insertText"]["text"] == "Decision 😀 Brief\t"
    assert header_requests[2]["updateTextStyle"]["range"]["endIndex"] == 18
    assert header_requests[3]["updateParagraphStyle"]["range"]["endIndex"] == 19


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("alignment", "START"),
        ("lineSpacing", 115),
        ("spaceBelow", {"magnitude": 6, "unit": "PT"}),
    ],
)
def test_post_write_brand_verification_requires_header_paragraph_style(
    field: str,
    invalid_value: object,
) -> None:
    document = _branded_google_document()
    changed = deepcopy(document)
    changed["tabs"][0]["documentTab"]["headers"]["header-a"]["content"][0]["paragraph"][
        "paragraphStyle"
    ][field] = invalid_value

    assert verify_brand_application(document, parse_google_document(document, title="Decision"))
    assert not verify_brand_application(changed, parse_google_document(changed, title="Decision"))


def test_post_write_brand_verification_requires_header_text_style_and_title() -> None:
    document = _branded_google_document()
    wrong_style = deepcopy(document)
    text_run = wrong_style["tabs"][0]["documentTab"]["headers"]["header-a"]["content"][0][
        "paragraph"
    ]["elements"][0]["textRun"]
    text_run["textStyle"]["weightedFontFamily"]["fontFamily"] = "Arial"
    wrong_title = deepcopy(document)
    wrong_title["tabs"][0]["documentTab"]["headers"]["header-a"]["content"][0]["paragraph"][
        "elements"
    ][0]["textRun"]["content"] = "Other\t"

    assert not verify_brand_application(
        wrong_style, parse_google_document(wrong_style, title="Decision")
    )
    assert not verify_brand_application(
        wrong_title, parse_google_document(wrong_title, title="Decision")
    )


def test_multi_tab_brand_verification_never_uses_top_level_inline_objects() -> None:
    document = _branded_google_document()
    second = deepcopy(document["tabs"][0])
    second["tabProperties"]["tabId"] = "tab-b"
    second_tab = second["documentTab"]
    second_tab["documentStyle"]["defaultHeaderId"] = "header-b"
    second_tab["headers"] = {"header-b": second_tab["headers"].pop("header-a")}
    second_tab.pop("inlineObjects")
    document["tabs"].append(second)
    document["inlineObjects"] = deepcopy(document["tabs"][0]["documentTab"]["inlineObjects"])

    assert not verify_brand_application(document, parse_google_document(document, title="Decision"))


def test_post_write_brand_verification_rejects_extra_or_wrong_size_logo() -> None:
    document = _branded_google_document()
    extra_logo = deepcopy(document)
    elements = extra_logo["tabs"][0]["documentTab"]["headers"]["header-a"]["content"][0][
        "paragraph"
    ]["elements"]
    elements.append({"inlineObjectElement": {"inlineObjectId": "logo-2"}})
    wrong_size = deepcopy(document)
    wrong_size["tabs"][0]["documentTab"]["inlineObjects"]["logo-1"]["inlineObjectProperties"][
        "embeddedObject"
    ]["size"]["width"] = {"magnitude": 40, "unit": "PT"}

    assert not verify_brand_application(
        extra_logo, parse_google_document(extra_logo, title="Decision")
    )
    assert not verify_brand_application(
        wrong_size, parse_google_document(wrong_size, title="Decision")
    )


def test_post_write_brand_verification_rejects_table_or_reversed_header() -> None:
    document = _branded_google_document()
    extra_table = deepcopy(document)
    extra_table["tabs"][0]["documentTab"]["headers"]["header-a"]["content"].append(
        {"table": {"rows": 1, "columns": 1}}
    )
    reversed_header = deepcopy(document)
    elements = reversed_header["tabs"][0]["documentTab"]["headers"]["header-a"]["content"][0][
        "paragraph"
    ]["elements"]
    elements[0], elements[1] = elements[1], elements[0]

    assert not verify_brand_application(
        extra_table, parse_google_document(extra_table, title="Decision")
    )
    assert not verify_brand_application(
        reversed_header, parse_google_document(reversed_header, title="Decision")
    )


def test_foreign_pending_mutation_fails_terminal_before_google_write() -> None:
    gateway = object.__new__(GoogleDocumentGateway)
    draft = OutputDraft(
        output=DocumentOutput(
            file_id=FileId("output"),
            name="Output",
            parent_folder_id=FileId("parent"),
        ),
        document_id=FileId("output"),
        tab_id=DocsTabId("tab-a"),
        revision_id=DocsRevisionId("revision-2"),
        source_sha256="source-hash",
        pending_mutation_sha256="v2-mutation",
    )

    with pytest.raises(IncompatiblePendingOutputError):
        gateway._apply_requests_sync(
            draft,
            "v3-mutation",
            [{"insertText": {"text": "unsafe"}}],
            DocsRevisionId("revision-2"),
        )


def test_single_tab_brand_verification_supports_legacy_top_level_inline_objects() -> None:
    document = _branded_google_document()
    document["inlineObjects"] = document["tabs"][0]["documentTab"].pop("inlineObjects")

    assert verify_brand_application(document, parse_google_document(document, title="Decision"))


def _branded_google_document() -> dict[str, Any]:
    return {
        "revisionId": "revision-2",
        "tabs": [
            {
                "tabProperties": {"tabId": "tab-a"},
                "documentTab": {
                    "inlineObjects": {
                        "logo-1": {
                            "inlineObjectProperties": {
                                "embeddedObject": {
                                    "imageProperties": {
                                        "sourceUri": (
                                            "https://wiki.sarj.ai/brand/sarj-logo-light.png"
                                        )
                                    },
                                    "size": {
                                        "width": {"magnitude": 52, "unit": "PT"},
                                        "height": {"magnitude": 28, "unit": "PT"},
                                    },
                                }
                            }
                        }
                    },
                    "documentStyle": {
                        "marginLeft": {"magnitude": 72, "unit": "PT"},
                        "marginRight": {"magnitude": 72, "unit": "PT"},
                        "marginTop": {"magnitude": 72, "unit": "PT"},
                        "marginBottom": {"magnitude": 72, "unit": "PT"},
                        "defaultHeaderId": "header-a",
                    },
                    "headers": {
                        "header-a": {
                            "content": [
                                {
                                    "paragraph": {
                                        "paragraphStyle": {
                                            "namedStyleType": "NORMAL_TEXT",
                                            "alignment": "END",
                                            "lineSpacing": 100,
                                            "spaceAbove": {"magnitude": 0, "unit": "PT"},
                                            "spaceBelow": {"magnitude": 0, "unit": "PT"},
                                            "keepWithNext": False,
                                        },
                                        "elements": [
                                            {
                                                "textRun": {
                                                    "content": "Decision\t",
                                                    "textStyle": {
                                                        "weightedFontFamily": {
                                                            "fontFamily": "Nunito"
                                                        },
                                                        "fontSize": {
                                                            "magnitude": 9,
                                                            "unit": "PT",
                                                        },
                                                        "foregroundColor": {
                                                            "color": {
                                                                "rgbColor": {
                                                                    "red": 115 / 255,
                                                                    "green": 115 / 255,
                                                                    "blue": 115 / 255,
                                                                }
                                                            }
                                                        },
                                                        "bold": True,
                                                    },
                                                }
                                            },
                                            {"inlineObjectElement": {"inlineObjectId": "logo-1"}},
                                        ],
                                    }
                                }
                            ]
                        }
                    },
                    "body": {
                        "content": [
                            {
                                "startIndex": 1,
                                "endIndex": 11,
                                "paragraph": {
                                    "paragraphStyle": {
                                        "namedStyleType": "NORMAL_TEXT",
                                        "alignment": "START",
                                        "lineSpacing": 115,
                                        "spaceAbove": {"magnitude": 2, "unit": "PT"},
                                        "spaceBelow": {"magnitude": 6, "unit": "PT"},
                                        "keepWithNext": False,
                                    },
                                    "elements": [
                                        {
                                            "textRun": {
                                                "content": "Approved.\n",
                                                "textStyle": {
                                                    "weightedFontFamily": {"fontFamily": "Nunito"},
                                                    "fontSize": {
                                                        "magnitude": 11,
                                                        "unit": "PT",
                                                    },
                                                    "foregroundColor": {
                                                        "color": {
                                                            "rgbColor": {
                                                                "red": 10 / 255,
                                                                "green": 10 / 255,
                                                                "blue": 10 / 255,
                                                            }
                                                        }
                                                    },
                                                },
                                            }
                                        }
                                    ],
                                },
                            }
                        ]
                    },
                },
                "childTabs": [],
            }
        ],
    }


def _in_place_document(text: str, *, revision: str = "revision-1") -> dict[str, Any]:
    content = f"{text}\n"
    return {
        "documentId": "source",
        "revisionId": revision,
        "tabs": [
            {
                "tabProperties": {"tabId": "tab-1"},
                "documentTab": {
                    "body": {
                        "content": [
                            {
                                "startIndex": 1,
                                "endIndex": 1 + utf16_length(content),
                                "paragraph": {
                                    "paragraphStyle": {},
                                    "elements": [{"textRun": {"content": content}}],
                                },
                            }
                        ]
                    }
                },
                "childTabs": [],
            }
        ],
    }


class _CheckpointStore:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.generation = 0
        self.checkpoints: list[InPlaceCheckpoint] = []

    async def save_document_transform_checkpoint(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        expected_generation: int,
        checkpoint: InPlaceCheckpoint,
    ) -> int:
        del task_id, lease_token
        assert expected_generation == self.generation
        self.events.append(f"checkpoint:{checkpoint.stage}")
        self.checkpoints.append(checkpoint)
        self.generation += 1
        return self.generation


class _InPlaceGateway(_Gateway):
    def __init__(self, text: str, *, missing_header: bool = False) -> None:
        document = _in_place_document(text)
        super().__init__(
            SourceSnapshot(
                title="Source",
                parent_folder_id=FileId("folder"),
                modified_time="2026-09-03T00:00:00Z",
                revision_id=DocsRevisionId("revision-1"),
                media_type="application/vnd.google-apps.document",
                google_document=document,
                owned_by_me=True,
                can_edit=True,
                is_personal_drive=True,
                web_url="https://docs.google.com/document/d/source/edit",
            )
        )
        self.document = document
        self.missing_header = missing_header
        self.header_created = False
        self.events: list[str] = []

    @override
    async def inspect_in_place(self, *, snapshot: SourceSnapshot, title: str) -> InPlaceInspection:
        del title
        headers = (
            ()
            if self.missing_header and not self.header_created
            else (TabHeader(tab_id=DocsTabId("tab-1"), header_id=DocsSegmentId("header-1")),)
        )
        return InPlaceInspection(
            output=DocumentOutput(
                file_id=FileId("source"),
                name="Source",
                parent_folder_id=FileId("folder"),
            ),
            headers=headers,
            missing_header_tab_ids=(DocsTabId("tab-1"),) if not headers else (),
            unknown_header_tab_ids=(),
            marker_range={"startIndex": 20, "endIndex": 21, "tabId": "tab-1"},
            logo_uri="https://assets.example.test/logo.png",
            structural_sha256="structure",
            protected_structure_sha256="protected",
            body_sha256="body",
            header_safe_sha256="header-safe",
        )

    @override
    async def apply_in_place_stage(
        self,
        *,
        document_id: FileId,
        required_revision_id: DocsRevisionId,
        marker_name: str,
        marker_range: dict[str, Any],
        requests: list[dict[str, Any]],
    ) -> InPlaceStageResult:
        del document_id, required_revision_id, marker_name, marker_range
        self.events.append("apply")
        if any("createHeader" in request for request in requests):
            self.header_created = True
            revision = "revision-2"
        else:
            source = parse_google_document(self.document, title="Source")
            replacement = next(
                (
                    request["insertText"]["text"]
                    for request in requests
                    if "insertText" in request
                    and request["insertText"].get("location", {}).get("segmentId") is None
                ),
                source.blocks[0].text,
            )
            self.document = _in_place_document(replacement, revision="revision-3")
            revision = "revision-3"
        self.document["revisionId"] = revision
        return InPlaceStageResult(
            revision_id=DocsRevisionId(revision),
            document=deepcopy(self.document),
        )

    @override
    async def verify_in_place(
        self,
        *,
        document: dict[str, Any],
        title: str,
        expected: DocumentIR,
        structural_sha256: str,
        protected_structure_sha256: str,
        verify_brand: bool,
    ) -> OutputVerification:
        del document, title, expected, structural_sha256, protected_structure_sha256, verify_brand
        return OutputVerification(
            content_preserved=True, structure_preserved=True, brand_applied=True
        )


def _in_place_request(mode: TransformMode) -> DocumentTransformRequest:
    return DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file",
            file_id=FileId("source"),
            expected_modified_time="2026-09-03T00:00:00Z",
        ),
        mode=mode,
        output_strategy=OutputStrategy.IN_PLACE,
    )


async def test_in_place_unslop_checkpoints_before_mutation_and_reports_revisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _InPlaceGateway("Moreover, we approved the plan.")
    store = _CheckpointStore(gateway.events)
    result = await DocumentTransformExecutor(
        gateway=gateway,
        generator=_Generator(),
        checkpoint_store=store,
        task_id=TaskId(uuid.uuid7()),
        lease_token=uuid.uuid7(),
    ).execute(operation_id=uuid.uuid7(), request=_in_place_request(TransformMode.UNSLOP))

    assert gateway.events[:2] == ["checkpoint:content", "apply"]
    assert result.output.file_id == "source"
    assert result.output_strategy is OutputStrategy.IN_PLACE
    assert result.changed is True
    assert result.source_revision_id == "revision-1"
    assert result.output_revision_id == "revision-3"
    persisted = task_service_module._serialize_checkpoint(store.checkpoints[0])
    assert "Moreover, we approved the plan." not in json.dumps(persisted)
    assert set(persisted) >= {"plan_ciphertext", "stage", "source_sha256"}
    monkeypatch.setattr(task_service_module, "_MAX_CHECKPOINT_PLAN_BYTES", 1)
    with pytest.raises(CheckpointTooLargeError):
        task_service_module._serialize_checkpoint(store.checkpoints[0])


async def test_in_place_clean_unslop_is_zero_batch_noop() -> None:
    gateway = _InPlaceGateway("The team approved the plan.")
    store = _CheckpointStore(gateway.events)
    result = await DocumentTransformExecutor(
        gateway=gateway,
        generator=_Generator(),
        checkpoint_store=store,
        task_id=TaskId(uuid.uuid7()),
        lease_token=uuid.uuid7(),
    ).execute(operation_id=uuid.uuid7(), request=_in_place_request(TransformMode.UNSLOP))

    assert "apply" not in gateway.events
    assert result.changed is False
    assert result.source_revision_id == result.output_revision_id == "revision-1"


async def test_in_place_format_checkpoints_header_then_content() -> None:
    gateway = _InPlaceGateway("Quarterly review", missing_header=True)
    store = _CheckpointStore(gateway.events)
    result = await DocumentTransformExecutor(
        gateway=gateway,
        generator=_Generator(),
        checkpoint_store=store,
        task_id=TaskId(uuid.uuid7()),
        lease_token=uuid.uuid7(),
    ).execute(operation_id=uuid.uuid7(), request=_in_place_request(TransformMode.FORMAT))

    assert gateway.events[:4] == [
        "checkpoint:headers",
        "apply",
        "checkpoint:content",
        "apply",
    ]
    assert result.changed is True
    assert result.output_revision_id == "revision-3"


def test_in_place_request_rejects_markdown_and_output_name() -> None:
    with pytest.raises(ValueError, match="native Google Doc"):
        DocumentTransformRequest(
            source=MarkdownSource(
                kind="markdown",
                content="Draft",
                name="Draft",
                destination_folder_id=FileId("folder"),
            ),
            mode=TransformMode.UNSLOP,
            output_strategy=OutputStrategy.IN_PLACE,
        )
    with pytest.raises(ValueError, match="output_name"):
        DocumentTransformRequest(
            source=DriveFileSource(
                kind="drive_file",
                file_id=FileId("source"),
                expected_modified_time="time",
            ),
            mode=TransformMode.UNSLOP,
            output_name="Fork",
            output_strategy=OutputStrategy.IN_PLACE,
        )


def test_copy_request_keeps_legacy_canonical_payload_hash_shape() -> None:
    request = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file",
            file_id=FileId("source"),
            expected_modified_time="time",
        ),
        mode=TransformMode.UNSLOP,
    )

    serialized = task_service_module._serialize(request)

    assert "output_strategy" not in serialized
    assert serialized["output_name"] is None


def test_in_place_drive_id_is_bounded_and_google_safe() -> None:
    opaque = DriveFileSource(
        kind="drive_file",
        file_id=FileId("opaque/id:still-accepted-for-copy"),
        expected_modified_time="time",
    )
    assert opaque.file_id == "opaque/id:still-accepted-for-copy"
    with pytest.raises(ValueError, match="between 1 and 128"):
        DocumentTransformRequest(
            source=DriveFileSource(
                kind="drive_file",
                file_id=FileId("a" * 129),
                expected_modified_time="time",
            ),
            mode=TransformMode.UNSLOP,
            output_strategy=OutputStrategy.IN_PLACE,
        )


def test_in_place_marker_reconciliation_is_exact_and_tab_scoped() -> None:
    expected = {"startIndex": 10, "endIndex": 11, "tabId": "tab-1"}
    marker = "sarj-transform-operation-content-hash"
    document = _in_place_document("Source")
    document["tabs"][0]["documentTab"]["namedRanges"] = {
        marker: {"namedRanges": [{"name": marker, "namedRangeId": "range-1", "ranges": [expected]}]}
    }

    assert google_gateway_module._has_exact_named_range(document, marker, expected)
    wrong_tab = {**expected, "tabId": "tab-2"}
    assert not google_gateway_module._has_exact_named_range(document, marker, wrong_tab)
    document["tabs"][0]["documentTab"]["namedRanges"][marker]["namedRanges"].append(
        {"name": marker, "namedRangeId": "range-2", "ranges": [expected]}
    )
    assert not google_gateway_module._has_exact_named_range(document, marker, expected)


def test_in_place_rejects_active_suggestion_metadata() -> None:
    assert google_gateway_module._has_active_suggestions(
        {"paragraph": {"suggestedParagraphStyleChanges": {"suggestion-1": {}}}}
    )


def test_google_in_place_lost_response_reconciles_atomic_tab_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "sarj-transform-operation-headers-hash"
    marker_range = {"startIndex": 10, "endIndex": 11, "tabId": "tab-1"}
    document = _in_place_document("Source", revision="revision-2")
    document["tabs"][0]["documentTab"]["namedRanges"] = {
        marker: {
            "namedRanges": [{"name": marker, "namedRangeId": "range-1", "ranges": [marker_range]}]
        }
    }
    captured: dict[str, Any] = {}

    class _Documents:
        def batchUpdate(self, **kwargs: Any) -> object:  # ruff: ignore[invalid-function-name]
            captured.update(kwargs)
            return object()

    class _Provider:
        def documents(self) -> _Documents:
            return _Documents()

    provider = _Provider()
    gateway = object.__new__(GoogleDocumentGateway)

    def get_provider() -> _Provider:
        return provider

    def request_boundary(value: Any, **kwargs: Any) -> Any:
        return value(**kwargs)

    monkeypatch.setattr(gateway, "_docs", get_provider)
    monkeypatch.setattr(google_gateway_module, "_google_http_request", request_boundary)

    def lost_response(_request: object) -> dict[str, Any]:
        raise google_gateway_module.DocumentGatewayError

    def get_document(_document_id: FileId) -> dict[str, Any]:
        return document

    monkeypatch.setattr(gateway, "_execute_mutation_dict", lost_response)
    monkeypatch.setattr(gateway, "_get_document_sync", get_document)

    result = gateway._apply_in_place_stage_sync(
        FileId("source"),
        DocsRevisionId("revision-1"),
        marker,
        marker_range,
        [{"createHeader": {"type": "DEFAULT"}}],
    )

    body = captured["body"]
    assert body["writeControl"] == {"requiredRevisionId": "revision-1"}
    assert body["requests"][0] == {"createNamedRange": {"name": marker, "range": marker_range}}
    assert body["requests"][1] == {"createHeader": {"type": "DEFAULT"}}
    assert result.revision_id == "revision-2"
