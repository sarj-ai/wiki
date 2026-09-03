from __future__ import annotations

from typing import Any, NamedTuple, override
import uuid

from app.document_transform.brand import SarjBrandProfile, build_copy_mutations
from app.document_transform.executor import DocumentTransformExecutor
from app.document_transform.gateway import DocumentGateway, OutputDraft, SourceSnapshot, TabHeader
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
    MarkdownSource,
    TextPatch,
    TransformMode,
    TransformPhase,
)
from app.document_transform.unslop import scan_unslop_candidates
from app.identifiers import DocsRevisionId, DocsSegmentId, DocsTabId, FileId


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
        "Additionally, Alice approved the acquisition by Northstar.",
        "Additionally, Bob rejected the acquisition by Northstar.",
    )

    decision = validate_patches(document, [patch], phase=TransformPhase.UNSLOP)

    assert decision.accepted == {}
    assert decision.findings[0].code in {
        "proper_noun_changed",
        "noncandidate_content_changed",
    }


def test_unslop_rejects_edit_to_sentence_without_candidate() -> None:
    document, patch = _unslop_patch(
        "Additionally, we reviewed the plan. The board approved option A.",
        "We reviewed the plan. The board rejected option A.",
    )

    decision = validate_patches(document, [patch], phase=TransformPhase.UNSLOP)

    assert decision.accepted == {}
    assert decision.findings[0].code == "sentence_locality_changed"


def test_unslop_accepts_small_candidate_only_deletion() -> None:
    document, patch = _unslop_patch(
        "Additionally, we reviewed the plan.",
        "We reviewed the plan.",
    )

    decision = validate_patches(document, [patch], phase=TransformPhase.UNSLOP)

    assert decision.accepted == {"b0": "We reviewed the plan."}


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


def test_unslop_scanner_catches_high_signal_upstream_phrases() -> None:
    document = parse_markdown(
        "Here's the thing: at the end of the day, this is a game changer.",
        title="Draft",
    )

    codes = {candidate.code for candidate in scan_unslop_candidates(document)}

    assert {"throat_clearing", "filler", "jargon"} <= codes


class _Gateway(DocumentGateway):
    def __init__(self, snapshot: SourceSnapshot | None = None) -> None:
        self.snapshot = snapshot
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

    assert generator.calls == [TransformPhase.UNSLOP]
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
    generator = _Generator({TransformPhase.UNSLOP: "Additionally, teams may encrypt all records."})
    request = DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown",
            content="Additionally, teams must encrypt all records.",
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
        request["updateTextStyle"]["range"] for request in requests if "updateTextStyle" in request
    ]

    assert style_ranges == [
        {"startIndex": 1, "endIndex": 18, "tabId": "tab-a"},
        {"startIndex": 19, "endIndex": 25, "tabId": "tab-a"},
    ]
    assert all("insertPageNumber" not in request for request in requests)
