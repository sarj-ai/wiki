from copy import deepcopy
import json
from typing import Any

from loguru import logger
import pytest

from app.document_transform.ir import parse_google_document
from app.document_transform.native_pagination import plan_native_pagination
from tests.test_native_spacer_layout import _spaced_document
from tests.test_transform_diagnostics_workflow import MODULE, TASK, _record


def _named_style_value_diagnostics(style: dict[str, Any]) -> dict[str, Any]:
    native = _spaced_document()
    native["namedStyles"] = {"styles": [{"namedStyleType": "NORMAL_TEXT", "paragraphStyle": style}]}
    records: list[dict[str, Any]] = []
    sink = logger.add(lambda message: records.append(message.record["extra"]))
    try:
        plan_native_pagination(
            native, parse_google_document(native, title="Synthetic"), emit_diagnostics=True
        )
    finally:
        logger.remove(sink)
    return records[-1]["diagnostics"]


def _border() -> dict[str, Any]:
    return {
        "width": {"magnitude": 0, "unit": "PT"},
        "padding": {"magnitude": 0, "unit": "PT"},
        "color": {},
        "dashStyle": "SOLID",
    }


@pytest.mark.parametrize("magnitude", [0, 0.0, -0.0, 1, -1])
def test_explicit_indent_zero_is_not_inherited_or_nonzero(magnitude: float) -> None:
    checks = _named_style_value_diagnostics({"indentStart": {"magnitude": magnitude, "unit": "PT"}})
    assert checks["spacer_named_indent_schema_known"]
    assert checks["spacer_named_indent_all_zero"] is (magnitude == 0)
    assert "spacer_named_indent_all_zero" not in _named_style_value_diagnostics({})


@pytest.mark.parametrize(
    "value",
    [True, None, "SECRET", 10**400, float("nan"), float("inf"), float("-inf"), {}],
    ids=[
        "bool",
        "null",
        "string",
        "huge",
        "nan",
        "positive-infinity",
        "negative-infinity",
        "object",
    ],
)
def test_malformed_dimensions_are_unknown_and_never_throw(value: object) -> None:
    checks = _named_style_value_diagnostics({"indentEnd": {"magnitude": value, "unit": "PT"}})
    assert checks["spacer_named_indent_schema_known"] is False
    assert "spacer_named_indent_all_zero" not in checks
    assert "SECRET" not in json.dumps(checks)


@pytest.mark.parametrize(
    "dimension",
    [
        {},
        {"unit": "PT"},
        {"magnitude": 0},
        {"magnitude": 0, "unit": "PX"},
        {"magnitude": 0, "unit": "PT", "SECRET": "SECRET"},
    ],
    ids=["empty", "missing-magnitude", "missing-unit", "wrong-unit", "unknown-field"],
)
def test_incomplete_dimension_is_not_assumed_zero(dimension: dict[str, Any]) -> None:
    checks = _named_style_value_diagnostics({"indentStart": dimension})
    assert checks["spacer_named_indent_schema_known"] is False
    assert "spacer_named_indent_all_zero" not in checks


@pytest.mark.parametrize(
    ("shading", "transparent"),
    [
        ({"backgroundColor": {}}, True),
        ({"backgroundColor": {"color": {"rgbColor": {"red": 1, "green": 1, "blue": 1}}}}, False),
    ],
    ids=["explicit-transparent", "opaque-white"],
)
def test_shading_transparent_is_distinct_from_opaque_white(
    shading: dict[str, Any], transparent: bool
) -> None:
    checks = _named_style_value_diagnostics({"shading": shading})
    assert checks["spacer_named_shading_schema_known"]
    assert checks["spacer_named_shading_explicit_transparent"] is transparent


@pytest.mark.parametrize(
    "shading",
    [
        {},
        None,
        {"backgroundColor": {"color": {}}},
        {"backgroundColor": {"SECRET": 1}},
        {"backgroundColor": {"color": {"rgbColor": {"red": True}}}},
        {"backgroundColor": {"color": {"rgbColor": {"red": float("nan")}}}},
    ],
    ids=["missing", "null", "missing-rgb", "unknown", "boolean", "nan"],
)
def test_unknown_shading_is_not_labeled_transparent(shading: object) -> None:
    checks = _named_style_value_diagnostics({"shading": shading})
    assert checks["spacer_named_shading_schema_known"] is False
    assert "spacer_named_shading_explicit_transparent" not in checks


@pytest.mark.parametrize("padding", [0, 4])
def test_zero_border_width_does_not_imply_zero_padding(padding: int) -> None:
    border = _border()
    border["padding"]["magnitude"] = padding
    checks = _named_style_value_diagnostics({"borderTop": border})
    assert checks["spacer_named_border_schema_known"]
    assert checks["spacer_named_border_all_width_zero"]
    assert checks["spacer_named_border_all_padding_zero"] is (padding == 0)
    assert checks["spacer_named_border_all_transparent"]


@pytest.mark.parametrize("change", ["missing", "unknown", "malformed", "dash", "huge"])
def test_border_requires_complete_known_schema(change: str) -> None:
    border = _border()
    match change:
        case "missing":
            del border["padding"]
        case "unknown":
            border["SECRET"] = "SECRET"
        case "malformed":
            border["color"] = None
        case "dash":
            border["dashStyle"] = {"SECRET": "SECRET"}
        case _:
            border["width"]["magnitude"] = 10**400
    checks = _named_style_value_diagnostics({"borderTop": border})
    assert checks["spacer_named_border_schema_known"] is False
    assert "spacer_named_border_all_width_zero" not in checks
    assert "SECRET" not in json.dumps(checks)


def test_diagnostics_describe_only_first_rejected_entry_without_changing_rules() -> None:
    native = _spaced_document()
    style = {
        "indentStart": {"magnitude": 0, "unit": "PT"},
        "borderTop": _border(),
        "shading": {"backgroundColor": {}},
    }
    native["namedStyles"] = {
        "styles": [
            {"namedStyleType": "NORMAL_TEXT", "paragraphStyle": style},
            {
                "namedStyleType": "HEADING_1",
                "paragraphStyle": {
                    "indentStart": {"magnitude": 9, "unit": "PT"},
                    "SECRET": "SECRET",
                },
            },
        ]
    }
    before = deepcopy(native)
    original = parse_google_document(native, title="SECRET")
    expected = plan_native_pagination(native, original)
    records: list[dict[str, Any]] = []
    sink = logger.add(lambda message: records.append(message.record["extra"]))
    try:
        assert plan_native_pagination(native, original, emit_diagnostics=True) == expected
    finally:
        logger.remove(sink)
    checks = records[-1]["diagnostics"]
    assert checks["spacer_named_values_first_entry_only"]
    assert checks["spacer_named_indent_all_zero"]
    assert checks["spacer_named_border_all_padding_zero"]
    assert checks["spacer_named_shading_explicit_transparent"]
    assert checks["spacer_used_named_styles_known"] is False
    assert checks["spacer_planned_above_count"] == 0
    record = _record()
    record["jsonPayload"]["record"]["message"] = "document spacer eligibility"
    record["jsonPayload"]["record"]["extra"]["diagnostics"] = checks
    rows = MODULE["sanitize"]([record], TASK)
    assert rows[0]["spacer_named_indent_all_zero"] is True
    assert "SECRET" not in json.dumps(rows)
    assert native == before
