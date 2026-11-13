from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.document_transform.errors import UnsupportedSourceError
from app.document_transform.ir import (
    BlockKind,
    DocumentIR,
    TextBlock,
    parse_google_document,
    utf16_length,
)
from app.document_transform.native_font_proof import NativeFontBlockId
from app.document_transform.readability_json import array, integer, obj, plain_text
from app.identifiers import DocsTabId


_MAX_LINE_SPACING = 1000
_MAX_FONT_SIZE = 200
_MIN_CONTENT_ELEMENTS = 2
_MAX_CONTENT_ELEMENTS = 2000
_MAX_PROOF_BYTES = 8192


class _NamedStyle(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    name: str = Field(alias="namedStyleType")
    paragraph_style: dict[str, JsonValue] = Field(default_factory=dict, alias="paragraphStyle")
    text_style: dict[str, JsonValue] = Field(default_factory=dict, alias="textStyle")


class _NamedStyles(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    styles: list[_NamedStyle] = Field(default_factory=list)


class _NormalText(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow")

    bold: JsonValue = False
    italic: JsonValue = False
    underline: JsonValue = False
    strikethrough: JsonValue = False
    small_caps: JsonValue = Field(default=False, alias="smallCaps")
    background_color: JsonValue = Field(default_factory=dict, alias="backgroundColor")
    baseline_offset: JsonValue = Field(default="NONE", alias="baselineOffset")
    link: JsonValue = None
    font_size: JsonValue = Field(default_factory=dict, alias="fontSize")


class _NormalParagraph(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    named_style_type: JsonValue = Field(default=None, alias="namedStyleType")
    alignment: JsonValue = None
    direction: JsonValue = None
    spacing_mode: JsonValue = Field(default=None, alias="spacingMode")
    line_spacing: JsonValue = Field(default=115, alias="lineSpacing")
    space_above: JsonValue = Field(default=None, alias="spaceAbove")
    space_below: JsonValue = Field(default=None, alias="spaceBelow")
    indent_start: JsonValue = Field(default=None, alias="indentStart")
    indent_end: JsonValue = Field(default=None, alias="indentEnd")
    indent_first_line: JsonValue = Field(default=None, alias="indentFirstLine")
    keep_with_next: JsonValue = Field(default=False, alias="keepWithNext")
    keep_lines_together: JsonValue = Field(default=False, alias="keepLinesTogether")
    avoid_widow_and_orphan: JsonValue = Field(default=None, alias="avoidWidowAndOrphan")
    page_break_before: JsonValue = Field(default=False, alias="pageBreakBefore")
    shading: JsonValue = Field(default_factory=dict)
    border_top: JsonValue = Field(default=None, alias="borderTop")
    border_bottom: JsonValue = Field(default=None, alias="borderBottom")
    border_left: JsonValue = Field(default=None, alias="borderLeft")
    border_right: JsonValue = Field(default=None, alias="borderRight")
    border_between: JsonValue = Field(default=None, alias="borderBetween")


class _Dimension(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    magnitude: JsonValue = 0
    unit: JsonValue = None


class _Border(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    color: JsonValue = None
    width: JsonValue = None
    padding: JsonValue = None
    dash_style: JsonValue = Field(default=None, alias="dashStyle")


class _Content(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    content: list[dict[str, JsonValue]]


class _DocumentTab(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    body: _Content
    document_style: dict[str, JsonValue] = Field(default_factory=dict, alias="documentStyle")
    named_styles: _NamedStyles = Field(default_factory=_NamedStyles, alias="namedStyles")


class _TabProperties(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    tab_id: DocsTabId = Field(alias="tabId")


class _Tab(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    properties: _TabProperties = Field(alias="tabProperties")
    document: _DocumentTab = Field(alias="documentTab")
    child_tabs: list[JsonValue] = Field(default_factory=list, alias="childTabs")


class _Native(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    tabs: list[_Tab] = Field(default_factory=list)
    document_style: dict[str, JsonValue] | None = Field(default=None, alias="documentStyle")
    named_styles: _NamedStyles | None = Field(default=None, alias="namedStyles")


class _DocumentStyle(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    document_format: dict[str, JsonValue] = Field(default_factory=dict, alias="documentFormat")


class _DocumentFormat(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    document_mode: JsonValue = Field(default=None, alias="documentMode")


class _TailStyle(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    name: JsonValue = Field(default="NORMAL_TEXT", alias="namedStyleType")
    direction: JsonValue = None


class _TailText(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    content: JsonValue
    text_style: JsonValue = Field(default_factory=dict, alias="textStyle")


class _TailParagraph(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    elements: list[dict[str, JsonValue]]
    paragraph_style: _TailStyle = Field(default_factory=_TailStyle, alias="paragraphStyle")


@dataclass(frozen=True, slots=True)
class TerminalBlankPlan:
    tab_id: DocsTabId
    block_id: NativeFontBlockId
    spacer_sha256: str
    target_start: int
    expected_json: str
    needs_write: bool

    @property
    def requests(self) -> list[dict[str, JsonValue]]:
        if not self.needs_write:
            return []
        return [
            {
                "updateTextStyle": {
                    "range": {
                        "tabId": self.tab_id,
                        "startIndex": self.target_start,
                        "endIndex": self.target_start + 1,
                    },
                    "textStyle": {"fontSize": {"magnitude": 1, "unit": "PT"}},
                    "fields": "fontSize",
                }
            }
        ]


@dataclass(frozen=True, slots=True)
class _TerminalBody:
    tab_id: DocsTabId
    content: tuple[dict[str, JsonValue], ...]
    normal: dict[str, JsonValue]


def plan_terminal_blank(
    native: dict[str, JsonValue] | None, document: DocumentIR
) -> TerminalBlankPlan | None:
    if native is None:
        return None
    try:
        return _terminal_plan(native, document)
    except KeyError, TypeError, ValueError, OverflowError, UnsupportedSourceError:
        return None


def terminal_blank_requests(
    native: dict[str, JsonValue], document: DocumentIR
) -> list[dict[str, JsonValue]]:
    plan = plan_terminal_blank(native, document)
    if (
        plan is None
        or not next(
            block for block in document.blocks if block.id == plan.block_id
        ).suppress_space_below
    ):
        return []
    return plan.requests


def verify_terminal_blank(
    source: dict[str, JsonValue], actual: dict[str, JsonValue], document: DocumentIR
) -> bool:
    plan = plan_terminal_blank(source, document)
    if (
        plan is None
        or not next(
            block for block in document.blocks if block.id == plan.block_id
        ).suppress_space_below
    ):
        return True
    try:
        body = obj(obj(array(actual["tabs"])[0])["documentTab"])
        terminal = array(obj(body["body"])["content"])[-1]
        return json.dumps(terminal, sort_keys=True, separators=(",", ":")) == plan.expected_json
    except KeyError, IndexError, TypeError, ValueError:
        return False


def _terminal_plan(native: dict[str, JsonValue], document: DocumentIR) -> TerminalBlankPlan | None:
    body = _terminal_body(native, document)
    if body is None or not _terminal_is_plain(body.content[-1]):
        return None
    block = _last_prose(body, document)
    displacement = _text_displacement(native, document)
    if block is None or displacement is None:
        return None
    terminal = body.content[-1]
    start = integer(terminal["startIndex"]) + displacement
    proof = json.dumps(
        {"tab_id": body.tab_id, "terminal": terminal, "normal": body.normal},
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(proof.encode()) > _MAX_PROOF_BYTES:
        return None
    return TerminalBlankPlan(
        tab_id=body.tab_id,
        block_id=NativeFontBlockId(block.id),
        spacer_sha256=hashlib.sha256(proof.encode()).hexdigest(),
        target_start=start,
        expected_json=json.dumps(
            _shifted_terminal(terminal, start), sort_keys=True, separators=(",", ":")
        ),
        needs_write=not bool(
            _TailText.model_validate(
                obj(array(obj(terminal["paragraph"])["elements"])[0])["textRun"]
            ).text_style
        ),
    )


def _terminal_body(native: dict[str, JsonValue], document: DocumentIR) -> _TerminalBody | None:
    source = _Native.model_validate(native)
    if (
        document.source_format != "google_doc"
        or len(source.tabs) != 1
        or len(document.tab_ids) != 1
    ):
        return None
    tab = source.tabs[0]
    if tab.child_tabs or tab.properties.tab_id != document.tab_ids[0]:
        return None
    body = tab.document
    style = _DocumentStyle.model_validate(
        source.document_style if source.document_style is not None else body.document_style
    )
    if _DocumentFormat.model_validate(style.document_format).document_mode != "PAGES":
        return None
    content = tuple(body.body.content)
    if (
        not _MIN_CONTENT_ELEMENTS <= len(content) <= _MAX_CONTENT_ELEMENTS
        or sum("sectionBreak" in node for node in content) > 1
    ):
        return None
    named_styles = source.named_styles if source.named_styles is not None else body.named_styles
    normals = [value for value in named_styles.styles if value.name == "NORMAL_TEXT"]
    if len(normals) != 1 or not _normal_is_plain(normals[0]):
        return None
    return _TerminalBody(
        tab_id=document.tab_ids[0],
        content=content,
        normal=normals[0].model_dump(mode="json", by_alias=True, exclude_unset=True),
    )


def _normal_is_plain(normal: _NamedStyle) -> bool:
    paragraph = _NormalParagraph.model_validate(normal.paragraph_style)
    text = _NormalText.model_validate(normal.text_style)
    if "link" in text.model_fields_set or text.background_color != {}:
        return False
    if (
        any(
            value is not False
            for value in (
                text.bold,
                text.italic,
                text.underline,
                text.strikethrough,
                text.small_caps,
            )
        )
        or text.baseline_offset != "NONE"
    ):
        return False
    size = _Dimension.model_validate(text.font_size)
    magnitude = size.magnitude
    return (
        _normal_spacing_is_plain(paragraph)
        and size.unit == "PT"
        and isinstance(magnitude, (int, float))
        and not isinstance(magnitude, bool)
        and 1 <= magnitude <= _MAX_FONT_SIZE
    )


def _normal_spacing_is_plain(paragraph: _NormalParagraph) -> bool:
    line_spacing = paragraph.line_spacing
    if (
        not isinstance(line_spacing, (int, float))
        or isinstance(line_spacing, bool)
        or not 0 < line_spacing <= _MAX_LINE_SPACING
        or not math.isfinite(line_spacing)
    ):
        return False
    if any(
        value is not False
        for value in (
            paragraph.page_break_before,
            paragraph.keep_with_next,
            paragraph.keep_lines_together,
        )
    ):
        return False
    if any(
        value is not None and not _zero_points(value)
        for value in (
            paragraph.space_above,
            paragraph.space_below,
            paragraph.indent_start,
            paragraph.indent_end,
            paragraph.indent_first_line,
        )
    ):
        return False
    if paragraph.shading not in ({}, {"backgroundColor": {}}):
        return False
    return all(
        value is None or _border_is_plain(value)
        for value in (
            paragraph.border_top,
            paragraph.border_bottom,
            paragraph.border_left,
            paragraph.border_right,
            paragraph.border_between,
        )
    )


def _border_is_plain(value: JsonValue) -> bool:
    border = _Border.model_validate(value)
    return _zero_points(border.width) and _zero_points(border.padding)


def _zero_points(value: JsonValue) -> bool:
    if not isinstance(value, dict):
        return False
    dimension = _Dimension.model_validate(value)
    return (
        dimension.unit == "PT"
        and type(dimension.magnitude) in {int, float}
        and dimension.magnitude == 0
    )


def _terminal_is_plain(terminal: dict[str, JsonValue]) -> bool:
    if set(terminal) != {"startIndex", "endIndex", "paragraph"}:
        return False
    start, end = integer(terminal["startIndex"]), integer(terminal["endIndex"])
    paragraph = _TailParagraph.model_validate(terminal["paragraph"])
    if paragraph.paragraph_style.name != "NORMAL_TEXT":
        return False
    elements = paragraph.elements
    if end != start + 1 or len(elements) != 1:
        return False
    run = obj(elements[0])
    if (
        set(run) - {"startIndex", "endIndex", "textRun"}
        or integer(run["startIndex"]) != start
        or integer(run["endIndex"]) != end
    ):
        return False
    text = _TailText.model_validate(run["textRun"])
    return text.content == "\n" and text.text_style in (
        {},
        {"fontSize": {"magnitude": 1, "unit": "PT"}},
    )


def _last_prose(body: _TerminalBody, document: DocumentIR) -> TextBlock | None:
    previous = body.content[-2]
    matching = [
        block
        for block in document.blocks
        if block.source_range is not None
        and block.source_range.tab_id == body.tab_id
        and block.source_range.segment_id is None
        and block.source_range.start_index == integer(previous["startIndex"])
    ]
    if len(matching) != 1:
        return None
    block = matching[0]
    source = block.source_range
    if (
        source is None
        or source.end_index + 1 != integer(body.content[-1]["startIndex"])
        or not block.editable
        or block.native_content
        or block.kind is not BlockKind.PARAGRAPH
    ):
        return None
    if (
        hashlib.sha256(plain_text(previous).removesuffix("\n").encode()).hexdigest()
        != block.source_style_text_sha256
    ):
        return None
    return block


def _text_displacement(native: dict[str, JsonValue], document: DocumentIR) -> int | None:
    # Never infer native object widths from the length of their visible labels.
    original = parse_google_document(native, title=document.title)
    by_id = {block.id: block for block in original.blocks}
    displacement = 0
    for block in document.blocks:
        location = block.source_range
        if (
            not block.editable
            or location is None
            or location.segment_id is not None
            or location.tab_id != document.tab_ids[0]
        ):
            continue
        source = by_id.get(block.id)
        if (
            source is None
            or not source.editable
            or source.native_content
            or source.source_range != location
            or source.sha256 != block.source_style_text_sha256
        ):
            return None
        displacement += utf16_length(block.text) - utf16_length(source.text)
    return displacement


def _shifted_terminal(terminal: dict[str, JsonValue], start: int) -> dict[str, JsonValue]:
    expected = deepcopy(terminal)
    expected["startIndex"], expected["endIndex"] = start, start + 1
    run = obj(array(obj(expected["paragraph"])["elements"])[0])
    run["startIndex"], run["endIndex"] = start, start + 1
    obj(run["textRun"])["textStyle"] = {"fontSize": {"magnitude": 1, "unit": "PT"}}
    return expected
