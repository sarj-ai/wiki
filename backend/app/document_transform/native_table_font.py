from copy import deepcopy
import hashlib
import json
from typing import Any, Literal, NamedTuple
import unicodedata

from pydantic import BaseModel, ConfigDict, Field

from app.document_transform.ir import MAX_DOC_TABS, utf16_length
from app.document_transform.native_font_proof import native_paragraph_font_sha256
from app.identifiers import DocsTabId


TABLE_FONT_FAMILY = "Nunito"
_STYLE_KEYS = frozenset(
    {
        "backgroundColor",
        "baselineOffset",
        "bold",
        "fontSize",
        "foregroundColor",
        "italic",
        "link",
        "smallCaps",
        "strikethrough",
        "underline",
        "weightedFontFamily",
    }
)


class TableFontTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tab_id: DocsTabId
    body_ordinal: int = Field(ge=0, strict=True)
    row: int = Field(ge=0, strict=True)
    cell: int = Field(ge=0, strict=True)
    paragraph: int = Field(ge=0, strict=True)
    start: int = Field(ge=0, strict=True)
    split: int = Field(ge=0, strict=True)
    end: int = Field(ge=0, strict=True)
    residual_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    needs_change: bool = Field(strict=True)


class TableFontPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    targets: tuple[TableFontTarget, ...] = Field(max_length=2500)
    inventory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parents_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    skipped_paragraphs: int = Field(ge=0, le=100_000, strict=True)


class _CellParagraph(NamedTuple):
    row: int
    cell: int
    ordinal: int
    element: dict[str, Any]


def plan_table_fonts(document: dict[str, Any]) -> TableFontPlan:
    targets = []
    skipped = 0
    for tab_id, tab in _tabs(document).items():
        content = tab.get("body", {}).get("content", [])
        for ordinal, table in enumerate(content):
            if "table" not in table:
                continue
            skipped += _paragraph_count(table)
            if not _table_position_known(content, ordinal) or not _safe_table(table):
                continue
            for row, cell, paragraph, element in _paragraphs(table):
                if not _candidate(element):
                    continue
                skipped -= 1
                first = element["paragraph"]["elements"][0]["textRun"]["content"][0]
                targets.append(
                    TableFontTarget(
                        tab_id=tab_id,
                        body_ordinal=ordinal,
                        row=row,
                        cell=cell,
                        paragraph=paragraph,
                        start=element["startIndex"],
                        split=element["startIndex"] + utf16_length(first),
                        end=element["endIndex"] - 1,
                        residual_sha256=native_paragraph_font_sha256(element),
                        needs_change=not _has_font(element),
                    )
                )
    proof = tuple(targets)
    return TableFontPlan(
        targets=proof,
        inventory_sha256=_inventory(_project(document, proof, applied=False)),
        parents_sha256=_parents(document),
        skipped_paragraphs=skipped,
    )


def project_table_fonts(
    document: dict[str, Any], plan: TableFontPlan, *, applied: bool = False
) -> dict[str, Any]:
    if _parents(document) != plan.parents_sha256:
        raise ValueError
    result = _project(document, plan.targets, applied=applied)
    if _inventory(result) != plan.inventory_sha256:
        raise ValueError
    return result


def table_font_requests(plan: TableFontPlan) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for target in plan.targets:
        if not target.needs_change:
            continue
        ranges = (
            [(target.start, target.split), (target.split, target.end)]
            if target.split < target.end
            else [(target.start, target.end)]
        )
        requests.extend(
            {
                "updateTextStyle": {
                    "range": {"tabId": target.tab_id, "startIndex": left, "endIndex": right},
                    "textStyle": {"weightedFontFamily": {"fontFamily": TABLE_FONT_FAMILY}},
                    "fields": "weightedFontFamily.fontFamily",
                }
            }
            for left, right in ranges
        )
    return requests


def table_font_proof_matches(
    document: dict[str, Any] | None, plan: TableFontPlan, *, applied: bool
) -> bool:
    if document is None:
        return False
    try:
        project_table_fonts(document, plan, applied=applied)
    except KeyError, IndexError, TypeError, ValueError, AttributeError:
        return False
    return True


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _tabs(document: dict[str, Any]) -> dict[DocsTabId, dict[str, Any]]:
    result: dict[DocsTabId, dict[str, Any]] = {}
    pending = list(document["tabs"])
    while pending:
        tab = pending.pop(0)
        identifier = tab["tabProperties"]["tabId"]
        if not isinstance(identifier, str) or not identifier or identifier in result:
            raise ValueError
        result[DocsTabId(identifier)] = tab["documentTab"]
        if len(result) > MAX_DOC_TABS:
            raise ValueError
        pending.extend(tab.get("childTabs", []))
    return result


def _parents(document: dict[str, Any]) -> str:
    return _sha(
        [
            document.get("namedStyles"),
            {key: tab.get("namedStyles") for key, tab in _tabs(document).items()},
        ]
    )


def _paragraphs(table: dict[str, Any]) -> list[_CellParagraph]:
    result = []
    for row_index, row in enumerate(table["table"]["tableRows"]):
        for cell_index, cell in enumerate(row["tableCells"]):
            for paragraph_index, element in enumerate(cell["content"]):
                if "paragraph" in element:
                    result.append(_CellParagraph(row_index, cell_index, paragraph_index, element))
    return result


def _safe_table(table: dict[str, Any]) -> bool:
    if set(table) - {"startIndex", "endIndex", "table"}:
        return False
    value = table["table"]
    if set(value) - {"rows", "columns", "tableRows", "tableStyle"}:
        return False
    previous_row_end = table["startIndex"]
    for row in value["tableRows"]:
        if (
            set(row) - {"startIndex", "endIndex", "tableCells", "tableRowStyle"}
            or not _contains(table, row)
            or row["startIndex"] < previous_row_end
        ):
            return False
        previous_row_end = row["endIndex"]
        previous_cell_end = row["startIndex"]
        for cell in row["tableCells"]:
            if (
                set(cell) - {"startIndex", "endIndex", "content", "tableCellStyle"}
                or not _contains(row, cell)
                or cell["startIndex"] < previous_cell_end
            ):
                return False
            previous_cell_end = cell["endIndex"]
            previous_end = cell["startIndex"]
            for element in cell["content"]:
                if not _contains(cell, element) or element["startIndex"] < previous_end:
                    return False
                previous_end = element["endIndex"]
    return True


def _contains(parent: dict[str, Any], child: dict[str, Any]) -> bool:
    bounds = [
        parent.get("startIndex"),
        parent.get("endIndex"),
        child.get("startIndex"),
        child.get("endIndex"),
    ]
    return all(type(value) is int for value in bounds) and bool(
        parent["startIndex"] <= child["startIndex"] < child["endIndex"] <= parent["endIndex"]
    )


def _paragraph_count(value: object) -> int:
    match value:
        case dict() as mapping:
            return int("paragraph" in mapping) + sum(
                _paragraph_count(child) for child in mapping.values()
            )
        case list() as values:
            return sum(_paragraph_count(child) for child in values)
        case _:
            return 0


def _table_position_known(content: list[dict[str, Any]], ordinal: int) -> bool:
    table = content[ordinal]
    if type(table.get("startIndex")) is not int or type(table.get("endIndex")) is not int:
        return False
    return all(
        type(element.get("endIndex")) is int and element["endIndex"] <= table["startIndex"]
        for element in content[:ordinal]
    ) and all(
        type(element.get("startIndex")) is int and element["startIndex"] >= table["endIndex"]
        for element in content[ordinal + 1 :]
    )


def _candidate(element: dict[str, Any]) -> bool:
    paragraph = element["paragraph"]
    if set(element) - {"startIndex", "endIndex", "paragraph"} or set(paragraph) - {
        "elements",
        "paragraphStyle",
    }:
        return False
    parts = paragraph.get("elements", [])
    if not parts:
        return False
    for part in parts:
        if set(part) - {"startIndex", "endIndex", "textRun"} or "textRun" not in part:
            return False
        run = part["textRun"]
        if set(run) - {"content", "textStyle"}:
            return False
        style = run.get("textStyle", {})
        if not isinstance(style, dict) or set(style) - _STYLE_KEYS:
            return False
        text = run.get("content")
        if not isinstance(text, str) or any(
            c.isalpha() and "LATIN" not in unicodedata.name(c, "") for c in text
        ):
            return False
        if text != "\n":
            family = style.get("weightedFontFamily", {})
            font = family.get("fontFamily") if isinstance(family, dict) else None
            if (
                not isinstance(font, str)
                or not font
                or any(
                    token in font.casefold()
                    for token in (
                        "mono",
                        "courier",
                        "consolas",
                        "menlo",
                        "code",
                        "math",
                        "symbol",
                        "monaco",
                        "andale",
                        "cascadia",
                    )
                )
            ):
                return False
            if style.get("baselineOffset", "NONE") != "NONE":
                return False
    if not "".join(part["textRun"]["content"] for part in parts).strip():
        return False
    native_paragraph_font_sha256(element)
    return True


def _project(
    document: dict[str, Any], targets: tuple[TableFontTarget, ...], *, applied: bool
) -> dict[str, Any]:
    result = deepcopy(document)
    tabs = _tabs(result)
    identities = {(t.tab_id, t.body_ordinal, t.row, t.cell, t.paragraph) for t in targets}
    if len(identities) != len(targets):
        raise ValueError
    for target in targets:
        element = _at(tabs, target)
        text = "".join(part["textRun"]["content"] for part in element["paragraph"]["elements"])
        if applied and not _has_font(element):
            raise ValueError
        if (
            element["startIndex"] != target.start
            or element["endIndex"] != target.end + 1
            or not target.start < target.split <= target.end
            or target.split != target.start + utf16_length(text[0])
            or native_paragraph_font_sha256(element) != target.residual_sha256
        ):
            raise ValueError
        element.clear()
        element.update(
            {
                "startIndex": target.start,
                "endIndex": target.end + 1,
                "fontResidualSha256": target.residual_sha256,
            }
        )
    return result


def _at(tabs: dict[DocsTabId, dict[str, Any]], target: TableFontTarget) -> dict[str, Any]:
    element: dict[str, Any] = tabs[target.tab_id]["body"]["content"][target.body_ordinal]["table"][
        "tableRows"
    ][target.row]["tableCells"][target.cell]["content"][target.paragraph]
    return element


def _has_font(element: dict[str, Any]) -> bool:
    return all(
        part["textRun"].get("textStyle", {}).get("weightedFontFamily", {}).get("fontFamily")
        == TABLE_FONT_FAMILY
        for part in element["paragraph"]["elements"]
        if part["textRun"]["content"] != "\n"
    )


def _inventory(document: dict[str, Any]) -> str:
    return _sha(
        [
            (tab_id, ordinal, element)
            for tab_id, tab in _tabs(document).items()
            for ordinal, element in enumerate(tab.get("body", {}).get("content", []))
            if "table" in element
        ]
    )
