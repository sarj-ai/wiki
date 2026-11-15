from io import BytesIO

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
import pytest

from app.document_transform.docx_renderer import render_markdown_docx
from app.document_transform.ir import parse_markdown
from app.document_transform.readability_tables import plan_readable_tables
from app.document_transform.safety import rewrite_constraints, validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase
from app.document_transform.table_layout import numeric_table_value
from tests.test_table_layout import _native_table


@pytest.mark.parametrize(
    "quantity",
    [
        "(1.20 ± 0.04) mg",
        "(1.20 +/- 0.04) μg",
        "(1,20 +/− 0,04) mg/L",
        "(1.20e-3 ± 0.04e-3) kg",
        "(12.30 ± 0.40) m s⁻²",
        "(1/2 ± 1/10) μL",
        "(−1.20 ± 0.04) °C",
        "(١٢٫٣ ± ٠٫٤) mg",
    ],
)
def test_grouped_uncertainty_aligns_without_changing_values(quantity: str) -> None:
    assert numeric_table_value(quantity)
    target = plan_readable_tables(_native_table((("Sample", quantity), ("Missing", "—"))))[0]
    assert target.numeric == {1}
    markdown = f"| Sample | Mass |\n| --- | --- |\n| A | {quantity} |\n| B | — |"
    rendered = Document(BytesIO(render_markdown_docx(markdown, title="Measurements")))
    cell = rendered.tables[0].cell(1, 1)
    assert cell.text == quantity
    assert cell.paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.RIGHT
    source = f"The team measured the sample at {quantity}."
    assert rewrite_constraints(source).protected_tokens == (quantity,)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("(1.20 ± 0.04) mg", "(1.20 ± 0.04) g"),
        ("(1.20 ± 0.04) mg/L", "(1.20 ± 0.04) mg"),
        ("(1.20 ± 0.04) mg", "(1.2 ± 0.04) mg"),
        ("(1.20 ± 0.04) mg", "(1.20 ± 0.4) mg"),
        ("(1.20 ± 0.04) mg and (2.30 ± 0.05) g", "(1.20 ± 0.04) g and (2.30 ± 0.05) mg"),
    ],
)
def test_grouped_uncertainty_keeps_units_precision_and_attachment(before: str, after: str) -> None:
    document = parse_markdown(
        f"The sample was measured by the team at {before}.", title="Measurements"
    )
    block = document.blocks[0]
    patch = TextPatch(
        block_id=block.id,
        expected_sha256=block.sha256,
        replacement=f"The team measured the sample at {after}.",
    )
    result = validate_patches(
        document, [patch], phase=TransformPhase.UNSLOP, aggressive_unslop=True
    )
    assert not result.accepted
    assert {finding.code for finding in result.findings} == {"protected_content_changed"}
    faithful = patch.model_copy(
        update={"replacement": f"The team measured the sample at {before}."}
    )
    assert validate_patches(
        document, [faithful], phase=TransformPhase.UNSLOP, aggressive_unslop=True
    ).accepted == {block.id: faithful.replacement}


@pytest.mark.parametrize(
    "value",
    [
        "(1.20 ± 0.04) mg observed",
        "(1.20 ± 0.04) apples",
        "(1.20 ± 0.04) μG",
        "(1.20 ± 0.04 mg",
        "1.20 ± 0.04) mg",
        "(1.20 ± 0.04)\nmg",
    ],
)
def test_grouped_uncertainty_does_not_consume_prose_or_malformed_groups(value: str) -> None:
    assert not numeric_table_value(value)


def test_grouped_uncertainty_does_not_protect_an_unrelated_following_word() -> None:
    assert rewrite_constraints("The team measured (1.20 ± 0.04) as planned.").protected_tokens == (
        "(1.20 ± 0.04)",
    )
