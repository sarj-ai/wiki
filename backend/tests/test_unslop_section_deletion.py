from io import BytesIO

from docx import Document
import pytest

from app.document_transform.docx_renderer import render_markdown_docx
from app.document_transform.ir import BlockKind, parse_markdown, rewrite_markdown
from app.document_transform.planner import TextTransformPlanner
from app.document_transform.schemas import DocumentTransformRequest
from app.document_transform.unslop import emptied_section_headings
from tests.test_ruthless_unslop import _DeletingEditor


@pytest.fixture
def editor() -> _DeletingEditor:
    return _DeletingEditor(equivalent=True)


async def test_reviewed_filler_removes_its_generic_heading_and_preserves_title_facts_and_code(
    editor: _DeletingEditor,
):
    source = "# Overview\n\n## Observations\n\nThe queue contains 640 jobs.\n\n```text\nkeep  spaces\n```\n\n## Closing thoughts\n\nEvery detail matters.\n"
    original = parse_markdown(source, title="Overview")
    request = DocumentTransformRequest.model_validate(
        {
            "mode": "both",
            "source": {
                "kind": "markdown",
                "content": source,
                "name": "Overview",
                "destination_folder_id": "fixture",
            },
        }
    )
    result = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        original, request
    )
    rewritten = rewrite_markdown(source, original, result.document)
    assert editor.reviews > 0
    assert (
        rewritten
        == "# Overview\n\n## Observations\n\nThe queue contains 640 jobs.\n\n```text\nkeep  spaces\n```\n\n"
    )
    assert result.deterministic_count == 1
    assert original.blocks[0] == result.document.blocks[0]
    rendered = Document(BytesIO(render_markdown_docx(rewritten, title="Overview")))
    assert [p.text for p in rendered.paragraphs] == [
        "Overview",
        "Observations",
        "The queue contains 640 jobs.",
        "keep  spaces",
    ]


@pytest.mark.parametrize(
    "source",
    [
        "# Report\n\n## Limits\n\nFiller.\n",
        "# Report\n\n## Closing thoughts\n\nFiller.\n\n```text\nkeep code\n```\n",
        "# Report\n\n## Closing thoughts\n\nFiller.\n\n> Preserve this quote.\n",
        "# Report\n\n## Closing thoughts\n\nFiller.\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n",
    ],
)
def test_section_cleanup_preserves_meaningful_headings_and_surviving_content(source: str):
    original = parse_markdown(source, title="Report")
    current = original.replace_blocks(
        {block.id: "" for block in original.blocks if block.text == "Filler."}
    )
    assert emptied_section_headings(original, current) == ()


def test_empty_nested_generic_sections_and_existing_empty_generic_headings_are_pruned():
    source = "# Report\n\n## Summary\n\n### Final thoughts\n\nFiller.\n\n## Overview\n"
    original = parse_markdown(source, title="Report")
    current = original.replace_blocks(
        {block.id: "" for block in original.blocks if block.text == "Filler."}
    )
    selected = emptied_section_headings(original, current)
    assert [block.text for block in original.blocks if block.id in selected] == [
        "Summary",
        "Final thoughts",
        "Overview",
    ]


def test_short_paragraph_line_deletion_preserves_windows_line_endings():
    source = "First fact.\r\n\r\nFiller.\r\n\r\nLast fact.\r\n"
    original = parse_markdown(source, title="Report")
    current = original.replace_blocks(
        {block.id: "" for block in original.blocks if block.text == "Filler."}
    )
    assert rewrite_markdown(source, original, current) == "First fact.\r\n\r\nLast fact.\r\n"
    assert [b.text for b in current.blocks if b.kind is BlockKind.PARAGRAPH] == [
        "First fact.",
        "Last fact.",
    ]
