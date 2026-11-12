from io import BytesIO
from typing import override

from docx import Document as WordDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.styles.style import ParagraphStyle
from pydantic import JsonValue
import pytest

from app.document_transform import google_gateway, markdown_import
from app.document_transform.brand import SarjBrandProfile, build_copy_mutations, paragraph_font_size
from app.document_transform.docx_renderer import markdown_import_manifest, render_markdown_docx
from app.document_transform.ir import (
    BlockKind,
    DocumentIR,
    SourceRange,
    TextBlock,
    parse_google_document,
    parse_markdown,
)
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
from app.document_transform.unslop import model_editable_blocks
from app.identifiers import DocsTabId, FileId


class _DeletingEditor(PatchGenerator):
    def __init__(self, *, equivalent: bool) -> None:
        self.equivalent = equivalent
        self.reviews = 0

    @property
    @override
    def model_ids(self) -> tuple[str, ...]:
        return ("test-editor",)

    @override
    async def generate(self, document: DocumentIR, *, phase: TransformPhase) -> list[TextPatch]:
        return [
            TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement="")
            for block in model_editable_blocks(document)
        ]

    @override
    async def review(self, document: DocumentIR, patches: list[TextPatch]) -> RewriteReview:
        self.reviews += 1
        if self.equivalent:
            return RewriteReview(frozenset(patch.block_id for patch in patches), {})
        return RewriteReview(
            frozenset(),
            {patch.block_id: "Deletion loses a concrete instruction" for patch in patches},
        )


@pytest.fixture
def deleting_editor_factory() -> type[_DeletingEditor]:
    return _DeletingEditor


@pytest.mark.parametrize(
    ("source", "equivalent"),
    [
        ("Keep the context.", True),
        (
            "The examples illustrate measurement and inspection code. They contain no measured timings, output transcript, or claim that a current machine reproduces the reference table. Numeric constants in the snippets are example parameters, not additional reference estimates.",
            True,
        ),
        (
            (
                "Keep the context. An operation, data size, and network rate belong with each "
                "estimate. Distinguish latency from throughput."
            ),
            True,
        ),
        ("Restart the service after approval.", False),
    ],
)
async def test_whole_paragraph_deletion_needs_independent_review(
    source: str, deleting_editor_factory: type[_DeletingEditor], *, equivalent: bool
) -> None:
    document = parse_markdown(source, title="Reference")
    editor = deleting_editor_factory(equivalent=equivalent)
    request = DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown",
            name="Reference",
            content=source,
            destination_folder_id=FileId("folder"),
        ),
        mode=TransformMode.UNSLOP,
    )
    result = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        document, request
    )
    assert editor.reviews > 0
    assert result.document.blocks[0].text == ("" if equivalent else source)
    assert (result.document.blocks[0].kind is BlockKind.BLANK) == equivalent


@pytest.mark.parametrize(
    "source",
    [
        "Use 10 ms as the threshold.",
        'Retain the quotation "keep context".',
        "The team must retain context.",
        "The team should retain context.",
        "Ask Atlas before proceeding.",
        "Keep [the source](https://example.com).",
        "The benchmark recorded no errors.",
        "The program must not print credentials.",
        "Retry writes using the current revision.",
    ],
)
def test_whole_paragraph_deletion_cannot_bypass_literal_or_obligation_guards(source: str) -> None:
    document = parse_markdown(source, title="Reference")
    block = document.blocks[0]
    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement="")],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )
    assert not decision.accepted


def test_native_deletion_has_no_empty_insert_or_zero_width_style_request() -> None:
    tab = DocsTabId("tab")
    original = DocumentIR(
        "Reference",
        (
            TextBlock(
                "b0",
                BlockKind.PARAGRAPH,
                "Keep the context.",
                source_range=SourceRange(1, 18, tab),
            ),
            TextBlock(
                "b1",
                BlockKind.PARAGRAPH,
                "The team retains the source.",
                source_range=SourceRange(19, 46, tab),
                authored_emphasis=(("bold", True),),
            ),
        ),
        "google_doc",
    )
    requests = build_copy_mutations(
        original,
        original.replace_blocks({"b0": ""}),
        profile=SarjBrandProfile(),
        headers=(),
        logo_uri="https://example.com/logo.png",
    )
    assert not any("insertText" in request for request in requests)
    styles = [request["updateTextStyle"] for request in requests if "updateTextStyle" in request]
    body = next(style for style in styles if "startIndex" in style["range"])
    assert body["textStyle"]["bold"] is False
    assert body["range"]["endIndex"] > body["range"]["startIndex"]
    paragraph = next(
        request["updateParagraphStyle"] for request in requests if "updateParagraphStyle" in request
    )
    assert paragraph["paragraphStyle"]["alignment"] == "JUSTIFIED"


def test_branded_docx_removes_body_bold_and_keeps_its_import_proof_consistent() -> None:
    source = "# Reference\n\n**Scattered emphasis** inside prose.\n\n- **Loud** item\n\n| Name | Value |\n| --- | --- |\n| **Cache** | 10 ms |\n\n`keep_code()`\n"
    document = WordDocument(BytesIO(render_markdown_docx(source, title="Reference")))
    normal, heading = document.styles["Normal"], document.styles["Heading 1"]
    assert isinstance(normal, ParagraphStyle)
    assert isinstance(heading, ParagraphStyle)
    assert normal.paragraph_format.alignment == WD_ALIGN_PARAGRAPH.JUSTIFY
    assert normal.font.bold is False
    assert heading.font.bold is True
    assert not any(run.bold for paragraph in document.paragraphs for run in paragraph.runs)
    assert document.tables[0].cell(1, 0).paragraphs[0].runs[0].bold is not True
    assert document.tables[0].cell(0, 0).paragraphs[0].runs[0].bold is True
    manifest = markdown_import_manifest(source, title="Reference", branded=True)
    assert all(not block.bold for block in manifest.blocks)
    assert all(not inline.bold for block in manifest.blocks for inline in block.inline_signature)
    unbranded = WordDocument(
        BytesIO(render_markdown_docx(source, title="Reference", branded=False))
    )
    assert any(run.bold for paragraph in unbranded.paragraphs for run in paragraph.runs)
    code = next(
        run
        for paragraph in document.paragraphs
        for run in paragraph.runs
        if run.text == "keep_code()"
    )
    properties = code._r.rPr
    assert properties is not None
    fonts = properties.rFonts
    assert fonts is not None
    assert fonts.get(qn("w:ascii")) == "Courier New"
    assert fonts.get(qn("w:cs")) == "Courier New"


@pytest.mark.parametrize(
    ("title", "size"),
    [("Numbers Every Programmer Should Know", 20), ("Reference", 24)],
)
def test_document_titles_use_the_same_fitted_size_in_native_and_docx_output(
    title: str, size: int
) -> None:
    source = f"# {title}\n"
    block = parse_markdown(source, title=title).blocks[0]
    document = WordDocument(BytesIO(render_markdown_docx(source, title=title)))
    run = document.paragraphs[0].runs[0]
    if size == 24:
        assert run.font.size is None
    else:
        assert run.font.size is not None
        assert run.font.size.pt == size
    assert paragraph_font_size(block, SarjBrandProfile()) == size
    manifest = markdown_import_manifest(source, title=title, branded=True)
    assert markdown_import._role_size(manifest.blocks[0]) == size


@pytest.mark.parametrize("verify_brand", [False, True])
def test_native_readback_ignores_only_empty_reviewed_paragraphs(*, verify_brand: bool) -> None:
    def raw(texts: list[str]) -> dict[str, JsonValue]:
        index = 1
        content: list[JsonValue] = []
        for text in texts:
            end = index + len(text) + 1
            content.append(
                {
                    "startIndex": index,
                    "endIndex": end,
                    "paragraph": {
                        "elements": [
                            {
                                "startIndex": index,
                                "endIndex": end,
                                "textRun": {"content": text + "\n", "textStyle": {}},
                            }
                        ]
                    },
                }
            )
            index = end
        return {
            "revisionId": "revision",
            "tabs": [
                {"tabProperties": {"tabId": "tab"}, "documentTab": {"body": {"content": content}}}
            ],
        }

    source = parse_google_document(
        raw(["Keep the context.", "The team retains the source."]), title="Reference"
    )
    expected = source.replace_blocks({"b0": ""})
    actual = parse_google_document(raw(["", "The team retains the source."]), title="Reference")
    assert google_gateway._typography_content_preserved(actual, expected, verify_brand=verify_brand)
    verified = google_gateway._typography_verification_ir(actual, expected)
    assert verified.blocks[0].id == expected.blocks[1].id
    missing = parse_google_document(raw(["", "The team retains nothing."]), title="Reference")
    assert not google_gateway._typography_content_preserved(
        missing, expected, verify_brand=verify_brand
    )


def test_presentation_commentary_can_disappear_without_losing_the_substantive_sentence() -> None:
    source = "The table retains each operation. The article provides explanatory prose with particular attention to presentation continuity."
    document = parse_markdown(source, title="Reference")
    block = document.blocks[0]
    replacement = "The table retains each operation."
    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )
    assert decision.accepted == {block.id: replacement}
