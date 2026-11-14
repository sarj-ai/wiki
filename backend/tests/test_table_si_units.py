from io import BytesIO

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
import pytest

from app.document_transform.docx_renderer import render_markdown_docx
from app.document_transform.readability_json import obj
from app.document_transform.readability_tables import plan_readable_tables, readable_table_requests
from app.document_transform.safety import rewrite_constraints
from app.document_transform.table_layout import numeric_table_value
from tests.test_table_layout import _native_table


@pytest.mark.parametrize(
    "quantity",
    [
        "5 μg",
        "5 µg",
        "2 nm",
        "2.00 pF",
        "0.125 ± 0.004 μg",
        "1.20e-6 nF",
        "1/2 μL",
        "3 pm",
        "2.00 GΩ",
        "10 daPa",
        "3 mSv",
        "2 kat",
        "5 kJ/(kg K)",
        "12.3 μmol m⁻² s⁻¹",
        "1 nm–2 nm",
        "QAR 1.2300",
    ],
)
def test_prefixed_si_quantities_align_in_native_and_imported_tables(quantity: str) -> None:
    rows = (("Mass", quantity), ("Missing", "—"))
    target = plan_readable_tables(_native_table(rows))[0]
    assert target.numeric == {1}
    alignments = [
        obj(obj(request["updateParagraphStyle"])["paragraphStyle"])["alignment"]
        for request in readable_table_requests(_native_table(rows), (target,))
        if "updateParagraphStyle" in request
    ]
    assert alignments == ["START", "END"] * len(rows)
    source = f"| Property | Reading |\n| --- | --- |\n| Mass | {quantity} |\n| Missing | — |"
    document = Document(BytesIO(render_markdown_docx(source, title="SI measurements")))
    cell = document.tables[0].cell(1, 1)
    assert cell.text == quantity
    assert cell.paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.RIGHT


@pytest.mark.parametrize("quantity", ["2 nf", "5 UG", "3 gω", "5 μg observed", "12 apples"])
def test_new_unit_symbols_remain_case_sensitive_and_do_not_consume_prose(quantity: str) -> None:
    assert not numeric_table_value(quantity)


def test_prefixed_si_symbols_do_not_absorb_a_following_prose_clause() -> None:
    assert rewrite_constraints("The team measured 3 m as recorded.").protected_tokens == ("3 m",)
    assert rewrite_constraints("The team measured 5 kJ/(kg K).").protected_tokens == (
        "5 kJ/(kg K)",
    )
