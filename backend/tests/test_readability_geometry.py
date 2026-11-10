from copy import deepcopy

from pydantic import JsonValue
import pytest

from app.document_transform.readability_json import bodies, content, obj
from app.document_transform.readability_tables import plan_readable_tables, readable_table_requests
from tests.test_document_readability import _fixture


@pytest.mark.parametrize(
    ("page", "section", "expected"),
    [
        ({}, {}, 9360),
        ({"pageSize": {"width": {"magnitude": 500, "unit": "PT"}}}, {}, 7120),
        ({"flipPageOrientation": True}, {}, 12960),
        ({"flipPageOrientation": True}, {"flipPageOrientation": False}, 9360),
        ({}, {"flipPageOrientation": True}, 12960),
        ({}, {"marginLeft": {"magnitude": 144, "unit": "PT"}}, 7920),
        ({}, {"marginRight": {"magnitude": 36, "unit": "PT"}}, 10080),
        ({}, {"marginLeft": {"unit": "PT"}}, 10800),
        ({}, {"columnProperties": [{"width": {"magnitude": 360, "unit": "PT"}}]}, 7200),
        (
            {
                "pageSize": {"width": {"magnitude": 500, "unit": "PT"}},
                "marginLeft": {"magnitude": 36, "unit": "PT"},
                "marginRight": {"magnitude": 48, "unit": "PT"},
            },
            {},
            8320,
        ),
    ],
    ids=[
        "letter",
        "custom-page",
        "landscape-document",
        "portrait-section-override",
        "landscape-section",
        "wide-left-margin",
        "narrow-right-margin",
        "zero-left-margin",
        "explicit-column-width",
        "custom-page-with-margins",
    ],
)
def test_native_table_width_uses_page_and_containing_section(
    page: dict[str, JsonValue], section: dict[str, JsonValue], expected: int
) -> None:
    document = _fixture("before")
    tab = next(bodies(document))[1]
    obj(tab["documentStyle"]).update(page)
    obj(obj(content(tab)[0])["sectionBreak"])["sectionStyle"] = section
    original = deepcopy(document)

    tables = plan_readable_tables(document)

    assert len(tables) == 1
    assert sum(tables[0].widths) == expected
    widths = [
        obj(obj(request["updateTableColumnProperties"])["tableColumnProperties"])["width"]
        for request in readable_table_requests(document, tables)
        if "updateTableColumnProperties" in request
    ]
    assert sum(float(str(obj(width)["magnitude"])) for width in widths) == expected / 20
    assert document == original


@pytest.mark.parametrize(
    "geometry",
    [
        {"documentFormat": {"documentMode": "PAGELESS"}},
        {"pageSize": {"width": {"magnitude": 100, "unit": "PT"}}},
        {"pageSize": {"width": {"magnitude": 8.5, "unit": "IN"}}},
        {"pageSize": {"width": {"magnitude": float("inf"), "unit": "PT"}}},
        {"pageSize": {"width": {"magnitude": float("nan"), "unit": "PT"}}},
        {"pageSize": {"width": {"magnitude": 10**1000, "unit": "PT"}}},
        {"marginLeft": {"magnitude": -1, "unit": "PT"}},
        {"marginRight": {"magnitude": True, "unit": "PT"}},
        {"flipPageOrientation": "false"},
    ],
    ids=[
        "pageless",
        "too-narrow",
        "non-point-unit",
        "infinite-width",
        "nan-width",
        "huge-width",
        "negative-margin",
        "boolean-margin",
        "string-orientation",
    ],
)
def test_ambiguous_or_invalid_page_geometry_preserves_the_native_table(
    geometry: dict[str, JsonValue],
) -> None:
    document = _fixture("before")
    tab = next(bodies(document))[1]
    obj(tab["documentStyle"]).update(geometry)

    assert not plan_readable_tables(document)


@pytest.mark.parametrize(
    "section",
    [
        {"columnProperties": [{}, {}]},
        {"columnProperties": [{"width": {"magnitude": 50, "unit": "PT"}}]},
    ],
    ids=["multiple-columns", "too-narrow-column"],
)
def test_unresolved_section_columns_preserve_the_native_table(
    section: dict[str, JsonValue],
) -> None:
    document = _fixture("before")
    tab = next(bodies(document))[1]
    obj(obj(content(tab)[0])["sectionBreak"])["sectionStyle"] = section

    assert not plan_readable_tables(document)


def _shift_indexes(value: JsonValue, delta: int) -> JsonValue:
    match value:
        case list():
            return [_shift_indexes(item, delta) for item in value]
        case dict():
            return {
                key: item + delta
                if key in {"startIndex", "endIndex"} and isinstance(item, int)
                else _shift_indexes(item, delta)
                for key, item in value.items()
            }
        case _:
            return value


def test_each_table_uses_its_own_section_and_missing_margins_return_to_document_defaults() -> None:
    document = _fixture("before")
    tab = next(bodies(document))[1]
    table = next(obj(value) for value in content(tab) if "table" in obj(value))
    content(tab).extend(
        [
            {
                "startIndex": 590,
                "endIndex": 591,
                "sectionBreak": {"sectionStyle": {"marginLeft": {"magnitude": 144, "unit": "PT"}}},
            },
            _shift_indexes(table, 600),
            {"startIndex": 1190, "endIndex": 1191, "sectionBreak": {"sectionStyle": {}}},
            _shift_indexes(table, 1200),
        ]
    )

    assert [sum(target.widths) for target in plan_readable_tables(document)] == [9360, 7920, 9360]


def test_document_geometry_is_inherited_when_a_tab_only_overrides_one_margin() -> None:
    document = _fixture("before")
    tab = next(bodies(document))[1]
    document["documentStyle"] = tab.pop("documentStyle")
    obj(document["documentStyle"])["pageSize"] = {"width": {"magnitude": 500, "unit": "PT"}}
    tab["documentStyle"] = {"marginLeft": {"magnitude": 36, "unit": "PT"}}

    assert sum(plan_readable_tables(document)[0].widths) == 7840


def test_suggested_section_geometry_preserves_the_native_table() -> None:
    document = _fixture("before")
    tab = next(bodies(document))[1]
    obj(obj(content(tab)[0])["sectionBreak"])["suggestedInsertionIds"] = ["suggestion"]

    assert not plan_readable_tables(document)
