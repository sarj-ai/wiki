from dataclasses import dataclass
import json
import re

from pydantic import BaseModel, ConfigDict, JsonValue, StrictStr

from app.document_transform.ir import BlockKind, NativeContent, TextBlock, utf16_length
from app.document_transform.readability_json import array, integer, obj


_LABEL = re.compile(r"[^\n.!?]{1,40}:\s*\Z")
_MAX_FIELDS = 3
_MAX_FIELD_CHARS = 120
_MAX_BODY_CHARS = 1_000


class _TextRun(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)
    content: StrictStr


@dataclass(frozen=True, slots=True, kw_only=True)
class _FieldPart:
    text: str
    end: int


def native_paragraph_matches(element: dict[str, JsonValue], block: TextBlock) -> bool:
    if (
        block.kind is not BlockKind.PARAGRAPH
        or not block.native_content
        or len(block.text) > _MAX_BODY_CHARS
        or block.sha256 != block.source_style_text_sha256
    ):
        return False
    try:
        return _field_runs_match(element, block)
    except KeyError, TypeError, ValueError:
        return False


def _field_runs_match(element: dict[str, JsonValue], block: TextBlock) -> bool:
    cursor = integer(element["startIndex"])
    atom_index = 0
    visible = []
    for item in array(obj(element["paragraph"])["elements"]):
        atom = block.native_content[atom_index] if atom_index < len(block.native_content) else None
        part = _field_part(item, cursor, atom)
        if part is None:
            return False
        cursor = part.end
        visible.append(part.text)
        atom_index += int("textRun" not in obj(item))
    return (
        atom_index == len(block.native_content)
        and cursor == element["endIndex"]
        and "".join(visible) == block.text + "\n"
    )


def _field_part(item: JsonValue, cursor: int, atom: NativeContent | None) -> _FieldPart | None:
    if (
        not isinstance(item, dict)
        or type(item.get("startIndex")) is not int
        or item["startIndex"] != cursor
    ):
        return None
    end = item.get("endIndex")
    if type(end) is not int or end <= cursor:
        return None
    kind = next(
        (key for key in ("textRun", "dateElement", "person", "richLink") if key in item), None
    )
    if kind is None or set(item) != {"startIndex", "endIndex", kind}:
        return None
    if kind == "textRun":
        value = _TextRun.model_validate(item[kind]).content
        return _FieldPart(text=value, end=end) if utf16_length(value) == end - cursor else None
    native_text = _native_display(item[kind], kind=kind, atom=atom, width=end - cursor)
    return _FieldPart(text=native_text, end=end) if native_text is not None else None


def _native_display(
    payload: JsonValue, *, kind: str, atom: NativeContent | None, width: int
) -> str | None:
    if atom is None or atom.kind != kind or width != 1 or not isinstance(payload, dict):
        return None
    protected = {
        "element": {key: value for key, value in payload.items() if key != "textStyle"},
        "outer": {},
    }
    return atom.display_text if json.loads(atom.payload_json) == protected else None


def native_field_groups(mapped: list[TextBlock | None]) -> tuple[tuple[TextBlock, ...], ...]:
    groups = []
    for index, heading in enumerate(mapped):
        if (
            heading is None
            or heading.kind is not BlockKind.HEADING
            or len(heading.text) > _MAX_FIELD_CHARS
        ):
            continue
        group = _field_group(heading, mapped[index + 1 : index + _MAX_FIELDS + 2])
        if group is not None:
            groups.append(group)
    return tuple(groups)


def _field_group(
    heading: TextBlock, following: list[TextBlock | None]
) -> tuple[TextBlock, ...] | None:
    group = [heading]
    for candidate in following:
        if candidate is None or not _contiguous(group[-1], candidate):
            return None
        if not _is_field(candidate):
            if (
                len(group) > 1
                and candidate.kind is BlockKind.PARAGRAPH
                and len(candidate.text) <= _MAX_BODY_CHARS
            ):
                return tuple(group)
            return None
        if len(group) > _MAX_FIELDS:
            return None
        group.append(candidate)
    return None


def _is_field(block: TextBlock) -> bool:
    return bool(
        block.native_content
        and len(block.text) <= _MAX_FIELD_CHARS
        and _LABEL.fullmatch(block.text[: block.native_content[0].text_offset])
    )


def _contiguous(first: TextBlock, second: TextBlock) -> bool:
    return bool(
        first.source_range is not None
        and second.source_range is not None
        and first.source_range.end_index + 1 == second.source_range.start_index
    )
