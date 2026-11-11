from copy import deepcopy

from pydantic import JsonValue
import pytest

from app.document_transform.readability_json import array, bodies, content, obj
from app.document_transform.readability_tables import plan_readable_tables, readable_table_requests
from tests.test_table_layout import _native_table


@pytest.mark.parametrize("first_value", ["N/A", "", "—", "Pending", "1.2300 ms"])
def test_body_values_do_not_authorize_header_formatting(first_value: str) -> None:
    document = _native_table((("Pending", first_value), ("Ready", "1/2 µs")))
    original = deepcopy(document)

    target = plan_readable_tables(document)[0]
    requests = readable_table_requests(document, (target,))

    assert not target.header
    assert not any(
        "bold" in obj(obj(request["updateTextStyle"])["textStyle"])
        for request in requests
        if "updateTextStyle" in request
    )
    assert document == original


@pytest.mark.parametrize("first_value", ["Not measured", "2026-11-11", "v1.02.3"])
def test_body_column_classification_includes_its_first_row(first_value: str) -> None:
    document = _native_table((("Pending", first_value), ("Ready", "1.2300 ms")))

    target = plan_readable_tables(document)[0]

    assert not target.header
    assert not target.numeric


def test_single_body_row_can_align_its_authored_quantity() -> None:
    document = _native_table((("Ready", "1.2300 ms"),))

    target = plan_readable_tables(document)[0]

    assert not target.header
    assert target.numeric == {1}


def test_explicit_native_header_excludes_labels_from_numeric_classification() -> None:
    document = _native_table((("Status", "Latency"), ("Ready", "1.2300 ms")))
    table = obj(obj(content(next(bodies(document))[1])[0])["table"])
    obj(array(table["tableRows"])[0])["tableRowStyle"] = {"tableHeader": True}

    target = plan_readable_tables(document)[0]

    assert target.header
    assert target.header_rows == 1
    assert target.numeric == {1}


def test_unclassified_first_row_keeps_authored_emphasis() -> None:
    document = _native_table((("Important", "Pending"), ("Ready", "1.2300 ms")))
    table = obj(obj(content(next(bodies(document))[1])[0])["table"])
    cell = obj(array(obj(array(table["tableRows"])[0])["tableCells"])[0])
    paragraph = obj(obj(array(cell["content"])[0])["paragraph"])
    style = obj(obj(obj(array(paragraph["elements"])[0])["textRun"])["textStyle"])
    style["bold"] = True
    original: dict[str, JsonValue] = deepcopy(document)

    target = plan_readable_tables(document)[0]
    fonts = [
        obj(request["updateTextStyle"])
        for request in readable_table_requests(document, (target,))
        if "updateTextStyle" in request
    ]

    assert not target.header
    assert all("bold" not in str(font["fields"]) for font in fonts)
    assert document == original
