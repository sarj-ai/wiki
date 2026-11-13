from copy import deepcopy

from pydantic import JsonValue
import pytest

from app.document_transform.readability_json import array, bodies, content, obj
from app.document_transform.readability_tables import plan_readable_tables, readable_table_requests
from tests.test_table_layout import _native_table


@pytest.mark.parametrize("missing", ["", "-", "–", "—", "N/A", "na", "NULL", "…"])
def test_missing_first_measurement_does_not_turn_a_body_row_into_a_header(missing: str) -> None:
    document = _native_table((("Cache", missing), ("Disk", "10 ms"), ("Network", "20 ms")))
    original = deepcopy(document)

    target = plan_readable_tables(document)[0]
    requests = readable_table_requests(document, (target,))

    assert not target.header
    assert target.numeric == {1}
    assert all(
        obj(obj(request["updateTextStyle"])["textStyle"])["bold"] is False
        for request in requests
        if "updateTextStyle" in request
    )
    assert not any("pinTableHeaderRows" in request for request in requests)
    assert document == original


@pytest.mark.parametrize("missing", ["", "N/A", "—"])
def test_explicit_native_header_remains_a_header_even_with_a_missing_label(missing: str) -> None:
    document = _native_table((("Operation", missing), ("Disk", "10 ms")))
    table = obj(obj(content(next(bodies(document))[1])[0])["table"])
    obj(array(table["tableRows"])[0])["tableRowStyle"] = {"tableHeader": True}

    target = plan_readable_tables(document)[0]

    assert target.header
    assert target.header_rows == 1
    assert target.numeric == {1}


def test_label_like_body_values_do_not_authorize_new_header_emphasis() -> None:
    document = _native_table((("Operation", "Latency"), ("Disk", "10 ms")))

    target = plan_readable_tables(document)[0]
    requests: list[dict[str, JsonValue]] = readable_table_requests(document, (target,))

    assert not target.header
    assert all(
        obj(obj(request["updateTextStyle"])["textStyle"])["bold"] is False
        for request in requests
        if "updateTextStyle" in request
    )
