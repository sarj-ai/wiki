from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.document_transform.readability_json import content, obj


_MIN_PRINTABLE_WIDTH = 150
_MAX_PRINTABLE_WIDTH = 1500


class _GeometryModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)


class _Dimension(_GeometryModel):
    magnitude: float = Field(default=0, ge=0, allow_inf_nan=False)
    unit: Literal["PT"]


class _PageSize(_GeometryModel):
    width: _Dimension = _Dimension(magnitude=612, unit="PT")
    height: _Dimension = _Dimension(magnitude=792, unit="PT")


class _Column(_GeometryModel):
    width: _Dimension | None = None


class _Position(_GeometryModel):
    start: int = Field(default=0, ge=0, alias="startIndex")


class _DocumentFormat(_GeometryModel):
    mode: Literal["PAGES", "PAGELESS", "DOCUMENT_MODE_UNSPECIFIED"] = Field(
        default="PAGES", alias="documentMode"
    )


class _Geometry(_GeometryModel):
    page_size: _PageSize = Field(default=_PageSize(), alias="pageSize")
    margin_left: _Dimension = Field(default=_Dimension(magnitude=72, unit="PT"), alias="marginLeft")
    margin_right: _Dimension = Field(
        default=_Dimension(magnitude=72, unit="PT"), alias="marginRight"
    )
    flipped: bool = Field(default=False, alias="flipPageOrientation")
    columns: list[_Column] = Field(default_factory=list, alias="columnProperties")
    document_format: _DocumentFormat = Field(default=_DocumentFormat(), alias="documentFormat")


def readable_table_width(
    document: dict[str, JsonValue], tab: dict[str, JsonValue], *, start: int
) -> int:
    style = _Geometry.model_validate(
        {
            **_style(document, "documentStyle"),
            **_style(tab, "documentStyle"),
            **_table_section(tab, start=start),
        }
    )
    if style.document_format.mode == "PAGELESS" or len(style.columns) > 1:
        msg = "Preserve tables whose fixed printable width cannot be resolved"
        raise ValueError(msg)
    page_width = style.page_size.height if style.flipped else style.page_size.width
    available = page_width.magnitude - style.margin_left.magnitude - style.margin_right.magnitude
    if style.columns and style.columns[0].width is not None:
        available = min(available, style.columns[0].width.magnitude)
    if not _MIN_PRINTABLE_WIDTH <= available <= _MAX_PRINTABLE_WIDTH:
        msg = "Unknown printable width"
        raise ValueError(msg)
    return round(available * 20)


def _style(source: dict[str, JsonValue], field: str) -> dict[str, JsonValue]:
    return obj(source[field]) if field in source else {}


def _table_section(tab: dict[str, JsonValue], *, start: int) -> dict[str, JsonValue]:
    section: dict[str, JsonValue] = {}
    for value in content(tab):
        element = obj(value)
        if _Position.model_validate(element).start > start:
            break
        if "sectionBreak" in element:
            boundary = obj(element["sectionBreak"])
            if any(
                boundary[key]
                for key in ("suggestedInsertionIds", "suggestedDeletionIds")
                if key in boundary
            ):
                msg = "Preserve tables in suggested sections"
                raise ValueError(msg)
            section = _style(boundary, "sectionStyle")
    return section
