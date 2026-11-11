from copy import deepcopy

from pydantic import JsonValue
import pytest

from app.document_transform.readability_json import array, bodies, content, obj
from app.document_transform.readability_tables import plan_readable_tables, readable_table_requests
from app.document_transform.table_layout import table_column_widths
from tests.test_table_layout import _native_table


@pytest.mark.parametrize(
    ("rows", "numeric"),
    [
        ((("1/3 s", "12.50%", "€1.234,50"),), {0, 1, 2}),
        ((("Latency", "1.20e-7 s", "N/A"),), {1}),
        ((("1/2 µs", "12.500%"), ("3/4 µs", "15.000%")), {0, 1}),
        ((("Cache", "Read only"),), set()),
    ],
)
def test_body_only_tables_classify_every_row_without_inventing_a_header(
    rows: tuple[tuple[str, ...], ...], numeric: set[int]
) -> None:
    document = _native_table(rows)
    original = deepcopy(document)

    targets = plan_readable_tables(document)

    assert len(targets) == 1
    target = targets[0]
    assert not target.header
    assert target.numeric == numeric
    assert target.widths == table_column_widths(rows, total=9360, header_rows=0)
    requests = readable_table_requests(document, targets)
    alignments: list[JsonValue] = [
        obj(obj(request["updateParagraphStyle"])["paragraphStyle"])["alignment"]
        for request in requests
        if "updateParagraphStyle" in request
    ]
    assert alignments == [
        "END" if column in numeric else "START" for _ in rows for column in range(len(rows[0]))
    ]
    assert not any(
        "bold" in obj(obj(request["updateTextStyle"])["textStyle"])
        for request in requests
        if "updateTextStyle" in request
    )
    assert document == original


def test_explicit_single_header_row_remains_a_header() -> None:
    document = _native_table((("Latency", "Measurement"),))
    table = obj(content(next(bodies(document))[1])[0])
    obj(array(obj(table["table"])["tableRows"])[0])["tableRowStyle"] = {"tableHeader": True}

    target = plan_readable_tables(document)[0]

    assert target.header
    assert target.header_rows == 1
    assert not target.numeric
