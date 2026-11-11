from copy import deepcopy

import pytest

from app.document_transform.readability import (
    plan_readability,
    readability_invariant_sha256,
    verify_readability,
)
from app.document_transform.readability_json import array, bodies, content, obj
from app.document_transform.readability_tables import readable_table_requests
from app.document_transform.readability_types import ReadabilityVersion
from tests.test_document_readability import _LayoutEditor
from tests.test_readability_body_tables import _body_readback, _first_cell_style
from tests.test_table_layout import _native_table


@pytest.mark.parametrize("explicit", [False, True])
async def test_new_layout_preserves_inherited_and_explicit_borders_and_rejects_damage(
    *,
    explicit: bool,
) -> None:
    source = _native_table((("Cache", "Read only"), ("Disk", "Read and write")))
    source["revisionId"] = "r1"
    if explicit:
        _first_cell_style(source)["borderTop"] = {
            "color": {"color": {"rgbColor": {"red": 0.8}}},
            "width": {"magnitude": 1.25, "unit": "PT"},
            "dashStyle": "SOLID",
        }
    original = deepcopy(source)
    plan = await plan_readability(source, title="Inherited borders", generator=_LayoutEditor())
    assert plan is not None
    after = _body_readback(source, plan.tables[0])

    assert verify_readability(after, plan)
    assert _first_cell_style(after).get("borderTop") == _first_cell_style(deepcopy(source)).get(
        "borderTop"
    )
    assert source == original
    damaged = deepcopy(after)
    _first_cell_style(damaged)["borderTop"] = {"width": {"magnitude": 9, "unit": "PT"}}
    assert not verify_readability(damaged, plan)
    removed = deepcopy(after)
    _first_cell_style(removed).pop("borderTop", None)
    assert verify_readability(removed, plan) is (not explicit)


@pytest.mark.parametrize("version", [1, 2, 3])
async def test_saved_border_writing_versions_keep_exact_requests_and_strict_verification(
    version: ReadabilityVersion,
) -> None:
    source = _native_table((("Cache", "Read only"), ("Disk", "Read and write")))
    source["revisionId"] = "r1"
    plan = await plan_readability(source, title="Saved borders", generator=_LayoutEditor())
    assert plan is not None
    requests = readable_table_requests(source, plan.tables, version=version)
    legacy = plan.model_copy(update={"version": version, "requests": requests})
    legacy = legacy.model_copy(
        update={"invariant_sha256": readability_invariant_sha256(source, legacy, applied=False)}
    )
    after = _body_readback(source, legacy.tables[0], version=version)
    if version < 3:
        # Versions 1/2 wrote horizontal padding; version 3 preserved it.
        table = obj(obj(content(next(bodies(after))[1])[0])["table"])
        for row in array(table["tableRows"]):
            for cell in array(obj(row)["tableCells"]):
                style = obj(obj(cell)["tableCellStyle"])
                for field in ("paddingLeft", "paddingRight"):
                    style[field] = {"magnitude": 6, "unit": "PT"}
    assert verify_readability(after, legacy)
    assert legacy.requests == readable_table_requests(source, legacy.tables, version=version)
    _first_cell_style(after).pop("borderTop")
    assert not verify_readability(after, legacy)
