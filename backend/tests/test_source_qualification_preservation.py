from pathlib import Path
from typing import override

from pydantic import BaseModel, ConfigDict
import pytest

from app.document_transform.ir import DocumentIR, parse_markdown
from app.document_transform.models import PatchGenerator, RewriteReview
from app.document_transform.planner import TextTransformPlanner
from app.document_transform.schemas import (
    DocumentTransformRequest,
    MarkdownSource,
    TextPatch,
    TransformMode,
    TransformPhase,
)
from app.document_transform.unslop import model_editable_blocks
from app.identifiers import FileId


class _SourceCase(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    id: str
    source: str
    bad_candidate: str
    must_preserve: list[str]


class _Corpus(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    cases: list[_SourceCase]
    control_variants: list[_SourceCase]


_CORPUS = _Corpus.model_validate_json(
    (Path(__file__).parent / "data/source_qualification_cases.json").read_text()
)
_REGRESSIONS = tuple(
    case for case in (*_CORPUS.cases, *_CORPUS.control_variants) if case.must_preserve
)


class _ApprovingEditor(PatchGenerator):
    def __init__(self, proposals: dict[str, str]) -> None:
        self.proposals = proposals

    @property
    @override
    def model_ids(self) -> tuple[str, ...]:
        return ("controlled-approving-editor",)

    @override
    async def generate(self, document: DocumentIR, *, phase: TransformPhase) -> list[TextPatch]:
        return [
            TextPatch(
                block_id=block.id,
                expected_sha256=block.sha256,
                replacement=self.proposals.get(block.id, block.text),
            )
            for block in model_editable_blocks(document)
        ]

    @override
    async def review(self, document: DocumentIR, patches: list[TextPatch]) -> RewriteReview:
        return RewriteReview(frozenset(patch.block_id for patch in patches), {})


def _request(source: str, mode: TransformMode) -> DocumentTransformRequest:
    return DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown",
            name="Source qualifications",
            content=source,
            destination_folder_id=FileId("folder"),
        ),
        mode=mode,
    )


@pytest.mark.parametrize("mode", [TransformMode.UNSLOP, TransformMode.BOTH])
@pytest.mark.parametrize("case", _REGRESSIONS, ids=[case.id for case in _REGRESSIONS])
async def test_model_approval_cannot_delete_source_qualifications_or_instructions(
    case: _SourceCase, mode: TransformMode
) -> None:
    document = parse_markdown(case.source, title="Source qualifications")
    editor = _ApprovingEditor({document.blocks[0].id: case.bad_candidate})
    result = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        document, _request(case.source, mode)
    )
    assert result.document.blocks[0].text == case.source
    assert any(
        finding.code in {"force_language_changed", "empty_replacement"}
        for finding in result.findings
    )


@pytest.mark.parametrize("mode", [TransformMode.UNSLOP, TransformMode.BOTH])
async def test_qualified_code_introduction_can_still_be_shortened(mode: TransformMode) -> None:
    source = _CORPUS.cases[0].source
    replacement = (
        "These measurement and budgeting examples are not benchmark results; their numeric "
        "constants are example parameters, not additional reference estimates."
    )
    document = parse_markdown(source, title="Source qualifications")
    result = await TextTransformPlanner(
        generator=_ApprovingEditor({document.blocks[0].id: replacement}), allow_unslop_model=True
    ).transform(document, _request(source, mode))
    assert result.document.blocks[0].text == replacement


@pytest.mark.parametrize("mode", [TransformMode.UNSLOP, TransformMode.BOTH])
async def test_rejected_instruction_deletion_does_not_block_other_faithful_edits(
    mode: TransformMode,
) -> None:
    instruction = _CORPUS.cases[1].source
    framing = (
        "This working note is intended to bring together latency estimates from the Google "
        "SRE reference so the same information can be read in one place."
    )
    replacement = "This note brings together latency estimates from the Google SRE reference."
    source = instruction + "\n\n" + framing
    document = parse_markdown(source, title="Source qualifications")
    instruction_block = next(block for block in document.blocks if block.text == instruction)
    framing_block = next(block for block in document.blocks if block.text == framing)
    editor = _ApprovingEditor({instruction_block.id: "", framing_block.id: replacement})
    result = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        document, _request(source, mode)
    )
    actual = {block.id: block.text for block in result.document.blocks}
    assert actual[instruction_block.id] == instruction
    assert actual[framing_block.id] == replacement


@pytest.fixture
def qualified_guide_editor_factory() -> type[_ApprovingEditor]:
    return _ApprovingEditor


@pytest.mark.parametrize("mode", [TransformMode.UNSLOP, TransformMode.BOTH])
@pytest.mark.parametrize("modifier", ["short", "brief", "working"])
async def test_qualified_guide_can_remove_its_writing_preamble(
    modifier: str, mode: TransformMode, qualified_guide_editor_factory: type[_ApprovingEditor]
) -> None:
    source = (
        f"In a broad and general sense, this {modifier} guide is intended to bring together "
        "five ideas for a weekend in New York City so the same information can be read in "
        "one place. The suggestions include comedy, miniature trains, gallery programmes, "
        "a museum evening, and ice skating. There is a range of options in this selection."
    )
    replacement = (
        "Five New York City weekend ideas: comedy, miniature trains, gallery programmes, "
        "a museum evening, and ice skating."
    )
    document = parse_markdown(source, title="Weekend guide")
    result = await TextTransformPlanner(
        generator=qualified_guide_editor_factory({document.blocks[0].id: replacement}),
        allow_unslop_model=True,
    ).transform(document, _request(source, mode))
    assert result.document.blocks[0].text == replacement


@pytest.mark.parametrize("mode", [TransformMode.UNSLOP, TransformMode.BOTH])
@pytest.mark.parametrize("modifier", ["short", "brief", "working"])
async def test_qualified_guide_keeps_substantive_intentions(
    modifier: str, mode: TransformMode, qualified_guide_editor_factory: type[_ApprovingEditor]
) -> None:
    source = f"This {modifier} guide is intended to reduce queueing before admission."
    replacement = f"This {modifier} guide reduces queueing before admission."
    document = parse_markdown(source, title="Weekend guide")
    result = await TextTransformPlanner(
        generator=qualified_guide_editor_factory({document.blocks[0].id: replacement}),
        allow_unslop_model=True,
    ).transform(document, _request(source, mode))
    assert result.document.blocks[0].text == source
    assert any(finding.code == "force_language_changed" for finding in result.findings)
