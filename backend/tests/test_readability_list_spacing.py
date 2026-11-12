from copy import deepcopy

import pytest

from app.document_transform.ir import utf16_length
from app.document_transform.readability import plan_readability, verify_readability
from app.document_transform.readability_json import obj
from tests.test_document_readability import _fixture, _LayoutEditor, _paragraph


@pytest.mark.parametrize("spacing", ["COLLAPSE_LISTS", "SPACING_MODE_UNSPECIFIED"])
async def test_imported_paragraph_native_list_spacing_has_one_known_api_effect(
    spacing: str,
) -> None:
    before = _fixture("before")
    initial = await plan_readability(before, title="List spacing", generator=_LayoutEditor())
    assert initial is not None
    target = next(p for p in initial.paragraphs if p.parts[0].role == "bullet")
    source_style = obj(obj(_paragraph(before, target.start)["paragraph"])["paragraphStyle"])
    source_style["spacingMode"] = "NEVER_COLLAPSE"
    plan = await plan_readability(before, title="List spacing", generator=_LayoutEditor())
    assert plan is not None
    after = _fixture("after")
    cursor = target.start
    for part in target.parts:
        style = obj(obj(_paragraph(after, cursor)["paragraph"])["paragraphStyle"])
        style["spacingMode"] = spacing
        cursor += utf16_length(part.text) + 1
    original = deepcopy(after)

    assert verify_readability(after, plan) is (spacing == "COLLAPSE_LISTS")
    assert after == original
