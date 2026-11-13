from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.document_transform.readability_geometry import readable_table_width
from app.document_transform.readability_json import array, bodies, content, integer, obj, plain_text
from app.document_transform.readability_types import (
    TERMINAL_PROOF_VERSION,
    ReadabilityCheckpoint,
    ReadabilityParagraph,
    ReadabilityTerminal,
)


_MINIMUM_TAB_COUNT = 2
_MINIMUM_TERMINAL_ELEMENTS = 2
type NativeReadabilityRequest = dict[str, JsonValue]


class _TerminalSpacing(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    magnitude: float = Field(default=0, ge=0, allow_inf_nan=False)
    unit: Literal["PT"]


class _TerminalSourceStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    named_style: Literal["NORMAL_TEXT"] = Field(default="NORMAL_TEXT", alias="namedStyleType")
    direction: Literal["LEFT_TO_RIGHT", "RIGHT_TO_LEFT"] | None = None
    alignment: Literal["START", "END", "CENTER", "JUSTIFIED"] | None = None
    line_spacing: float | None = Field(default=None, ge=0, alias="lineSpacing")
    above_spacing: _TerminalSpacing | None = Field(default=None, alias="spaceAbove")
    below_spacing: _TerminalSpacing | None = Field(default=None, alias="spaceBelow")
    keep_with_next: Literal[False] = Field(default=False, alias="keepWithNext")
    keep_together: bool = Field(default=False, alias="keepLinesTogether")
    avoid_widow: bool = Field(default=False, alias="avoidWidowAndOrphan")


def plan_readable_terminals(
    document: dict[str, JsonValue], paragraphs: tuple[ReadabilityParagraph, ...]
) -> tuple[ReadabilityTerminal, ...]:
    # Splitting a paragraph can push Docs' mandatory newline after the final
    # table onto an otherwise empty page. Compact only that unstyled newline;
    # authored spacing and the complete table remain unchanged.
    tabs = tuple(bodies(document))
    if len(tabs) < _MINIMUM_TAB_COUNT:
        return ()
    expanded = {paragraph.tab_id for paragraph in paragraphs if len(paragraph.parts) > 1}
    result = []
    for tab_id, tab in tabs:
        if tab_id not in expanded:
            continue
        terminal = _unstyled_table_tail(tab)
        if terminal is not None:
            try:
                readable_table_width(document, tab, start=integer(terminal["startIndex"]))
            except ValueError:
                continue
            result.append(
                ReadabilityTerminal(
                    tab_id=tab_id,
                    start=integer(terminal["startIndex"]),
                    end=integer(terminal["endIndex"]),
                    source=deepcopy(terminal),
                )
            )
    return tuple(result)


def readable_terminal_requests(
    terminals: tuple[ReadabilityTerminal, ...],
) -> list[NativeReadabilityRequest]:
    return [
        {
            "updateTextStyle": {
                "range": {"startIndex": item.start, "endIndex": item.end, "tabId": item.tab_id},
                "textStyle": {"fontSize": {"magnitude": 1, "unit": "PT"}},
                "fields": "fontSize",
            }
        }
        for item in terminals
    ]


def validated_readable_terminal_requests(
    document: dict[str, JsonValue], checkpoint: ReadabilityCheckpoint
) -> list[NativeReadabilityRequest]:
    if (
        checkpoint.version >= TERMINAL_PROOF_VERSION
        and plan_readable_terminals(document, checkpoint.paragraphs) != checkpoint.terminals
    ):
        msg = "Terminal paragraph proof is not bound to the current source"
        raise ValueError(msg)
    return readable_terminal_requests(checkpoint.terminals)


def project_readable_terminals(
    document: dict[str, JsonValue],
    terminals: tuple[ReadabilityTerminal, ...],
    *,
    applied: bool,
) -> None:
    tabs = dict(bodies(document))
    for item in terminals:
        elements = content(tabs[item.tab_id])
        observed = obj(elements[-1])
        expected = deepcopy(item.source)
        if (
            expected.get("startIndex") != item.start
            or expected.get("endIndex") != item.end
            or item.end != item.start + 1
            or _unstyled_table_tail({"body": {"content": [elements[-2], expected]}}) != expected
        ):
            msg = "Terminal paragraph source proof changed"
            raise ValueError(msg)
        if applied:
            run = obj(array(obj(expected["paragraph"])["elements"])[0])
            obj(run["textRun"])["textStyle"] = {"fontSize": {"magnitude": 1, "unit": "PT"}}
        if observed != expected:
            msg = "Terminal paragraph font or protected content changed"
            raise ValueError(msg)
        elements[-1] = deepcopy(item.source)


def _unstyled_table_tail(tab: dict[str, JsonValue]) -> dict[str, JsonValue] | None:
    elements = content(tab)
    if len(elements) < _MINIMUM_TERMINAL_ELEMENTS or "table" not in obj(elements[-2]):
        return None
    terminal = obj(elements[-1])
    try:
        if plain_text(terminal) != "\n":
            return None
        paragraph = obj(terminal["paragraph"])
        runs = array(paragraph["elements"])
        _TerminalSourceStyle.model_validate(
            obj(paragraph["paragraphStyle"]) if "paragraphStyle" in paragraph else {}
        )
        if len(runs) != 1 or _run_style(runs[0]):
            return None
    except KeyError, TypeError, ValueError:
        return None
    return terminal


def _run_style(value: JsonValue) -> dict[str, JsonValue]:
    run = obj(obj(value)["textRun"])
    return obj(run["textStyle"]) if "textStyle" in run else {}
