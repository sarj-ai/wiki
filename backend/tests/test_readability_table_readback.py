from copy import deepcopy

from pydantic import JsonValue
import pytest

from app.document_transform.readability import plan_readability, verify_readability
from app.document_transform.readability_json import array, integer, obj
from tests.test_document_readability import _fixture, _LayoutEditor, _paragraph, _plan


def _first_header(document: dict[str, JsonValue]) -> dict[str, JsonValue]:
    table = obj(_paragraph(document, 247)["table"])
    row = obj(array(table["tableRows"])[0])
    cell = obj(array(row["tableCells"])[0])
    return obj(array(cell["content"])[0])


def _shift_after_header(value: JsonValue) -> None:
    match value:
        case dict():
            for key, child in value.items():
                if key in {"startIndex", "endIndex"} and isinstance(child, int) and child >= 259:
                    value[key] = child - 9
                else:
                    _shift_after_header(child)
        case list():
            for child in value:
                _shift_after_header(child)
        case _:
            return


async def test_empty_header_verifies_without_styling_an_unwritten_newline() -> None:
    before, after = _fixture("before"), _fixture("after")
    for document in (before, after):
        header = _first_header(document)
        run = obj(obj(array(obj(header["paragraph"])["elements"])[0])["textRun"])
        run.update({"content": "\n", "textStyle": {}})
        _shift_after_header(document)

    plan = await plan_readability(before, title="Empty header", generator=_LayoutEditor())

    assert plan is not None
    assert plan.tables[0].header
    columns = array(
        obj(obj(_paragraph(after, 247)["table"])["tableStyle"])["tableColumnProperties"]
    )
    for column, width in zip(columns, plan.tables[0].widths, strict=True):
        obj(column)["width"] = {"magnitude": width / 20, "unit": "PT"}
    assert verify_readability(after, plan)
    assert not any(
        "updateTextStyle" in request
        and obj(obj(request["updateTextStyle"])["range"])["startIndex"] == 250
        for request in plan.requests
    )


@pytest.mark.parametrize("corruption", [None, "visible_bold", "newline_link"])
async def test_split_header_newline_keeps_its_inherited_style_and_detects_real_changes(
    corruption: str | None,
) -> None:
    plan = await _plan()
    after = _fixture("after")
    header = _first_header(after)
    elements = array(obj(header["paragraph"])["elements"])
    visible = obj(elements[0])
    end = integer(visible["endIndex"])
    visible["endIndex"] = end - 1
    run = obj(visible["textRun"])
    run["content"] = str(run["content"]).removesuffix("\n")
    newline: dict[str, JsonValue] = {
        "startIndex": end - 1,
        "endIndex": end,
        "textRun": {"content": "\n", "textStyle": {}},
    }
    elements.append(newline)
    if corruption == "visible_bold":
        obj(run["textStyle"])["bold"] = False
    elif corruption == "newline_link":
        obj(obj(newline["textRun"])["textStyle"])["link"] = {"url": "https://example.test/added"}

    original = deepcopy(after)
    assert verify_readability(after, plan) is (corruption is None)
    assert after == original
