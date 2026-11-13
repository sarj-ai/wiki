from io import BytesIO

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from pydantic import JsonValue
import pytest

from app.document_transform.docx_renderer import render_markdown_docx
from app.document_transform.readability_json import array, bodies, content, obj
from app.document_transform.readability_tables import plan_readable_tables, readable_table_requests
from app.document_transform.table_layout import numeric_table_value
from tests.test_table_layout import _native_table


@pytest.mark.parametrize(
    "quantity",
    [
        "20 ℃",
        "68 ℉",
        "293.15 K",
        "293.15 K",
        "−20.00 ° C",
        "20 ° c",
        "68 °\u202fF",
        "20 ℃ ± 0.2 ℃",
        "20 ° C to 25 ° C",
        "3 W m⁻¹ K⁻¹",
        "0.05 ℃/s",
        "3 W/℃",
        "20 ℃²",
        "3 W/° C",
        "3 W/(m ° C)",
        "<= 10 ms",
        ">= 99.90%",
        "1.234.567",
        "1.234.567 ms",
        "1'234.50 EUR",
        "1’234’567.00 CHF",
        "١٬٢٣٤٫٥٠ ms",
        "۱۲٬۳۴۵٫۶۷ ms",
        "1’234’567,00 CHF",
        "±١٬٢٣٤٫٥٠ ms",
        "1.234.567 ms +/- ١٫٠ ms",
    ],
)
def test_locale_quantities_keep_their_exact_text_and_numeric_alignment(quantity: str) -> None:
    assert numeric_table_value(quantity)
    markdown = f"| Operation | Quantity |\n| --- | --- |\n| Cache | {quantity} |\n| Disk | — |"
    word = Document(BytesIO(render_markdown_docx(markdown, title="Locale quantities")))
    cell = word.tables[0].cell(1, 1)
    assert cell.text == quantity
    assert cell.paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.RIGHT

    native = _native_table((("Operation", "Quantity"), ("Cache", quantity), ("Disk", "—")))
    table = obj(obj(content(next(bodies(native))[1])[0])["table"])
    obj(array(table["tableRows"])[0])["tableRowStyle"] = {"tableHeader": True}
    targets = plan_readable_tables(native)
    assert len(targets) == 1
    assert targets[0].numeric == {1}
    requests = readable_table_requests(native, targets)
    alignments: list[JsonValue] = [
        obj(obj(request["updateParagraphStyle"])["paragraphStyle"])["alignment"]
        for request in requests
        if "updateParagraphStyle" in request
    ]
    assert alignments == ["START", "END"] * 3


@pytest.mark.parametrize(
    "text",
    [
        "v1.234.567",
        "1.2.3",
        "192.168.1.1",
        "1'23.50",
        "١٬٢٣٫٥٠",
        "<= latency",
        "20 °",
        "20 ° Q",
        "20 ℃ sensor",
        "20 °\nC",
    ],
)
def test_identifiers_and_malformed_grouping_remain_text(text: str) -> None:
    assert not numeric_table_value(text)
