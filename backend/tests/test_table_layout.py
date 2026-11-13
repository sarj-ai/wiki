from io import BytesIO

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from pydantic import JsonValue
import pytest

from app.document_transform.docx_renderer import render_markdown_docx
from app.document_transform.ir import utf16_length
from app.document_transform.readability_json import array, bodies, content, obj
from app.document_transform.readability_tables import plan_readable_tables, readable_table_requests
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
        "± 0.05 s",
        "±0.05",
        "± 3,50 ms",
        "± 1.20e-3 s",
        "± 2 1/3 m",
        "± 0.5%",
        "($1,234.50)",
        "≥ 99.9%",
        "1:2",
        "1–10 GB",
        "1\u202f000 ms",
        "2.5 SAR",
        "SAR 1,250.00",
        "USD 1,234.50",
        "EUR 1.234,50",
        "GBP 250",
        "SAR −1,250.00",
        "−SAR 1,250.00",
        "(USD 1,234.50)",
        "6.02 × 10²³ mol⁻¹",
        "1.20 × 10^-7 s",
        "2.50 x 10**6 B",
        "10⁻⁹ s",
        "1½ kg",
        "1 ½ kg",
        "2 1/3 m",
        "1/-2",
        "1.20e-7/2",
        "−$1,250.00",
        "$−1,250.00",
        "₹ 1,250.00",
        "₹1,25,000.00",
        "12,34,567.89",
        "1,23,456",
        "1.234(5) m",
        "9.81 m/s²",
        "1.00 kg·m/s^2",
        "12.3 ± 0.4 m/s",
        "1.2e-7–2.4e-7 s",
        "1,25 m",
        "1.234,50 EUR",
        "1\u00a0234,50 EUR",
        "0,125 ± 0,004 m/s²",
        "9.81 m / s²",
        "3 kg · m / s²",
        "3 kg * m / s²",
        "3 m ^ 2",
        "3 m ** 2",
        "3 m ^ -2",
        "9.81 m s^-2",
        "3 kg m s⁻²",
        "12.3 ms ± 0.4 ms",
        "1.2 ms–2.4 ms",
        "Approximately 2 GiB",
        "−3 to −1 °C",
        "about 1.2300 ms",
        "roughly 1/2 µs",
        "Approx. 2 GiB",
        "10 ms to 12 ms",
        "approximately −3 to −1 °C",
        "50 Mbit/s",
        "200 Mbit/s",
        "100 Mbit/s",
        "1 bit/s",
        "64 Kibit/s",
        "2 Gbit/s",
        "<= 10 ms",
        ">= 99.9%",
        "1'234.50 EUR",
        "1’234,50 EUR",
        "1’234’567.80 ms",
        "CHF 1'234.50",
        "1’234,50 CHF",
        "1.20e-3 +/- 0.04e-3 s",
        pytest.param("١٢٫٣٪", id="arabic-percent"),
        pytest.param("۱۲٫۳۴۰۰٪", id="persian-percent"),
        pytest.param("12٫3٪", id="western-digits-arabic-percent"),
        pytest.param("≤ ١٢٫٣٪", id="arabic-percent-bound"),
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
    native = _native_table(rows)
    native_table = obj(obj(content(next(bodies(native))[1])[0])["table"])
    obj(array(native_table["tableRows"])[0])["tableRowStyle"] = {"tableHeader": True}
    targets = plan_readable_tables(native)
    assert len(targets) == 1
    assert targets[0].numeric == {1}
    assert targets[0].header
    assert sum(targets[0].widths) == 9360
    requests = readable_table_requests(native, targets)
    assert all(
        next(iter(request))
        in {
            "updateTableCellStyle",
            "updateTableColumnProperties",
            "updateParagraphStyle",
            "updateTextStyle",
        }
        for request in requests
    )
    alignment = [
        obj(obj(request["updateParagraphStyle"])["paragraphStyle"])["alignment"]
        for request in requests
        if "updateParagraphStyle" in request
    ]
    assert alignment == ["START", "END", "START"] * len(rows)


def _native_table(rows: tuple[tuple[str, ...], ...]) -> dict[str, JsonValue]:
    index = 2
    native_rows: list[JsonValue] = []
    for row in rows:
        cells: list[JsonValue] = []
        index += 1
        for text in row:
            index += 1
            end = index + utf16_length(text) + 1
            cells.append(
                {
                    "content": [
                        {
                            "startIndex": index,
                            "endIndex": end,
                            "paragraph": {
                                "paragraphStyle": {},
                                "elements": [
                                    {
                                        "startIndex": index,
                                        "endIndex": end,
                                        "textRun": {"content": text + "\n", "textStyle": {}},
                                    }
                                ],
                            },
                        }
                    ]
                }
            )
            index = end
        native_rows.append({"tableCells": cells})
    return {
        "tabs": [
            {
                "tabProperties": {"tabId": "t.0"},
                "documentTab": {
                    "body": {
                        "content": [
                            {
                                "startIndex": 1,
                                "endIndex": index + 1,
                                "table": {"columns": len(rows[0]), "tableRows": native_rows},
                            }
                        ]
                    }
                },
            }
        ]
    }


@pytest.mark.parametrize(
    "text",
    [
        "2026-11-10",
        "v1.2.3",
        "RFC 9110",
        "1.2.3",
        "A123",
        "12 apples",
        "12,34.56.78",
        "",
        "N/A",
        "6.02 × 10",
        "1.2 × 10^",
        "1.2 × 10⁻",
        "1.2 × 10^3 apples",
        "-$-1.00",
        "1.234(note) m",
        "10 m/unknown",
        "1.2e",
        "12,34,56",
        "12.34.56",
        "3 m under load",
        "3 m in May",
        "3 m moving",
        "3 m / sample",
        "SAR rollout",
        "SAR123",
        "USD -$1.00",
        "12 USD notes",
        "Approximately 2 tasks remain",
        "2 GiB to warm the cache",
        "The cache takes approximately 2 GiB.",
        "Mbit/s",
        "50 Mbit/s extra",
        "1 bit flip",
        "50 Mbit/s; retry",
        "2 requests/s",
        "1'23.45 EUR",
        "1’234’56 ms",
        "<= 10 ms under load",
        ">=99.9% of requests",
        "<< 10 ms",
        "><10 ms",
        "1.2 +/ 0.4 ms",
        "CHF rollout",
        "CHF123",
    ],
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


@pytest.mark.parametrize("available", [8320, 9360, 12960])
def test_narrow_tables_reserve_font_sized_space_for_headers_and_uncertain_quantities(
    available: int,
) -> None:
    rows = (
        ("Measurement", "Value", "Notes"),
        ("Uncertainty", "12.3 ± 0.4 ms", "Quoted interval, not a new estimate."),
        ("Currency", "$1,250.00", "The amount includes two decimal places."),
    )

    widths = table_column_widths(rows, total=available)

    assert widths[0] >= 80 * 20
    assert widths[1] >= 80 * 20
    assert widths[2] > max(widths[:2])
    assert sum(widths) == available
