from dataclasses import dataclass, replace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.document_transform.ir import utf16_length
from app.document_transform.readability_json import bodies, content, obj
from app.document_transform.readability_types import ReadabilityParagraph
from app.identifiers import DocsTabId


_MIN_LIST_ITEMS = 2


class _Bullet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    list_id: str = Field(alias="listId", min_length=1)
    nesting_level: int = Field(default=0, alias="nestingLevel")
    text_style: dict[str, JsonValue] = Field(default_factory=dict, alias="textStyle")


class _ListParagraph(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    bullet: _Bullet | None = None


class _ListElement(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    start_index: int | None = Field(default=None, alias="startIndex")
    end_index: int | None = Field(default=None, alias="endIndex")
    paragraph: _ListParagraph | None = None


class _ListMarker(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    glyph_type: str | None = Field(default=None, alias="glyphType")
    glyph_symbol: str | None = Field(default=None, alias="glyphSymbol")
    glyph_format: str = Field(alias="glyphFormat")
    start_number: int | None = Field(default=None, alias="startNumber")


class _ListProperties(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    nesting_levels: list[_ListMarker] = Field(alias="nestingLevels", min_length=1)


class _GeneratedList(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    list_properties: _ListProperties = Field(alias="listProperties")


@dataclass(frozen=True, slots=True)
class _ListGroup:
    tab_id: DocsTabId
    role: Literal["bullet", "numbered"]
    starts: tuple[int, ...]
    end: int


def _groups(targets: tuple[ReadabilityParagraph, ...]) -> tuple[_ListGroup, ...]:
    groups: list[_ListGroup] = []
    for target in sorted(targets, key=lambda item: (item.tab_id, item.start)):
        index = target.start
        for part in target.parts:
            end = index + utf16_length(part.text)
            if part.role in {"bullet", "numbered"}:
                if (
                    groups
                    and groups[-1].tab_id == target.tab_id
                    and groups[-1].role == part.role
                    and groups[-1].end + 1 == index
                ):
                    groups[-1] = replace(groups[-1], starts=(*groups[-1].starts, index), end=end)
                else:
                    groups.append(
                        _ListGroup(
                            target.tab_id,
                            "numbered" if part.role == "numbered" else "bullet",
                            (index,),
                            end,
                        )
                    )
            index = end + 1
    if any(len(group.starts) < _MIN_LIST_ITEMS for group in groups):
        msg = "Lists require at least two adjacent items"
        raise ValueError(msg)
    return tuple(groups)


def validate_readable_lists(
    document: dict[str, JsonValue], targets: tuple[ReadabilityParagraph, ...]
) -> None:
    tabs = dict(bodies(document))
    for group in _groups(targets):
        for value in content(tabs[group.tab_id]):
            element = _ListElement.model_validate(value)
            if (
                (element.end_index == group.starts[0] or element.start_index == group.end + 1)
                and element.paragraph is not None
                and element.paragraph.bullet is not None
            ):
                # Docs can automatically join a matching preceding list. Preserve
                # authored numbering instead of silently extending that list.
                msg = "Preserve authored lists next to a proposed list"
                raise ValueError(msg)


def readable_list_requests(
    targets: tuple[ReadabilityParagraph, ...], *, grouped: bool
) -> list[dict[str, JsonValue]]:
    requests: list[dict[str, JsonValue]] = []
    if not grouped:
        # Version 1 checkpoints persisted per-item requests in proposal order.
        # Reconstruct them exactly so an interrupted write can still resume.
        for target in targets:
            index = target.start
            for part in target.parts:
                end = index + utf16_length(part.text)
                if part.role in {"bullet", "numbered"}:
                    requests.append(
                        _list_request(
                            target.tab_id,
                            index,
                            end,
                            role="numbered" if part.role == "numbered" else "bullet",
                        )
                    )
                index = end + 1
        return requests
    return [
        _list_request(group.tab_id, group.starts[0], group.end, role=group.role)
        for group in _groups(targets)
    ]


def _list_request(
    tab_id: DocsTabId, start: int, end: int, *, role: Literal["bullet", "numbered"]
) -> dict[str, JsonValue]:
    return {
        "createParagraphBullets": {
            "range": {"startIndex": start, "endIndex": end, "tabId": tab_id},
            "bulletPreset": "NUMBERED_DECIMAL_ALPHA_ROMAN"
            if role == "numbered"
            else "BULLET_DISC_CIRCLE_SQUARE",
        }
    }


def verify_readable_lists(
    document: dict[str, JsonValue],
    targets: tuple[ReadabilityParagraph, ...],
    *,
    original_lists: dict[str, dict[str, JsonValue]],
) -> None:
    tabs = dict(bodies(document))
    seen: set[tuple[DocsTabId, str]] = set()
    for group in _groups(targets):
        tab = tabs[group.tab_id]
        elements = (_ListElement.model_validate(value) for value in content(tab))
        paragraphs = {
            element.start_index: element.paragraph
            for element in elements
            if element.paragraph is not None
        }
        identifiers = {_bullet_id(paragraphs[start]) for start in group.starts}
        if len(identifiers) != 1:
            msg = "Adjacent list items must share one native list"
            raise ValueError(msg)
        identifier = identifiers.pop()
        identity = (group.tab_id, identifier)
        if identity in seen or identifier in original_lists[str(group.tab_id)]:
            msg = "Generated lists must not continue another list"
            raise ValueError(msg)
        seen.add(identity)
        definition = _GeneratedList.model_validate(obj(tab["lists"])[identifier])
        _verify_marker(definition.list_properties.nesting_levels[0], role=group.role)


def _bullet_id(paragraph: _ListParagraph) -> str:
    bullet = paragraph.bullet
    if bullet is None or bullet.nesting_level != 0:
        raise ValueError
    return bullet.list_id


def _verify_marker(first: _ListMarker, *, role: Literal["bullet", "numbered"]) -> None:
    if role == "numbered":
        valid = (
            first.glyph_type == "DECIMAL"
            and first.glyph_format == "%0."
            and first.start_number == 1
            and first.glyph_symbol is None
        )
    else:
        valid = (
            first.glyph_symbol == "●"
            and first.glyph_format == "%0"
            and first.glyph_type in {None, "GLYPH_TYPE_UNSPECIFIED"}
        )
    if not valid:
        msg = "Native list marker does not match the planned preset"
        raise ValueError(msg)
