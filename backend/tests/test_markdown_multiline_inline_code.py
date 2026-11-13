from io import BytesIO

from docx import Document
import pytest

from app.document_transform.docx_renderer import render_markdown_docx
from app.document_transform.ir import parse_markdown, rewrite_markdown
from app.document_transform.planner import TextTransformPlanner
from app.document_transform.safety import validate_patches
from app.document_transform.schemas import DocumentTransformRequest, TextPatch, TransformPhase
from app.document_transform.unslop import model_editable_blocks
from tests.test_document_transform import _Generator


_LITERALS = (
    "`alpha\nMoreover, the operator acts in order to retain the sample.\nomega`",
    "``echo `alpha`\nMoreover, the operator acts in order to retain 🧪 and é.\nomega``",
    "`echo \"alpha\"\nMoreover, the operator acts in order to retain 'omega'.\n--dry-run`",
)
_OUTSIDE = "Moreover, the operator stores the report."


@pytest.fixture
def generator() -> _Generator:
    return _Generator()


@pytest.mark.parametrize("literal", _LITERALS)
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("mode", ["unslop", "both"])
async def test_multiline_inline_code_survives_the_real_planner_and_docx_renderer(
    literal: str, newline: str, mode: str, generator: _Generator
) -> None:
    source = f"The literal is {literal} in this example.\n\n{_OUTSIDE}\n".replace("\n", newline)
    original = parse_markdown(source, title="Inline code")
    request = DocumentTransformRequest.model_validate(
        {
            "mode": mode,
            "source": {
                "kind": "markdown",
                "name": "Inline code",
                "content": source,
                "destination_folder_id": "qa-folder",
            },
        }
    )

    result = await TextTransformPlanner(generator=generator).transform(original, request)
    rewritten = rewrite_markdown(source, original, result.document)

    assert rewritten == source.replace(_OUTSIDE, "The operator stores the report.")
    assert not generator.calls
    rendered = Document(BytesIO(render_markdown_docx(rewritten, title="Inline code")))
    code = [
        run.text
        for paragraph in rendered.paragraphs
        for run in paragraph.runs
        if run.font.name == "Courier New"
    ]
    assert any("in order to retain" in text for text in code)


@pytest.mark.parametrize("literal", _LITERALS)
def test_model_patches_cannot_rewrite_a_middle_line_of_multiline_inline_code(literal: str) -> None:
    original = parse_markdown(f"The literal is {literal}.\n\n{_OUTSIDE}", title="Inline code")
    block = next(block for block in original.blocks if "in order to" in block.text)

    decision = validate_patches(
        original,
        [
            TextPatch(
                block_id=block.id,
                expected_sha256=block.sha256,
                replacement=block.text.replace("in order to", "to"),
            )
        ],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["protected_block"]
    assert all("in order to" not in block.text for block in model_editable_blocks(original))


@pytest.mark.parametrize(
    "source",
    [
        f"A stray ` opener remains.\n\n{_OUTSIDE}\n\nA stray ` closer remains.",
        f"The example includes an escaped \\` mark.\n{_OUTSIDE}\nAn escaped \\` mark ends it.",
        f"The example uses `alpha`.\n{_OUTSIDE}\nThe example uses `omega`.",
        f"A stray ` appears.\n\n```text\n` exact code\n```\n\n{_OUTSIDE}",
    ],
    ids=["paragraph-boundaries", "escaped-openers", "single-line-literals", "fenced-code"],
)
def test_unrelated_prose_stays_editable_across_markdown_boundaries(source: str) -> None:
    document = parse_markdown(source, title="Inline code boundaries")

    assert any(block.text == _OUTSIDE for block in model_editable_blocks(document))


def test_multiline_code_in_a_list_preserves_the_next_item_as_editable_prose() -> None:
    source = (
        f"1. Retain `alpha\n   in order to retain the sample\n   omega` unchanged.\n2. {_OUTSIDE}"
    )
    document = parse_markdown(source, title="Code in a list")
    editable = model_editable_blocks(document)

    assert tuple(block.text for block in editable) == (_OUTSIDE,)
