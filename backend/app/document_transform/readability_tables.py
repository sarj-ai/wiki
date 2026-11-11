from copy import deepcopy
from dataclasses import dataclass
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.readability_geometry import readable_table_width
from app.document_transform.readability_json import (
    array,
    bodies,
    content,
    effective_text_style,
    integer,
    obj,
    plain_text,
)
from app.document_transform.readability_table_style import (
    project_readable_table_cells,
    readable_table_cell_requests,
)
from app.document_transform.readability_types import (
    CURRENT_READABILITY_VERSION,
    ReadabilityTable,
    ReadabilityVersion,
)
from app.document_transform.table_layout import (
    missing_table_value,
    numeric_columns,
    numeric_table_value,
    table_column_widths,
)
from app.identifiers import DocsTabId


_TOKENS = CURRENT_BRAND_MANIFEST.document
_MIN_COLUMNS = 2
_MAX_COLUMNS = 10
_DIMENSION_TOLERANCE = 0.05
_PARAGRAPH_FIELDS = ("alignment", "lineSpacing", "spaceAbove", "spaceBelow")


@dataclass(frozen=True, slots=True)
class _Cell:
    row: int
    column: int
    paragraphs: tuple[dict[str, JsonValue], ...]


class _HeaderStyle(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    header: bool = Field(default=False, alias="tableHeader")


class _HeaderRow(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    style: _HeaderStyle = Field(default=_HeaderStyle(), alias="tableRowStyle")


def plan_readable_tables(document: dict[str, JsonValue]) -> tuple[ReadabilityTable, ...]:
    result = []
    for tab_id, tab in bodies(document):
        for value in content(tab):
            element = obj(value)
            if "table" not in element:
                continue
            try:
                target = _table_plan(element, document=document, tab=tab, tab_id=tab_id)
                _project_table(deepcopy(element), target, document=document, tab=tab, applied=False)
            except KeyError, ValueError, TypeError:
                continue
            result.append(target)
    return tuple(result)


def _table_plan(
    element: dict[str, JsonValue],
    *,
    document: dict[str, JsonValue],
    tab: dict[str, JsonValue],
    tab_id: DocsTabId,
) -> ReadabilityTable:
    rows = _rows(_cells(element), integer(obj(element["table"])["columns"]))
    native_headers = _native_header_rows(element)
    header_rows = native_headers or 1
    numeric = numeric_columns(rows, header_rows=header_rows)
    header = bool(native_headers) or any(
        not numeric_table_value(rows[0][column]) and not missing_table_value(rows[0][column])
        for column in numeric
    )
    # Header inference needs body evidence. Once no header is detected, include
    # the first row in quantity classification and width planning as well. This
    # also lets a one-row body table align its quantities without inventing a header.
    data_start = header_rows if header else 0
    numeric = numeric_columns(rows, header_rows=data_start)
    return ReadabilityTable(
        tab_id=tab_id,
        start=integer(element["startIndex"]),
        widths=table_column_widths(
            rows,
            total=readable_table_width(document, tab, start=integer(element["startIndex"])),
            header_rows=data_start,
        ),
        numeric=numeric,
        header=header,
        header_rows=header_rows,
        style_cells=True,
    )


def _native_header_rows(element: dict[str, JsonValue]) -> int:
    count = 0
    for value in array(obj(element["table"])["tableRows"]):
        if not _HeaderRow.model_validate(value).style.header:
            break
        count += 1
    return count


def _rows(cells: tuple[_Cell, ...], columns: int) -> tuple[tuple[str, ...], ...]:
    values = [" ".join(plain_text(part).strip() for part in cell.paragraphs) for cell in cells]
    return tuple(tuple(values[index : index + columns]) for index in range(0, len(values), columns))


def readable_table_requests(
    document: dict[str, JsonValue],
    targets: tuple[ReadabilityTable, ...],
    *,
    version: ReadabilityVersion = CURRENT_READABILITY_VERSION,
) -> list[dict[str, JsonValue]]:
    requests: list[dict[str, JsonValue]] = []
    tabs = dict(bodies(document))
    for target in targets:
        element = next(
            obj(value)
            for value in content(tabs[target.tab_id])
            if obj(value).get("startIndex") == target.start
        )
        requests.extend(readable_table_cell_requests(element, target, version=version))
        for column, width in enumerate(target.widths):
            requests.append(
                {
                    "updateTableColumnProperties": {
                        "tableStartLocation": {"index": target.start, "tabId": target.tab_id},
                        "columnIndices": [column],
                        "tableColumnProperties": {
                            "widthType": "FIXED_WIDTH",
                            "width": {"magnitude": width / 20, "unit": "PT"},
                        },
                        "fields": "widthType,width",
                    }
                }
            )
        for cell in _cells(element):
            for part in cell.paragraphs:
                start, end = integer(part["startIndex"]), integer(part["endIndex"]) - 1
                paragraph_range: dict[str, JsonValue] = {
                    "startIndex": start,
                    "endIndex": end + 1,
                    "tabId": target.tab_id,
                }
                requests.append(
                    {
                        "updateParagraphStyle": {
                            "range": paragraph_range,
                            "paragraphStyle": _paragraph_style(
                                numeric=cell.column in target.numeric
                            ),
                            "fields": ",".join(_PARAGRAPH_FIELDS),
                        }
                    }
                )
                requests.extend(
                    _cell_font_requests(
                        part,
                        target,
                        document=document,
                        tab=tabs[target.tab_id],
                        header=target.header and cell.row < target.header_rows,
                    )
                )
    return requests


def _cell_font_requests(
    part: dict[str, JsonValue],
    target: ReadabilityTable,
    *,
    document: dict[str, JsonValue],
    tab: dict[str, JsonValue],
    header: bool,
) -> list[dict[str, JsonValue]]:
    requests: list[dict[str, JsonValue]] = []
    end = integer(part["endIndex"]) - 1
    for value in array(obj(part["paragraph"])["elements"]):
        run = obj(value)
        left = integer(run["startIndex"])
        right = min(end, integer(run["endIndex"]))
        if right <= left:
            continue
        style = effective_text_style(
            document,
            tab,
            obj(obj(part["paragraph"])["paragraphStyle"]),
            obj(obj(run["textRun"]).get("textStyle", {})),
        )
        preserve_family = _keep_table_family(style, text=str(obj(run["textRun"])["content"]))
        font: dict[str, JsonValue] = {
            "fontSize": {"magnitude": _TOKENS.table_size_pt or 10, "unit": "PT"}
        }
        if header:
            font["bold"] = True
        if not preserve_family:
            font["weightedFontFamily"] = {"fontFamily": _TOKENS.font_family}
        requests.append(
            {
                "updateTextStyle": {
                    "range": {
                        "startIndex": left,
                        "endIndex": right,
                        "tabId": target.tab_id,
                    },
                    "textStyle": font,
                    "fields": (
                        "fontSize" if preserve_family else "fontSize,weightedFontFamily.fontFamily"
                    )
                    + (",bold" if header else ""),
                }
            }
        )
    return requests


def project_readable_tables(
    document: dict[str, JsonValue],
    targets: tuple[ReadabilityTable, ...],
    *,
    applied: bool,
    version: ReadabilityVersion = CURRENT_READABILITY_VERSION,
) -> None:
    tabs = dict(bodies(document))
    for target in targets:
        matches = [
            obj(value)
            for value in content(tabs[target.tab_id])
            if obj(value).get("startIndex") == target.start and "table" in obj(value)
        ]
        if len(matches) != 1:
            msg = "Table identity changed"
            raise ValueError(msg)
        _project_table(
            matches[0],
            target,
            document=document,
            tab=tabs[target.tab_id],
            applied=applied,
            version=version,
        )


def _project_table(
    element: dict[str, JsonValue],
    target: ReadabilityTable,
    *,
    document: dict[str, JsonValue],
    tab: dict[str, JsonValue],
    applied: bool,
    version: ReadabilityVersion = CURRENT_READABILITY_VERSION,
) -> None:
    project_readable_table_cells(element, target, applied=applied, version=version)
    table = obj(element["table"])
    style = obj(table.get("tableStyle", {}))
    columns = array(style.get("tableColumnProperties", [{} for _ in target.widths]))
    if len(columns) != len(target.widths):
        msg = "Table column count changed"
        raise ValueError(msg)
    for value, width in zip(columns, target.widths, strict=True):
        column = obj(value)
        if applied and (
            column.get("widthType") != "FIXED_WIDTH"
            or not _dimension_matches(
                column.get("width", {}), {"magnitude": width / 20, "unit": "PT"}
            )
        ):
            msg = "Table column width was not applied"
            raise ValueError(msg)
        column.pop("widthType", None)
        column.pop("width", None)
    if not any(columns):
        style.pop("tableColumnProperties", None)
    if not style:
        table.pop("tableStyle", None)
    for cell in _cells(element):
        for paragraph in cell.paragraphs:
            _project_paragraph(
                paragraph,
                header=target.header and cell.row < target.header_rows,
                document=document,
                tab=tab,
                numeric=cell.column in target.numeric,
                applied=applied,
            )


def _project_paragraph(
    element: dict[str, JsonValue],
    *,
    document: dict[str, JsonValue],
    tab: dict[str, JsonValue],
    numeric: bool,
    header: bool,
    applied: bool,
) -> None:
    paragraph = obj(element["paragraph"])
    style = obj(paragraph.get("paragraphStyle", {}))
    expected = _paragraph_style(numeric=numeric)
    for key, value in expected.items():
        if applied and not (
            _dimension_matches(style.get(key, {}), value)
            if isinstance(value, dict)
            else style.get(key) == value
        ):
            msg = "Table paragraph formatting was not applied"
            raise ValueError(msg)
        style.pop(key, None)
    if not style:
        paragraph.pop("paragraphStyle", None)
    runs: list[JsonValue] = []
    for value in array(paragraph["elements"]):
        part = _canonical_table_run(
            obj(value), document=document, tab=tab, style=style, header=header, applied=applied
        )
        run = obj(part["textRun"])
        text = str(run["content"])
        previous = obj(runs[-1]) if runs else None
        if previous is not None and obj(previous["textRun"]).get("textStyle", {}) == run.get(
            "textStyle", {}
        ):
            old_run = obj(previous["textRun"])
            old_run["content"] = str(old_run["content"]) + text
        else:
            runs.append(part)
    paragraph["elements"] = runs


def _cells(element: dict[str, JsonValue]) -> tuple[_Cell, ...]:
    table = obj(element["table"])
    columns = integer(table["columns"])
    rows = array(table["tableRows"])
    if not _MIN_COLUMNS <= columns <= _MAX_COLUMNS or not rows:
        msg = "Table geometry is unsuitable for automatic layout"
        raise ValueError(msg)
    result = []
    for row_index, value in enumerate(rows):
        cells = array(obj(value)["tableCells"])
        if len(cells) != columns:
            msg = "Preserve merged table cells"
            raise ValueError(msg)
        for column, value_cell in enumerate(cells):
            cell = obj(value_cell)
            style = obj(cell.get("tableCellStyle", {}))
            if style.get("rowSpan", 1) != 1 or style.get("columnSpan", 1) != 1:
                msg = "Preserve merged table cells"
                raise ValueError(msg)
            paragraphs = tuple(obj(item) for item in array(cell["content"]))
            if not paragraphs:
                msg = "Preserve empty table structures"
                raise ValueError(msg)
            for paragraph in paragraphs:
                plain_text(paragraph)
            result.append(_Cell(row_index, column, paragraphs))
    return tuple(result)


def _paragraph_style(*, numeric: bool) -> dict[str, JsonValue]:
    return {
        "alignment": "END" if numeric else "START",
        "lineSpacing": 100,
        "spaceAbove": {"magnitude": 0, "unit": "PT"},
        "spaceBelow": {"magnitude": 2, "unit": "PT"},
    }


def _dimension_matches(value: JsonValue, expected: JsonValue) -> bool:
    observed = obj(value)
    wanted = obj(expected)
    return (
        observed.get("unit") == "PT"
        and abs(float(str(observed.get("magnitude", 0))) - float(str(wanted["magnitude"])))
        < _DIMENSION_TOLERANCE
    )


def _canonical_table_run(
    value: dict[str, JsonValue],
    *,
    document: dict[str, JsonValue],
    tab: dict[str, JsonValue],
    style: dict[str, JsonValue],
    header: bool,
    applied: bool,
) -> dict[str, JsonValue]:
    part = deepcopy(value)
    run = obj(part["textRun"])
    text_style = effective_text_style(document, tab, style, obj(run.get("textStyle", {})))
    run["textStyle"] = text_style
    if applied and run["content"] != "\n":
        _verify_table_font(text_style, text=str(run["content"]))
    if header:
        # Font writes exclude paragraph terminators and empty cells. Docs may
        # return their inherited styles in a separate newline-only text run.
        if applied and run["content"] != "\n" and text_style.get("bold") is not True:
            raise ValueError
        text_style.pop("bold", None)
    text_style.pop("fontSize", None)
    if not _keep_table_family(text_style, text=str(run["content"])):
        family = obj(text_style.get("weightedFontFamily", {}))
        family.pop("fontFamily", None)
        if not family:
            text_style.pop("weightedFontFamily", None)
    if not text_style:
        run.pop("textStyle", None)
    # Google can split text runs at style-range boundaries. Compare their
    # residual properties and text, independent of that segmentation.
    part.pop("startIndex", None)
    part.pop("endIndex", None)
    return part


def _verify_table_font(text_style: dict[str, JsonValue], *, text: str) -> None:
    if not _dimension_matches(
        text_style.get("fontSize", {}),
        {"magnitude": _TOKENS.table_size_pt or 10, "unit": "PT"},
    ):
        msg = "Table font size was not applied"
        raise ValueError(msg)
    if (
        not _keep_table_family(text_style, text=text)
        and obj(text_style.get("weightedFontFamily", {})).get("fontFamily") != _TOKENS.font_family
    ):
        msg = "Table font family was not applied"
        raise ValueError(msg)


def _keep_table_family(style: dict[str, JsonValue], *, text: str) -> bool:
    return _code_style(style) or any(
        char.isalpha() and char not in "µμ" and "LATIN" not in unicodedata.name(char, "")
        for char in text
    )


def _code_style(style: dict[str, JsonValue]) -> bool:
    family = str(obj(style.get("weightedFontFamily", {})).get("fontFamily", "")).casefold()
    return any(token in family for token in ("mono", "courier", "consolas", "menlo", "code"))
