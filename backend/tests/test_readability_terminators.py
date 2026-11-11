from copy import deepcopy

from pydantic import JsonValue
import pytest

from app.document_transform.readability import plan_readability, verify_readability
from app.document_transform.readability_json import array, bodies, content, integer, obj
from tests.test_document_readability import _fixture, _LayoutEditor, _plan


def _paragraph_ending(document: dict[str, JsonValue], end: int) -> dict[str, JsonValue]:
    return next(
        obj(value)
        for _, tab in bodies(document)
        for value in content(tab)
        if obj(value).get("endIndex") == end and "paragraph" in obj(value)
    )


def _split_newline(paragraph: dict[str, JsonValue]) -> dict[str, JsonValue]:
    elements = array(obj(paragraph["paragraph"])["elements"])
    last = obj(elements[-1])
    run = obj(last["textRun"])
    if run["content"] == "\n":
        return obj(run.setdefault("textStyle", {}))
    end = integer(last["endIndex"])
    last["endIndex"] = end - 1
    run["content"] = str(run["content"]).removesuffix("\n")
    style = deepcopy(obj(run.get("textStyle", {})))
    elements.append(
        {
            "startIndex": end - 1,
            "endIndex": end,
            "textRun": {"content": "\n", "textStyle": style},
        }
    )
    return style


@pytest.mark.parametrize("corruption", [None, "link", "font", "size", "annotation", "bold"])
async def test_split_prose_newline_verifies_every_unplanned_style_property(
    corruption: str | None,
) -> None:
    plan = await _plan()
    after = _fixture("after")
    style = _split_newline(_paragraph_ending(after, plan.paragraphs[0].end + 1))
    match corruption:
        case "link":
            style["link"] = {"url": "https://example.test/unplanned"}
        case "font":
            style["weightedFontFamily"] = {"fontFamily": "Courier New"}
        case "size":
            style["fontSize"] = {"magnitude": 99, "unit": "PT"}
        case "annotation":
            style["sourceExtension"] = "unplanned"
        case "bold":
            style["bold"] = True
        case _:
            assert corruption is None
    original = deepcopy(after)

    assert verify_readability(after, plan) is (corruption is None)
    assert after == original


@pytest.mark.parametrize("retained", [True, False])
async def test_authored_newline_link_survives_restructuring(retained: bool) -> None:
    before, after = _fixture("before"), _fixture("after")
    baseline = await _plan()
    end = baseline.paragraphs[0].end + 1
    source = _split_newline(_paragraph_ending(before, end))
    source["link"] = {"url": "https://example.test/authored"}
    plan = await plan_readability(before, title="Authored newline", generator=_LayoutEditor())
    assert plan is not None
    actual = _split_newline(_paragraph_ending(after, end))
    if retained:
        actual["link"] = deepcopy(source["link"])

    assert verify_readability(after, plan) is retained
