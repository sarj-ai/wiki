from io import BytesIO

from docx import Document
import pytest

from app.document_transform.docx_renderer import render_markdown_docx


@pytest.mark.parametrize("branded", [False, True])
def test_short_procedure_keeps_its_prerequisite_and_steps_together(branded: bool) -> None:
    source = (
        "# Procedure\n\nObtain a source revision before starting.\n\n"
        "1. Read both tabs.\n2. If either tab changed, stop without writing.\n"
        "3. Otherwise write once.\n\nRead back before retrying."
    )
    document = Document(BytesIO(render_markdown_docx(source, title="Procedure", branded=branded)))
    paragraphs = {paragraph.text: paragraph for paragraph in document.paragraphs}

    assert tuple(paragraphs) == (
        "Procedure",
        "Obtain a source revision before starting.",
        "Read both tabs.",
        "If either tab changed, stop without writing.",
        "Otherwise write once.",
        "Read back before retrying.",
    )
    for text in (
        "Obtain a source revision before starting.",
        "Read both tabs.",
        "If either tab changed, stop without writing.",
    ):
        assert (paragraphs[text].paragraph_format.keep_with_next is True) is branded
    assert paragraphs["Otherwise write once."].paragraph_format.keep_with_next is not True


@pytest.mark.parametrize("case", ["many", "wide", "multiline", "nested", "mixed"])
def test_large_or_complex_lists_are_not_forced_into_an_oversized_group(case: str) -> None:
    match case:
        case "many":
            source = "\n".join(f"{index}. Step {index}." for index in range(1, 9))
        case "wide":
            source = "1. " + "x" * 241 + "\n2. Continue."
        case "multiline":
            source = "1. First line.  \n   Continuation.\n2. Continue."
        case "nested":
            source = "1. First.\n   1. Nested.\n2. Continue."
        case _:
            source = "1. First.\n\n- An independent fact."
    document = Document(BytesIO(render_markdown_docx(source, title="Procedure")))

    assert all(
        paragraph.paragraph_format.keep_with_next is not True
        for paragraph in document.paragraphs[1:]
    )
