from __future__ import annotations

from copy import deepcopy
from importlib.metadata import version
from itertools import pairwise
import math
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.native_code_source import (
    NativeCodeRecord,
    code_array,
    code_bodies,
    code_color,
    code_index,
    code_json,
    code_object,
    code_parents,
    code_string,
    code_units,
)
from app.document_transform.native_font_proof import native_paragraph_font_sha256
from app.identifiers import DocsTabId


if TYPE_CHECKING:
    from app.document_transform.native_code import (
        NativeCodeColor,
        NativeCodeParagraph,
        NativeCodeStylePlan,
    )


_RGB_CHANNELS = ("red", "green", "blue")
_COLOR_TOLERANCE = 0.000001


class _CodeTextStyle(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True, strict=True)

    foreground: JsonValue = Field(default=None, alias="foregroundColor")


class _CodeTextRun(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True, strict=True)

    content: str
    style: _CodeTextStyle = Field(default_factory=_CodeTextStyle, alias="textStyle")


def project_native_code_styles(
    source: NativeCodeRecord, actual: NativeCodeRecord, plan: NativeCodeStylePlan
) -> NativeCodeRecord:
    if plan.syntax_version != f"native-code-v2/pygments-{version('Pygments')}":
        msg = "Unsupported native code syntax version"
        raise ValueError(msg)
    if not plan.paragraphs:
        return deepcopy(actual)
    if code_parents(source) != plan.parents_sha256 or code_parents(actual) != plan.parents_sha256:
        msg = "Native code style parents changed"
        raise ValueError(msg)
    projected = deepcopy(actual)
    before, after = code_bodies(source), code_bodies(projected)
    identities: set[tuple[DocsTabId, int]] = set()
    for item in plan.paragraphs:
        identity = item.tab_id, item.body_ordinal
        if identity in identities:
            msg = "Duplicate native code paragraph"
            raise ValueError(msg)
        identities.add(identity)
        original = code_object(before[item.tab_id][item.body_ordinal])
        observed = code_object(after[item.tab_id][item.body_ordinal])
        _verify_paragraph(original, observed, item)
        after[item.tab_id][item.body_ordinal] = _rebased(
            original, item.target_start - item.source_start
        )
    return projected


def _verify_paragraph(
    original: NativeCodeRecord, observed: NativeCodeRecord, item: NativeCodeParagraph
) -> None:
    if code_json(original) != item.source_json:
        msg = "Native code source attestation changed"
        raise ValueError(msg)
    if (observed.get("startIndex"), observed.get("endIndex")) != (
        item.target_start,
        item.target_end,
    ):
        msg = "Native code range changed"
        raise ValueError(msg)
    expected = styled_paragraph(original, item.colors, delta=item.target_start - item.source_start)
    if code_json(expected) != item.expected_json:
        msg = "Native code expected style attestation changed"
        raise ValueError(msg)
    observed = _canonical_terminator_foreground(original, observed, item)
    verified = styled_paragraph(observed, item.colors, verify=True)
    if native_paragraph_font_sha256(
        expected, preserve_font_family=True
    ) != native_paragraph_font_sha256(verified, preserve_font_family=True):
        msg = "Native code literal or residual properties changed"
        raise ValueError(msg)


def _canonical_terminator_foreground(
    original: NativeCodeRecord, observed: NativeCodeRecord, item: NativeCodeParagraph
) -> NativeCodeRecord:
    newline = item.target_end - 1
    adjacent = [color for color in item.colors if color.end == newline]
    if len(adjacent) != 1:
        return observed
    last = _last_part(observed)
    run = _CodeTextRun.model_validate(last["textRun"])
    text = run.content
    if (
        code_index(last["endIndex"]) != item.target_end
        or not text.endswith("\n")
        or not _same_color(run.style.foreground, code_color(adjacent[0].color))
    ):
        return observed
    if code_index(last["endIndex"]) - code_index(last["startIndex"]) != code_units(text):
        msg = "Invalid native code terminator range"
        raise ValueError(msg)
    source_run = _CodeTextRun.model_validate(_last_part(original)["textRun"])
    # Docs may extend an adjacent token's requested foreground to its newline.
    # Canonicalize only that exact planned color; the full residual fingerprint
    # still binds every literal, range, font, link and other style property.
    result = deepcopy(observed)
    projected_parts = code_array(code_object(result["paragraph"])["elements"])
    terminator = deepcopy(last)
    terminator["startIndex"] = newline
    terminator_run = code_object(terminator["textRun"])
    terminator_run["content"] = "\n"
    terminator_style = code_object(terminator_run.setdefault("textStyle", {}))
    if "foreground" in source_run.style.model_fields_set:
        terminator_style["foregroundColor"] = deepcopy(source_run.style.foreground)
    else:
        terminator_style.pop("foregroundColor", None)
    if not terminator_style and "style" not in source_run.model_fields_set:
        terminator_run.pop("textStyle", None)
    if text != "\n":
        prefix = deepcopy(last)
        prefix["endIndex"] = newline
        code_object(prefix["textRun"])["content"] = text.removesuffix("\n")
        projected_parts[-1] = prefix
        projected_parts.append(terminator)
    else:
        projected_parts[-1] = terminator
    return result


def _last_part(element: NativeCodeRecord) -> NativeCodeRecord:
    return code_object(code_array(code_object(element["paragraph"])["elements"])[-1])


def verify_native_code_styles(
    source: NativeCodeRecord, actual: NativeCodeRecord, plan: NativeCodeStylePlan
) -> bool:
    try:
        project_native_code_styles(source, actual, plan)
    except KeyError, IndexError, TypeError, ValueError, AttributeError:
        return False
    return True


def styled_paragraph(
    element: NativeCodeRecord,
    colors: tuple[NativeCodeColor, ...],
    *,
    delta: int = 0,
    verify: bool = False,
) -> NativeCodeRecord:
    result = _rebased(element, delta)
    paragraph = code_object(result["paragraph"])
    style = code_object(paragraph.setdefault("paragraphStyle", {}))
    if verify and style.get("keepLinesTogether") is not True:
        msg = "Native code paragraph lines were not kept together"
        raise ValueError(msg)
    style["keepLinesTogether"] = True
    shading = code_object(style.setdefault("shading", {}))
    background = code_color(CURRENT_BRAND_MANIFEST.colors.subtle)
    if verify and not _same_color(shading.get("backgroundColor"), background):
        msg = "Native code shading was not applied"
        raise ValueError(msg)
    shading["backgroundColor"] = background
    parts: list[JsonValue] = []
    cursor = code_index(result["startIndex"])
    for value in code_array(paragraph["elements"]):
        part = code_object(value)
        if code_index(part["startIndex"]) != cursor:
            msg = "Native code text run gap"
            raise ValueError(msg)
        parts.extend(_styled_parts(part, colors, verify=verify))
        cursor = code_index(part["endIndex"])
    if cursor != result["endIndex"]:
        msg = "Native code paragraph coverage changed"
        raise ValueError(msg)
    paragraph["elements"] = parts
    return result


def _styled_parts(
    part: NativeCodeRecord, colors: tuple[NativeCodeColor, ...], *, verify: bool
) -> list[NativeCodeRecord]:
    start, end = code_index(part["startIndex"]), code_index(part["endIndex"])
    run = code_object(part["textRun"])
    text = code_string(run["content"])
    offsets = _character_offsets(text)
    if end - start != code_units(text) or not text:
        msg = "Invalid native code text range"
        raise ValueError(msg)
    boundaries = {start, end}
    for color in colors:
        if color.start < end and color.end > start:
            boundaries.update((max(start, color.start), min(end, color.end)))
    return [
        _styled_segment(part, limits, offsets, colors, verify=verify)
        for limits in pairwise(sorted(boundaries))
    ]


def _character_offsets(text: str) -> dict[int, int]:
    offsets = {0: 0}
    cursor = 0
    for index, character in enumerate(text, start=1):
        cursor += code_units(character)
        offsets[cursor] = index
    return offsets


def _styled_segment(
    part: NativeCodeRecord,
    limits: tuple[int, int],
    offsets: dict[int, int],
    colors: tuple[NativeCodeColor, ...],
    *,
    verify: bool,
) -> NativeCodeRecord:
    left, right = limits
    start = code_index(part["startIndex"])
    segment = deepcopy(part)
    segment["startIndex"], segment["endIndex"] = left, right
    run = code_object(segment["textRun"])
    run["content"] = code_string(run["content"])[offsets[left - start] : offsets[right - start]]
    selected = [color for color in colors if color.start <= left and color.end >= right]
    if len(selected) > 1:
        msg = "Overlapping native code colors"
        raise ValueError(msg)
    if selected:
        style = code_object(run.setdefault("textStyle", {}))
        desired = code_color(selected[0].color)
        if verify and not _same_color(style.get("foregroundColor"), desired):
            msg = "Native code syntax color was not applied"
            raise ValueError(msg)
        style["foregroundColor"] = desired
    return segment


def _same_color(actual: JsonValue, expected: NativeCodeRecord) -> bool:
    observed, wanted = _rgb(actual), _rgb(expected)
    return (
        observed is not None
        and wanted is not None
        and all(abs(a - b) <= _COLOR_TOLERANCE for a, b in zip(observed, wanted, strict=True))
    )


def _rgb(value: JsonValue) -> tuple[float, ...] | None:
    if not isinstance(value, dict) or set(value) != {"color"}:
        return None
    color = value["color"]
    if not isinstance(color, dict) or set(color) != {"rgbColor"}:
        return None
    rgb = color["rgbColor"]
    if not isinstance(rgb, dict) or set(rgb) - set(_RGB_CHANNELS):
        return None
    result = []
    for channel in _RGB_CHANNELS:
        component = rgb.get(channel, 0)
        if (
            not isinstance(component, (int, float))
            or isinstance(component, bool)
            or not math.isfinite(component)
            or not 0 <= component <= 1
        ):
            return None
        result.append(float(component))
    return tuple(result)


def _rebased(element: NativeCodeRecord, delta: int) -> NativeCodeRecord:
    result = deepcopy(element)
    paragraph = code_object(result["paragraph"])
    records = [result, *(code_object(value) for value in code_array(paragraph["elements"]))]
    for record in records:
        for key in ("startIndex", "endIndex"):
            record[key] = code_index(record[key]) + delta
    return result
