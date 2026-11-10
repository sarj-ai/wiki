from io import BytesIO

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
import pytest

from app.document_transform.docx_renderer import render_markdown_docx
from app.document_transform.table_layout import (
    numeric_columns,
    numeric_table_value,
    table_column_widths,
)


@pytest.mark.parametrize(
    "quantity",
    [
        "1/2",
        "½",
        "1⁄3",
        "-0.005",
        "1.20e-7 s",
        "10 ± 2 ms",
        "($1,234.50)",
        "≥ 99.9%",
        "1:2",
        "1–10 GB",
        "1\u202f000 ms",
        "2.5 SAR",
    ],
)
def test_quantities_use_numeric_alignment_without_rewriting_their_representation(
    quantity: str,
) -> None:
    rows = (
        ("Operation", "Measurement", "Notes"),
        ("Cache", quantity, "Retain the precision and notation of the measurement"),
        ("Disk", "—", "Missing values do not turn quantities into a text column"),
    )
    assert numeric_table_value(quantity)
    assert numeric_columns(rows) == {1}
    widths = table_column_widths(rows, total=9360)
    assert sum(widths) == 9360
    assert widths[2] > widths[1]
    source = f"| Operation | Measurement | Notes |\n| --- | --- | --- |\n| Cache | {quantity} | Keep precision |\n| Disk | — | Missing |"
    document = Document(BytesIO(render_markdown_docx(source, title="Measurements")))
    assert document.tables[0].cell(1, 1).text == quantity
    assert document.tables[0].cell(1, 1).paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.RIGHT


@pytest.mark.parametrize(
    "text",
    ["2026-11-10", "v1.2.3", "RFC 9110", "1.2.3", "A123", "12 apples", "12,34.56.78", "", "N/A"],
)
def test_dates_versions_identifiers_and_unrecognized_quantities_are_not_numbers(text: str) -> None:
    assert not numeric_table_value(text)


@pytest.mark.parametrize("columns", range(2, 12))
@pytest.mark.parametrize(
    "width", [3200, 4680, 7200, 9360, 12000, 14400, 16000, 20000, 24000, 30000]
)
def test_100_table_geometries_fit_the_available_width(columns: int, width: int) -> None:
    rows = tuple(
        tuple(
            (
                "A very long description with readable word wrapping"
                if column == 0
                else str(row * column)
            )
            for column in range(columns)
        )
        for row in range(3)
    )
    widths = table_column_widths(rows, total=width)
    assert len(widths) == columns
    assert min(widths) > 0
    assert sum(widths) == width
    assert widths[0] > widths[-1]
