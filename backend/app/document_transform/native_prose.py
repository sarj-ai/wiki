from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictInt, ValidationError


NATIVE_TEXT_STYLE_FIELDS = frozenset(
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
_MONOSPACE_FONTS = frozenset({"consolas", "courier new", "menlo", "monospace", "roboto mono"})
_MIN_LINKED_PROSE_RUNS = 2


class _NativeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _FontFamily(_NativeRecord):
    font_family: str = Field(alias="fontFamily")
    weight: StrictInt | None = None


class _TextStyle(_NativeRecord):
    background_color: JsonValue = Field(default=None, alias="backgroundColor")
    baseline_offset: JsonValue = Field(default=None, alias="baselineOffset")
    bold: JsonValue = None
    font_size: JsonValue = Field(default=None, alias="fontSize")
    foreground_color: JsonValue = Field(default=None, alias="foregroundColor")
    italic: JsonValue = None
    link: dict[str, JsonValue] | None = None
    small_caps: JsonValue = Field(default=None, alias="smallCaps")
    strikethrough: JsonValue = None
    underline: JsonValue = None
    weighted_font_family: _FontFamily | None = Field(default=None, alias="weightedFontFamily")

    @property
    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json", by_alias=True, exclude_unset=True),
            sort_keys=True,
            separators=(",", ":"),
        )


class _TextRun(_NativeRecord):
    content: str = Field(min_length=1)
    text_style: _TextStyle = Field(default_factory=_TextStyle, alias="textStyle")


class _Element(_NativeRecord):
    start_index: StrictInt = Field(alias="startIndex")
    end_index: StrictInt = Field(alias="endIndex")
    text_run: _TextRun = Field(alias="textRun")


class _Paragraph(BaseModel):
    model_config = ConfigDict(strict=True)
    elements: list[_Element] = Field(min_length=_MIN_LINKED_PROSE_RUNS)
    suggested_paragraph_style_changes: JsonValue = Field(
        default=None, alias="suggestedParagraphStyleChanges"
    )
    suggested_bullet_changes: JsonValue = Field(default=None, alias="suggestedBulletChanges")


@dataclass(frozen=True, slots=True)
class NativeProseTail:
    text: str
    links_sha256: str
    prefix_style_json: str


@dataclass(slots=True)
class _CitationRun:
    text: str
    link: dict[str, JsonValue] | None


def brandable_prose(paragraph: dict[str, JsonValue], *, start: int, end: int) -> bool:
    if type(start) is not int or type(end) is not int or start < 0 or end <= start:
        return False
    try:
        parsed = _Paragraph.model_validate(paragraph)
    except ValidationError:
        return False
    if not _valid_runs(parsed.elements, start=start, end=end):
        return False
    if any(element.text_run.text_style.link for element in parsed.elements):
        return False
    styles = [
        element.text_run.text_style.model_dump(
            exclude={"bold", "italic", "weighted_font_family"}, exclude_unset=True
        )
        for element in parsed.elements
        if element.text_run.content != "\n"
    ]
    return bool(styles) and all(style == styles[0] for style in styles)


def linked_prose_tail(paragraph: object, *, start: int, end: int) -> NativeProseTail | None:
    if type(start) is not int or type(end) is not int or start < 0 or end <= start:
        return None
    try:
        parsed = _Paragraph.model_validate(paragraph)
    except ValidationError:
        return None
    if parsed.suggested_paragraph_style_changes or parsed.suggested_bullet_changes:
        return None
    if not _valid_runs(parsed.elements, start=start, end=end):
        return None
    runs = [element.text_run for element in parsed.elements]
    first_link = next((index for index, run in enumerate(runs) if run.text_style.link), None)
    if first_link is None or first_link == 0:
        return None
    prefix = runs[:first_link]
    prefix_style = prefix[0].text_style.canonical_json
    if any(run.text_style.canonical_json != prefix_style for run in prefix):
        return None
    if not "".join(run.content for run in prefix).strip():
        return None
    suffix_runs = _citation_runs(runs[first_link:])
    suffix = "".join(run.text for run in suffix_runs)
    if not suffix.strip():
        return None
    payload = json.dumps(
        [asdict(run) for run in suffix_runs],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return NativeProseTail(
        text=suffix,
        links_sha256=hashlib.sha256(payload.encode()).hexdigest(),
        prefix_style_json=prefix_style,
    )


def _valid_runs(elements: list[_Element], *, start: int, end: int) -> bool:
    cursor = start
    for element in elements:
        run = element.text_run
        family = run.text_style.weighted_font_family
        if family is not None and family.font_family.casefold() in _MONOSPACE_FONTS:
            return False
        if run.text_style.link is not None and not run.text_style.link:
            return False
        run_end = cursor + len(run.content.encode("utf-16-le")) // 2
        if element.start_index != cursor or element.end_index != run_end:
            return False
        cursor = run_end
    visible = "".join(element.text_run.content for element in elements)
    return (
        cursor == end
        and visible.endswith("\n")
        and not any(c in visible[:-1] for c in "\n\r\v\f\u2028\u2029")
    )


def _citation_runs(runs: list[_TextRun]) -> list[_CitationRun]:
    suffix: list[_CitationRun] = []
    for index, run in enumerate(runs):
        text = run.content.removesuffix("\n") if index == len(runs) - 1 else run.content
        if not text:
            continue
        link = run.text_style.link
        if suffix and suffix[-1].link == link:
            suffix[-1].text += text
        else:
            suffix.append(_CitationRun(text=text, link=link))
    return suffix
