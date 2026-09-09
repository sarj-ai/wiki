from copy import deepcopy
import hashlib
import json
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app.document_transform.ir import MAX_DOC_UTF16_UNITS, utf16_length
from app.document_transform.native_font_context import (
    resolve_native_font_family,
    resolve_native_font_values,
)


_MAX_PARAGRAPH_BYTES = 131072
_MAX_INTERVALS = 1000
_MAX_NATIVE_INDEX = 2**31 - 1


class NativeFontInterval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    end: int = Field(gt=0, le=MAX_DOC_UTF16_UNITS, strict=True)
    bold: StrictBool
    weight: int = Field(ge=100, le=900, multiple_of=100, strict=True)


class NativeFontSemantics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    target_font_family: str = Field(min_length=1, max_length=200)
    preserve_family: StrictBool
    start: int = Field(ge=0, le=_MAX_NATIVE_INDEX, strict=True)
    end: int = Field(gt=0, le=_MAX_NATIVE_INDEX, strict=True)
    expected_start: int = Field(ge=0, le=_MAX_NATIVE_INDEX, strict=True)
    expected_end: int = Field(gt=0, le=_MAX_NATIVE_INDEX, strict=True)
    source_family_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    intervals: tuple[NativeFontInterval, ...] = Field(max_length=_MAX_INTERVALS)


def capture_font_semantics(
    document: dict[str, Any],
    tab_id: str,
    element: dict[str, Any],
    *,
    target_font_family: str,
    preserve_family: bool,
    position_delta: int = 0,
) -> NativeFontSemantics:
    if not preserve_family:
        _bounded(element)
    start, end = element["startIndex"], element["endIndex"]
    paragraph = element["paragraph"]
    intervals: list[NativeFontInterval] = []
    if not preserve_family:
        for part in paragraph["elements"]:
            values = resolve_native_font_values(
                document,
                tab_id,
                paragraph.get("paragraphStyle", {}),
                part["textRun"].get("textStyle", {}),
            )
            if values.bold is None or values.weight is None:
                raise ValueError
            interval = NativeFontInterval(
                end=part["endIndex"] - start, bold=values.bold, weight=values.weight
            )
            if intervals and (interval.bold, interval.weight) == (
                intervals[-1].bold,
                intervals[-1].weight,
            ):
                intervals[-1] = interval
            else:
                intervals.append(interval)
    return NativeFontSemantics(
        target_font_family=target_font_family,
        preserve_family=preserve_family,
        start=start,
        end=end,
        expected_start=start + position_delta,
        expected_end=end + position_delta,
        source_family_sha256=_family_digest(element),
        intervals=tuple(intervals),
    )


def semantic_font_digest(
    document: dict[str, Any],
    tab_id: str,
    element: dict[str, Any],
    evidence: NativeFontSemantics,
    *,
    require_target_family: bool,
) -> str:
    if not evidence.preserve_family:
        _bounded(element)
    element = _source_position(element, evidence, require_target=require_target_family)
    if evidence.preserve_family:
        if evidence.intervals:
            raise ValueError
        return _digest({"paragraph": element, "policy": evidence.model_dump(mode="json")})
    if not evidence.intervals or evidence.intervals[-1].end != evidence.end - evidence.start:
        raise ValueError
    cursor = 0
    for interval in evidence.intervals:
        if interval.end <= cursor:
            raise ValueError
        cursor = interval.end
    paragraph = element["paragraph"]
    target = all(
        resolve_native_font_family(
            document,
            tab_id,
            paragraph.get("paragraphStyle", {}),
            part["textRun"].get("textStyle", {}),
        )
        == evidence.target_font_family
        for part in paragraph["elements"]
        # Docs may canonicalize the family of its invisible, provider-owned
        # paragraph terminator independently of every visible text run.
        if part["textRun"].get("content") != "\n"
    )
    if not target and (
        require_target_family or _family_digest(element) != evidence.source_family_sha256
    ):
        raise ValueError
    payload = deepcopy(element)
    canonical: list[dict[str, Any]] = []
    interval_index = 0
    for part in payload["paragraph"].pop("elements"):
        start, end = part.pop("startIndex") - evidence.start, part.pop("endIndex") - evidence.start
        run = part["textRun"]
        content = run.pop("content")
        values = resolve_native_font_values(
            document, tab_id, paragraph.get("paragraphStyle", {}), run.get("textStyle", {})
        )
        while start < end:
            interval = evidence.intervals[interval_index]
            stop = min(end, interval.end)
            if stop <= start or (values.bold, values.weight) != (interval.bold, interval.weight):
                raise ValueError
            prefix, content = _split_utf16(content, stop - start)
            _append(
                canonical,
                prefix,
                {"residual": _residual(part), "bold": values.bold, "weight": values.weight},
            )
            start = stop
            if stop == interval.end:
                interval_index += 1
        if content:
            raise ValueError
    payload["paragraph"]["elements"] = canonical
    return _digest({"paragraph": payload, "policy": evidence.model_dump(mode="json")})


def _residual(part: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(part)
    style = result["textRun"].get("textStyle", {})
    family = style.get("weightedFontFamily", {})
    family.pop("fontFamily", None)
    family.pop("weight", None)
    if not family:
        style.pop("weightedFontFamily", None)
    style.pop("bold", None)
    if not style:
        result["textRun"].pop("textStyle", None)
    return result


def _source_position(
    element: dict[str, Any], evidence: NativeFontSemantics, *, require_target: bool
) -> dict[str, Any]:
    source = (evidence.start, evidence.end)
    expected = (evidence.expected_start, evidence.expected_end)
    actual = (element["startIndex"], element["endIndex"])
    if (
        evidence.start >= evidence.end
        or evidence.expected_end - evidence.expected_start != evidence.end - evidence.start
    ):
        raise ValueError
    if actual != expected and (require_target or actual != source):
        raise ValueError
    result = deepcopy(element)
    delta = element["startIndex"] - evidence.start
    result["startIndex"] -= delta
    result["endIndex"] -= delta
    for part in result["paragraph"]["elements"]:
        part["startIndex"] -= delta
        part["endIndex"] -= delta
    return result


def _family_digest(element: dict[str, Any]) -> str:
    result: list[dict[str, Any]] = []
    for part in element["paragraph"]["elements"]:
        run = part["textRun"]
        family = run.get("textStyle", {}).get("weightedFontFamily", {})
        _append(
            result,
            run["content"],
            {"present": "fontFamily" in family, "value": family.get("fontFamily")},
        )
    return _digest(result)


def _append(result: list[dict[str, Any]], content: str, properties: dict[str, Any]) -> None:
    if result and result[-1]["properties"] == properties:
        result[-1]["content"] += content
    else:
        result.append({"content": content, "properties": properties})


class _TextSplit(NamedTuple):
    prefix: str
    remainder: str


def _split_utf16(content: str, width: int) -> _TextSplit:
    consumed = 0
    for index, character in enumerate(content):
        consumed += utf16_length(character)
        if consumed == width:
            return _TextSplit(content[: index + 1], content[index + 1 :])
        if consumed > width:
            break
    raise ValueError


def _bounded(element: dict[str, Any]) -> None:
    if len(json.dumps(element, ensure_ascii=False).encode()) > _MAX_PARAGRAPH_BYTES:
        raise ValueError


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
