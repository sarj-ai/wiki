from pydantic import JsonValue

from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.readability_json import array, obj
from app.document_transform.readability_types import (
    CURRENT_READABILITY_VERSION,
    ReadabilityTable,
    ReadabilityVersion,
)


_COLORS = CURRENT_BRAND_MANIFEST.colors
_COLOR_TOLERANCE = 0.00001
_BORDER_PRESERVATION_VERSION = 4
_HORIZONTAL_PADDING_PRESERVATION_VERSION = 3


def _row_style(
    row: int, *, target: ReadabilityTable, version: ReadabilityVersion
) -> dict[str, JsonValue]:
    header_rows = target.header_rows if target.header else 0
    color = (
        _COLORS.primary_tint
        if row < header_rows
        else _COLORS.subtle
        if row % 2 == 0
        else _COLORS.paper
    ).removeprefix("#")
    rgb: dict[str, JsonValue] = {
        key: int(color[index : index + 2], 16) / 255
        for index, key in zip((0, 2, 4), ("red", "green", "blue"), strict=True)
    }
    border: dict[str, JsonValue] = {
        "color": {
            "color": {
                "rgbColor": {
                    key: int(_COLORS.border.removeprefix("#")[index : index + 2], 16) / 255
                    for index, key in zip((0, 2, 4), ("red", "green", "blue"), strict=True)
                }
            }
        },
        "width": {"magnitude": 0.5, "unit": "PT"},
        "dashStyle": "SOLID",
    }
    return {
        # Docs can omit an explicitly written border when it equals an inherited
        # table style. Preserve borders in new plans: an absent field cannot
        # prove a requested border value. Older checkpoints retain their writes.
        **(
            dict.fromkeys(("borderTop", "borderBottom", "borderLeft", "borderRight"), border)
            if version < _BORDER_PRESERVATION_VERSION
            else {}
        ),
        "backgroundColor": {"color": {"rgbColor": rgb}},
        "paddingTop": {"magnitude": 4, "unit": "PT"},
        "paddingBottom": {"magnitude": 4, "unit": "PT"},
        **(
            {
                "paddingLeft": {"magnitude": 6, "unit": "PT"},
                "paddingRight": {"magnitude": 6, "unit": "PT"},
            }
            if version < _HORIZONTAL_PADDING_PRESERVATION_VERSION
            else {}
        ),
        "contentAlignment": "TOP",
    }


def readable_table_background(
    row: int, *, target: ReadabilityTable, version: ReadabilityVersion
) -> JsonValue:
    if not (target.header or target.style_cells):
        return None
    return _row_style(row, target=target, version=version)["backgroundColor"]


def readable_table_cell_requests(
    element: dict[str, JsonValue],
    target: ReadabilityTable,
    *,
    version: ReadabilityVersion = CURRENT_READABILITY_VERSION,
) -> list[dict[str, JsonValue]]:
    if not (target.header or target.style_cells):
        return []
    requests: list[dict[str, JsonValue]] = []
    for row, _value in enumerate(array(obj(element["table"])["tableRows"])):
        style = _row_style(row, target=target, version=version)
        requests.append(
            {
                "updateTableCellStyle": {
                    "tableRange": {
                        "tableCellLocation": {
                            "tableStartLocation": {"index": target.start, "tabId": target.tab_id},
                            "rowIndex": row,
                            "columnIndex": 0,
                        },
                        "rowSpan": 1,
                        "columnSpan": len(target.widths),
                    },
                    "tableCellStyle": style,
                    "fields": ",".join(style),
                }
            }
        )
    return requests


def project_readable_table_cells(
    element: dict[str, JsonValue],
    target: ReadabilityTable,
    *,
    applied: bool,
    version: ReadabilityVersion = CURRENT_READABILITY_VERSION,
) -> None:
    if not (target.header or target.style_cells):
        return
    for row, value in enumerate(array(obj(element["table"])["tableRows"])):
        for cell_value in array(obj(value)["tableCells"]):
            cell = obj(cell_value)
            style = obj(cell.get("tableCellStyle", {}))
            for key, expected in _row_style(row, target=target, version=version).items():
                if applied and not _matches(
                    style.get(key, "TOP" if key == "contentAlignment" else None), expected
                ):
                    raise ValueError
                style.pop(key, None)
            if not style:
                cell.pop("tableCellStyle", None)


def _matches(observed: JsonValue, expected: JsonValue) -> bool:
    match expected:
        case dict():
            actual = obj(observed)
            return all(
                _matches(actual.get(key, 0 if isinstance(value, float) else None), value)
                for key, value in expected.items()
            )
        case float():
            return (
                isinstance(observed, (float, int)) and abs(observed - expected) < _COLOR_TOLERANCE
            )
        case _:
            return observed == expected
