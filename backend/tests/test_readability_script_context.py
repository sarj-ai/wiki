from copy import deepcopy

from pydantic import JsonValue
import pytest

from app.document_transform.ir import utf16_length
from app.document_transform.readability_json import array, bodies, content, integer, obj
from app.document_transform.readability_tables import (
    plan_readable_tables,
    project_readable_tables,
    readable_table_requests,
)
from tests.test_table_layout import _native_table


def _document(text: str) -> dict[str, JsonValue]:
    document = _native_table((("Operation", "Value"), (text, "1.2300 ms")))
    document["namedStyles"] = {
        "styles": [
            {
                "namedStyleType": "NORMAL_TEXT",
                "textStyle": {
                    "weightedFontFamily": {"fontFamily": "Arial", "weight": 400},
                    "fontSize": {"magnitude": 11, "unit": "PT"},
                },
            }
        ]
    }
    return document


def _paragraph(document: dict[str, JsonValue]) -> dict[str, JsonValue]:
    table = obj(obj(content(next(bodies(document))[1])[0])["table"])
    cell = obj(array(obj(array(table["tableRows"])[1])["tableCells"])[0])
    return obj(array(cell["content"])[0])


def _split(document: dict[str, JsonValue], offset: int) -> None:
    paragraph = obj(_paragraph(document)["paragraph"])
    run = obj(array(paragraph["elements"])[0])
    text = str(obj(run["textRun"])["content"])
    left, right = deepcopy(run), deepcopy(run)
    boundary = integer(run["startIndex"]) + utf16_length(text[:offset])
    left["endIndex"] = right["startIndex"] = boundary
    obj(left["textRun"])["content"] = text[:offset]
    obj(right["textRun"])["content"] = text[offset:]
    paragraph["elements"] = [left, right]


def _project(document: dict[str, JsonValue]) -> dict[str, JsonValue]:
    result = deepcopy(document)
    project_readable_tables(result, plan_readable_tables(document), applied=False)
    return result


@pytest.mark.parametrize("text", ["東京", "اختبار", "Cache 東京", "🧪 東京"])
@pytest.mark.parametrize("newline_only", [False, True])
def test_equivalent_non_latin_run_splitting_preserves_the_table_invariant(
    text: str, *, newline_only: bool
) -> None:
    source = _document(text)
    observed = deepcopy(source)
    _split(observed, len(text) if newline_only else 1)

    assert _project(observed) == _project(source)


@pytest.mark.parametrize("run_index", [0, 1])
def test_non_latin_text_and_terminator_families_remain_protected(run_index: int) -> None:
    source = _document("東京")
    observed = deepcopy(source)
    _split(observed, 2)
    paragraph = obj(_paragraph(observed)["paragraph"])
    run = obj(array(paragraph["elements"])[run_index])
    obj(run["textRun"])["textStyle"] = {
        "weightedFontFamily": {"fontFamily": "Courier New", "weight": 400}
    }

    assert _project(observed) != _project(source)


def test_new_font_policy_preserves_the_whole_script_context_and_legacy_requests() -> None:
    source = _document("Cache 東京")
    _split(source, len("Cache "))
    targets = plan_readable_tables(source)
    start = _paragraph(source)["startIndex"]

    current = next(
        obj(request["updateTextStyle"])
        for request in readable_table_requests(source, targets)
        if "updateTextStyle" in request
        and obj(obj(request["updateTextStyle"])["range"])["startIndex"] == start
    )
    legacy = next(
        obj(request["updateTextStyle"])
        for request in readable_table_requests(source, targets, version=4)
        if "updateTextStyle" in request
        and obj(obj(request["updateTextStyle"])["range"])["startIndex"] == start
    )

    assert current["fields"] == "fontSize"
    assert "weightedFontFamily" not in obj(current["textStyle"])
    assert legacy["fields"] == "fontSize,weightedFontFamily.fontFamily"
    assert obj(legacy["textStyle"])["weightedFontFamily"] == {"fontFamily": "Nunito"}
