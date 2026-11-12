from copy import deepcopy

from pydantic import JsonValue
import pytest

from app.document_transform.readability import plan_readability
from app.document_transform.readability_json import array, obj
from app.document_transform.readability_tables import plan_readable_tables
from tests.test_document_readability import _fixture, _LayoutEditor, _paragraph


def test_verified_table_layout_does_not_produce_another_write() -> None:
    document = _fixture("after")
    original = deepcopy(document)

    assert not plan_readable_tables(document)
    assert document == original


async def test_converged_table_only_run_has_no_checkpoint_or_requests() -> None:
    editor = _LayoutEditor()
    editor.supports_readability_planning = False

    assert await plan_readability(_fixture("after"), title="Repeat", generator=editor) is None
    assert editor.calls == 0


@pytest.mark.parametrize("field", ["font", "size", "alignment", "width", "padding", "shading"])
def test_actual_table_drift_still_receives_a_plan(field: str) -> None:
    document = _fixture("after")
    table = obj(_paragraph(document, 247)["table"])
    cell = obj(array(obj(array(table["tableRows"])[1])["tableCells"])[0])
    paragraph = obj(obj(array(cell["content"])[0])["paragraph"])
    style = obj(obj(obj(array(paragraph["elements"])[0])["textRun"])["textStyle"])
    match field:
        case "font":
            style["weightedFontFamily"] = {"fontFamily": "Arial"}
        case "size":
            style["fontSize"] = {"magnitude": 13, "unit": "PT"}
        case "alignment":
            obj(paragraph["paragraphStyle"])["alignment"] = "CENTER"
        case "width":
            column = obj(array(obj(table["tableStyle"])["tableColumnProperties"])[0])
            column["width"] = {"magnitude": 100, "unit": "PT"}
        case "padding":
            obj(cell["tableCellStyle"])["paddingTop"] = {"magnitude": 8, "unit": "PT"}
        case _:
            obj(cell["tableCellStyle"])["backgroundColor"] = {}
    original: dict[str, JsonValue] = deepcopy(document)

    assert len(plan_readable_tables(document)) == 1
    assert document == original
