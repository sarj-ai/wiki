from io import BytesIO

from docx import Document
import pytest

from app.document_transform.docx_renderer import render_markdown_docx


@pytest.mark.parametrize(
    "quotation",
    [
        "Preserve this quoted decision.",
        "“The workers is ready.”",
        "Keep **bold** and *italic* source emphasis.",
    ],
)
def test_unbranded_quote_has_importable_indent_without_changing_body(quotation: str) -> None:
    rendered = render_markdown_docx(
        f"Before the quotation.\n\n> {quotation}\n\nAfter the quotation.",
        title="Quote import QA",
        branded=False,
    )
    title, before, quote, after = Document(BytesIO(rendered)).paragraphs

    # Google import drops the built-in Quote style's inherited indentation.
    # Without a direct positive indent, the native structure gate sees body prose.
    indent = quote.paragraph_format.left_indent
    assert indent is not None
    assert indent.pt == 18
    assert quote.style is not None
    assert quote.style.font.italic is True
    assert quote.text == quotation.replace("**", "").replace("*", "")
    assert title.text == "Quote import QA"
    assert before.text == "Before the quotation."
    assert after.text == "After the quotation."
    assert before.paragraph_format.left_indent is None
    assert after.paragraph_format.left_indent is None
