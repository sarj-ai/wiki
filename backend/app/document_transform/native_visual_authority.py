from dataclasses import replace
import math
from typing import Any, TypeIs

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel, StrictInt

from app.document_transform.errors import InPlaceNeedsReviewError
from app.document_transform.ir import MAX_DOC_TABS, BlockKind, DocumentIR
from app.document_transform.native_font_context import native_normal_text_style


class _VisualRecord(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")


class _VisualTextStyle(_VisualRecord):
    font_size: JsonValue = Field(default=None, alias="fontSize")
    foreground_color: JsonValue = Field(default=None, alias="foregroundColor")
    link: JsonValue = None


class _VisualTextRun(_VisualRecord):
    content: str = ""
    text_style: _VisualTextStyle = Field(default_factory=_VisualTextStyle, alias="textStyle")


class _VisualRun(_VisualRecord):
    text_run: _VisualTextRun | None = Field(default=None, alias="textRun")


class _VisualParagraphStyle(_VisualRecord):
    named_style_type: str = Field(default="NORMAL_TEXT", alias="namedStyleType")


class _VisualParagraph(_VisualRecord):
    paragraph_style: _VisualParagraphStyle = Field(
        default_factory=_VisualParagraphStyle, alias="paragraphStyle"
    )
    elements: list[_VisualRun] = Field(default_factory=list)


class _VisualElement(_VisualRecord):
    start_index: StrictInt | None = Field(default=None, alias="startIndex")
    paragraph: _VisualParagraph | None = None


class _VisualBody(_VisualRecord):
    content: list[_VisualElement] = Field(default_factory=list)


class _VisualTabContent(_VisualRecord):
    body: _VisualBody = Field(default_factory=_VisualBody)


class _VisualTabProperties(_VisualRecord):
    tab_id: str = Field(alias="tabId", min_length=1)


class _VisualTab(_VisualRecord):
    tab_properties: _VisualTabProperties = Field(alias="tabProperties")
    document_tab: _VisualTabContent = Field(alias="documentTab")
    child_tabs: list[_VisualTab] = Field(default_factory=list, alias="childTabs")


class _VisualTabs(RootModel[list[_VisualTab]]):
    model_config = ConfigDict(strict=True)


def visual_authority_block_ids(
    document: dict[str, Any] | None, original: DocumentIR
) -> tuple[str, ...]:
    try:
        return _visual_authority_block_ids(document, original)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as error:
        raise InPlaceNeedsReviewError from error


def _visual_authority_block_ids(
    document: dict[str, Any] | None, original: DocumentIR
) -> tuple[str, ...]:
    if document is None:
        return ()
    tabs = _visual_tabs(document.get("tabs", []))
    selected = []
    for block in original.blocks:
        source = block.source_range
        if not block.editable or block.kind is not BlockKind.PARAGRAPH or source is None:
            continue
        paragraph = _normal_paragraph(tabs[source.tab_id], start_index=source.start_index)
        if paragraph is None:
            continue
        if _has_authored_presentation(
            paragraph,
            parent=native_normal_text_style(document, source.tab_id),
            protected_suffix=block.protected_suffix,
        ):
            selected.append(block.id)
    return tuple(selected)


def _visual_tabs(raw: JsonValue) -> dict[str, _VisualTabContent]:
    pending = _VisualTabs.model_validate(raw).root
    tabs: dict[str, _VisualTabContent] = {}
    visited = 0
    while pending:
        visited += 1
        tab = pending.pop()
        tab_id = tab.tab_properties.tab_id
        if visited > MAX_DOC_TABS or tab_id in tabs:
            raise ValueError
        tabs[tab_id] = tab.document_tab
        pending.extend(tab.child_tabs)
    return tabs


def _normal_paragraph(tab: _VisualTabContent, *, start_index: int) -> _VisualParagraph | None:
    matches = [element for element in tab.body.content if element.start_index == start_index]
    if len(matches) != 1:
        raise ValueError
    paragraph = matches[0].paragraph
    if paragraph is None or paragraph.paragraph_style.named_style_type != "NORMAL_TEXT":
        return None
    return paragraph


def _has_authored_presentation(
    paragraph: _VisualParagraph,
    *,
    parent: dict[str, JsonValue] | None,
    protected_suffix: str | None,
) -> bool:
    for part in paragraph.elements:
        run = part.text_run
        if run is None or not run.content.removesuffix("\n"):
            continue
        style = run.text_style
        if protected_suffix is not None and style.link is not None:
            continue
        if not style.model_fields_set.intersection({"font_size", "foreground_color"}):
            continue
        if parent is None or _differs(style.model_dump(by_alias=True, exclude_unset=True), parent):
            return True
    return False


def _differs(style: dict[str, Any], parent: dict[str, Any]) -> bool:
    for key in ("fontSize", "foregroundColor"):
        if key in style:
            actual, inherited = _presentation(key, style[key]), _presentation(key, parent.get(key))
            if actual is None or inherited is None or actual != inherited:
                return True
    return False


def _presentation(key: str, value: object) -> tuple[float, ...] | None:
    if not isinstance(value, dict):
        return None
    if key == "fontSize":
        magnitude = value.get("magnitude")
        if (
            set(value) == {"magnitude", "unit"}
            and value.get("unit") == "PT"
            and _finite(magnitude)
            and magnitude > 0
        ):
            return (float(magnitude),)
        return None
    color = value.get("color")
    if set(value) != {"color"} or not isinstance(color, dict) or set(color) != {"rgbColor"}:
        return None
    rgb = color.get("rgbColor")
    if not isinstance(rgb, dict) or not set(rgb) <= {"red", "green", "blue"}:
        return None
    result = []
    for name in ("red", "green", "blue"):
        component = rgb.get(name, 0)
        if not _finite(component) or not 0 <= component <= 1:
            return None
        result.append(float(component))
    return tuple(result)


def _finite(value: object) -> TypeIs[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def apply_visual_authority(
    document: DocumentIR, block_ids: tuple[str, ...], *, planner: bool = False
) -> DocumentIR:
    selected = set(block_ids)
    if len(selected) != len(block_ids) or not selected <= {
        block.id for block in document.blocks if block.kind is BlockKind.PARAGRAPH
    }:
        raise ValueError
    return replace(
        document,
        blocks=tuple(
            replace(
                block,
                preserved_visual_authority=block.id in selected,
                editable=False if planner and block.id in selected else block.editable,
            )
            for block in document.blocks
        ),
    )
