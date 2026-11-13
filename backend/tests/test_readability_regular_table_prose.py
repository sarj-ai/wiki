from collections.abc import Iterator
from copy import deepcopy

from pydantic import JsonValue
import pytest

from app.document_transform.readability import (
    plan_readability,
    readability_invariant_sha256,
    verify_readability,
)
from app.document_transform.readability_json import array, bodies, content, obj
from app.document_transform.readability_tables import readable_table_requests
from tests.test_document_readability import _LayoutEditor
from tests.test_readability_body_tables import _body_readback
from tests.test_table_layout import _native_table


def _runs(document: dict[str, JsonValue]) -> Iterator[dict[str, JsonValue]]:
    table = obj(obj(content(next(bodies(document))[1])[0])["table"])
    for row in array(table["tableRows"]):
        for cell in array(obj(row)["tableCells"]):
            for part in array(obj(cell)["content"]):
                yield from (obj(value) for value in array(obj(obj(part)["paragraph"])["elements"]))


@pytest.mark.parametrize("inherited_bold", [False, True])
async def test_regular_table_prose_resolves_inherited_weight_without_changing_code(
    *, inherited_bold: bool
) -> None:
    before = _native_table((("Cache", "640 jobs"), ("Disk", "keep  code")))
    before["revisionId"] = "r1"
    tab = next(bodies(before))[1]
    tab["namedStyles"] = {
        "styles": [
            {
                "namedStyleType": "NORMAL_TEXT",
                "textStyle": {
                    "bold": inherited_bold,
                    "weightedFontFamily": {"fontFamily": "Arial", "weight": 700},
                },
            }
        ]
    }
    code = list(_runs(before))[-1]
    code_style: dict[str, JsonValue] = {
        "weightedFontFamily": {"fontFamily": "Courier New", "weight": 700},
        "bold": True,
        "italic": True,
    }
    obj(code["textRun"])["textStyle"] = deepcopy(code_style)
    original = deepcopy(before)
    plan = await plan_readability(before, title="Inherited table", generator=_LayoutEditor())
    assert plan is not None
    after = _body_readback(before, plan.tables[0])
    for run in list(_runs(after))[:-1]:
        obj(run["textRun"])["textStyle"] = {
            "fontSize": {"magnitude": 10, "unit": "PT"},
            "weightedFontFamily": {"fontFamily": "Nunito", "weight": 400},
            "bold": False,
        }
    obj(list(_runs(after))[-1]["textRun"])["textStyle"] = {
        **code_style,
        "fontSize": {"magnitude": 10, "unit": "PT"},
    }
    assert verify_readability(after, plan)
    assert before == original
    fonts = [obj(r["updateTextStyle"]) for r in plan.requests if "updateTextStyle" in r]
    assert all(obj(f["textStyle"])["bold"] is False for f in fonts[:-1])
    assert fonts[-1]["fields"] == "fontSize"
    legacy = plan.model_copy(update={"version": 5})
    legacy = legacy.model_copy(
        update={
            "invariant_sha256": readability_invariant_sha256(before, legacy, applied=False),
            "requests": readable_table_requests(before, plan.tables, version=5),
        }
    )
    assert not verify_readability(after, legacy)
    for failure in ["bold", "weight", "code", "value", "link"]:
        changed = deepcopy(after)
        run = next(_runs(changed))
        style = obj(obj(run["textRun"])["textStyle"])
        match failure:
            case "bold":
                style["bold"] = True
            case "weight":
                obj(style["weightedFontFamily"])["weight"] = 700
            case "code":
                obj(obj(list(_runs(changed))[-1]["textRun"])["textStyle"])["bold"] = False
            case "value":
                obj(run["textRun"])["content"] = "Changed\n"
            case _:
                style["link"] = {"url": "https://example.test/unplanned"}
        assert not verify_readability(changed, plan), failure
