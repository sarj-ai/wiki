from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from typing import Any, NamedTuple, NewType

from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.errors import SourceTooLargeError, UnsupportedSourceError
from app.document_transform.ir import BlockKind, DocumentIR, TextBlock, utf16_length


MAX_PRESENTATION_SLIDES = 200
MAX_PRESENTATION_TEXT_UTF16_UNITS = 500_000
# Dense but ordinary decks can represent tables and timelines as one text shape per cell.
# This remains subordinate to the independent text, element, slide, and group-depth caps.
MAX_PRESENTATION_BLOCKS = 2_000
MAX_PRESENTATION_ELEMENTS = 10_000
MAX_GROUP_DEPTH = 20
_MAX_OBJECT_ID_LENGTH = 128
_PLAIN_TEXT_ELEMENT_COUNT = 2
_SARJ_LOGO_OBJECT_ID_PREFIX = "sarjLogo"
_VOLATILE_RESOURCE_KEYS = frozenset({"contentUrl", "thumbnailUrl"})

SlideId = NewType("SlideId", str)
SlideObjectId = NewType("SlideObjectId", str)


@dataclass(frozen=True, slots=True)
class SlideTextLocator:
    slide_id: SlideId
    object_id: SlideObjectId
    start_index: int
    end_index: int
    placeholder_type: str | None
    group_path: tuple[SlideObjectId, ...] = ()


@dataclass(frozen=True, slots=True)
class ProtectedSlideInventory:
    slide_id: SlideId
    object_ids: tuple[SlideObjectId, ...]
    object_kinds: tuple[str, ...]
    normalized_json: str


@dataclass(frozen=True, slots=True)
class ProtectedPresentationInventory:
    presentation_json: str
    slides: tuple[ProtectedSlideInventory, ...]

    @property
    def sha256(self) -> str:
        payload = {
            "presentation": json.loads(self.presentation_json),
            "slides": [
                {
                    "slide_id": slide.slide_id,
                    "object_ids": slide.object_ids,
                    "object_kinds": slide.object_kinds,
                    "structure": json.loads(slide.normalized_json),
                }
                for slide in self.slides
            ],
        }
        return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ParsedPresentation:
    document: DocumentIR
    locators: Mapping[str, SlideTextLocator]
    protected_inventory: ProtectedPresentationInventory
    protected_sha256: str


@dataclass(frozen=True, slots=True)
class _LocatedElement:
    element: dict[str, Any]
    object_id: SlideObjectId
    group_path: tuple[SlideObjectId, ...]


@dataclass(frozen=True, slots=True)
class _EditableShape:
    text: str
    start_index: int
    end_index: int
    placeholder_type: str | None


@dataclass(frozen=True, slots=True)
class PlainSlideText:
    text: str
    start_index: int
    end_index: int
    style: object


class _TextRunBounds(NamedTuple):
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class _ParsedSlide:
    blocks: tuple[TextBlock, ...]
    locators: Mapping[str, SlideTextLocator]
    inventory: ProtectedSlideInventory
    element_count: int


def parse_google_presentation(
    presentation: dict[str, Any],
    *,
    title: str,
) -> ParsedPresentation:
    _required_identifier(presentation, "presentationId")
    _required_identifier(presentation, "revisionId")
    slides = presentation.get("slides")
    if not isinstance(slides, list) or not slides:
        raise UnsupportedSourceError
    if len(slides) > MAX_PRESENTATION_SLIDES:
        raise SourceTooLargeError
    if _content_utf16_units(presentation) > MAX_PRESENTATION_TEXT_UTF16_UNITS:
        raise SourceTooLargeError

    blocks: list[TextBlock] = []
    locators: dict[str, SlideTextLocator] = {}
    protected_slides: list[ProtectedSlideInventory] = []
    seen_slide_ids: set[SlideId] = set()
    seen_object_ids: set[SlideObjectId] = set()
    element_count = 0

    for slide in slides:
        if not isinstance(slide, dict):
            raise UnsupportedSourceError
        slide_id = SlideId(_required_identifier(slide, "objectId"))
        if slide_id in seen_slide_ids:
            raise UnsupportedSourceError
        seen_slide_ids.add(slide_id)
        parsed_slide = _parse_slide(
            slide,
            slide_id=slide_id,
            seen_object_ids=seen_object_ids,
            existing_block_count=len(blocks),
        )
        element_count += parsed_slide.element_count
        if element_count > MAX_PRESENTATION_ELEMENTS:
            raise SourceTooLargeError
        blocks.extend(parsed_slide.blocks)
        locators.update(parsed_slide.locators)
        protected_slides.append(parsed_slide.inventory)

    presentation_metadata = {
        str(key): value
        for key, value in presentation.items()
        if key not in {"presentationId", "revisionId", "slides", "title"}
    }
    _reject_reserved_sarj_logo_objects(presentation_metadata)
    protected_inventory = ProtectedPresentationInventory(
        presentation_json=_canonical_json(presentation_metadata),
        slides=tuple(protected_slides),
    )
    return ParsedPresentation(
        document=DocumentIR(
            title=title,
            blocks=tuple(blocks),
            source_format="google_slides",
        ),
        locators=MappingProxyType(locators),
        protected_inventory=protected_inventory,
        protected_sha256=protected_inventory.sha256,
    )


def _parse_slide(
    slide: dict[str, Any],
    *,
    slide_id: SlideId,
    seen_object_ids: set[SlideObjectId],
    existing_block_count: int,
) -> _ParsedSlide:
    raw_elements = slide.get("pageElements", [])
    if not isinstance(raw_elements, list):
        raise UnsupportedSourceError
    located_elements = tuple(
        _walk_page_elements(
            raw_elements,
            slide_id=slide_id,
            seen_object_ids=seen_object_ids,
        )
    )
    _reject_reserved_sarj_logo_objects(
        {str(key): value for key, value in slide.items() if key != "pageElements"}
    )
    blocks: list[TextBlock] = []
    locators: dict[str, SlideTextLocator] = {}
    editable_by_object: dict[str, tuple[str, _EditableShape]] = {}
    object_ids: list[SlideObjectId] = []
    object_kinds: list[str] = []
    for located in located_elements:
        object_id = located.object_id
        object_ids.append(object_id)
        object_kinds.append(_element_kind(located.element))
        editable = _editable_shape(located.element)
        if editable is None:
            continue
        if existing_block_count + len(blocks) >= MAX_PRESENTATION_BLOCKS:
            raise SourceTooLargeError
        block_id = _block_id(slide_id=slide_id, object_id=object_id)
        if block_id in locators:
            raise UnsupportedSourceError
        blocks.append(
            TextBlock(
                id=block_id,
                kind=_block_kind(editable.placeholder_type),
                text=editable.text,
                level=_heading_level(editable.placeholder_type),
            )
        )
        locators[block_id] = SlideTextLocator(
            slide_id=slide_id,
            object_id=object_id,
            start_index=editable.start_index,
            end_index=editable.end_index,
            placeholder_type=editable.placeholder_type,
            group_path=located.group_path,
        )
        editable_by_object[object_id] = (block_id, editable)
    inventory = ProtectedSlideInventory(
        slide_id=slide_id,
        object_ids=tuple(object_ids),
        object_kinds=tuple(object_kinds),
        normalized_json=_canonical_json(
            _normalize_slide(
                slide,
                slide_id=slide_id,
                editable_by_object=editable_by_object,
            )
        ),
    )
    return _ParsedSlide(
        blocks=tuple(blocks),
        locators=MappingProxyType(locators),
        inventory=inventory,
        element_count=len(located_elements),
    )


def _walk_page_elements(
    elements: list[Any],
    *,
    slide_id: SlideId,
    seen_object_ids: set[SlideObjectId],
    group_path: tuple[SlideObjectId, ...] = (),
    depth: int = 0,
) -> Iterator[_LocatedElement]:
    if depth > MAX_GROUP_DEPTH:
        raise SourceTooLargeError
    for raw_element in elements:
        if not isinstance(raw_element, dict):
            raise UnsupportedSourceError
        element = {str(key): value for key, value in raw_element.items()}
        object_id = SlideObjectId(_required_identifier(element, "objectId"))
        if object_id in seen_object_ids:
            raise UnsupportedSourceError
        seen_object_ids.add(object_id)
        if _is_expected_sarj_logo(element, slide_id=slide_id, group_path=group_path):
            continue
        yield _LocatedElement(
            element=element,
            object_id=object_id,
            group_path=group_path,
        )
        group = element.get("elementGroup")
        if group is None:
            continue
        if not isinstance(group, dict):
            raise UnsupportedSourceError
        children = group.get("children", [])
        if not isinstance(children, list):
            raise UnsupportedSourceError
        yield from _walk_page_elements(
            children,
            slide_id=slide_id,
            seen_object_ids=seen_object_ids,
            group_path=(*group_path, object_id),
            depth=depth + 1,
        )


def _editable_shape(element: dict[str, Any]) -> _EditableShape | None:
    shape = element.get("shape")
    if not isinstance(shape, dict):
        return None
    text = shape.get("text")
    if not isinstance(text, dict):
        return None
    plain_text = parse_plain_slide_text(text.get("textElements"))
    if plain_text is None:
        return None
    style = plain_text.style
    if not isinstance(style, dict) or style.get("link") is not None:
        return None
    if not plain_text.text.strip():
        return None
    placeholder = shape.get("placeholder")
    placeholder_type = placeholder.get("type") if isinstance(placeholder, dict) else None
    if placeholder_type is not None and not isinstance(placeholder_type, str):
        return None
    return _EditableShape(
        text=plain_text.text,
        start_index=plain_text.start_index,
        end_index=plain_text.end_index,
        placeholder_type=placeholder_type,
    )


def parse_plain_slide_text(value: object) -> PlainSlideText | None:
    if not isinstance(value, list) or len(value) not in {_PLAIN_TEXT_ELEMENT_COUNT, 3}:
        return None
    paragraph_element, visible_element, *terminal_elements = value
    if not isinstance(paragraph_element, dict) or not isinstance(visible_element, dict):
        return None
    visible_element = {str(key): child for key, child in visible_element.items()}
    paragraph = paragraph_element.get("paragraphMarker")
    visible_run = visible_element.get("textRun")
    if not isinstance(paragraph, dict) or paragraph.get("bullet") is not None:
        return None
    if not isinstance(visible_run, dict):
        return None
    content = visible_run.get("content")
    if not isinstance(content, str):
        return None
    visible_bounds = _text_run_bounds(visible_element, content=content)
    if visible_bounds is None:
        return None
    start, end = visible_bounds
    if terminal_elements:
        terminal_element = terminal_elements[0]
        if not isinstance(terminal_element, dict):
            return None
        terminal_element = {str(key): child for key, child in terminal_element.items()}
        terminal_run = terminal_element.get("textRun")
        terminal_bounds = _text_run_bounds(terminal_element, content="\n")
        if (
            not isinstance(terminal_run, dict)
            or terminal_run.get("content") != "\n"
            or terminal_bounds != (end, end + 1)
            or "\n" in content
            or "\r" in content
        ):
            return None
        visible_text = content
        visible_end = end
    else:
        if not content.endswith("\n") or "\n" in content[:-1] or "\r" in content:
            return None
        visible_text = content[:-1]
        visible_end = end - 1
    if not visible_text or visible_end <= start:
        return None
    return PlainSlideText(
        text=visible_text,
        start_index=start,
        end_index=visible_end,
        style=visible_run.get("style"),
    )


def _text_run_bounds(element: dict[str, Any], *, content: str) -> _TextRunBounds | None:
    start = element.get("startIndex", 0)
    end = element.get("endIndex")
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    if start < 0 or end <= start or end - start != utf16_length(content):
        return None
    return _TextRunBounds(start, end)


def _normalize_slide(
    slide: dict[str, Any],
    *,
    slide_id: SlideId,
    editable_by_object: dict[str, tuple[str, _EditableShape]],
) -> object:
    raw_elements = slide.get("pageElements", [])
    if not isinstance(raw_elements, list):
        raise UnsupportedSourceError
    normalized_slide = {str(key): value for key, value in slide.items() if key != "pageElements"}
    normalized_slide["pageElements"] = [
        value
        for value in raw_elements
        if not (
            isinstance(value, dict)
            and _is_expected_sarj_logo(value, slide_id=slide_id, group_path=())
        )
    ]
    return _normalize_value(
        normalized_slide,
        editable_by_object=editable_by_object,
    )


def _normalize_value(
    value: object,
    *,
    editable_by_object: dict[str, tuple[str, _EditableShape]],
) -> object:
    if isinstance(value, list):
        return [
            _normalize_value(
                child,
                editable_by_object=editable_by_object,
            )
            for child in value
        ]
    if not isinstance(value, dict):
        return value
    normalized: dict[str, object] = {
        str(key): _normalize_value(
            child,
            editable_by_object=editable_by_object,
        )
        for key, child in value.items()
        if key not in _VOLATILE_RESOURCE_KEYS
    }
    object_id = normalized.get("objectId")
    editable = editable_by_object.get(object_id) if isinstance(object_id, str) else None
    shape = normalized.get("shape")
    if editable is not None and isinstance(shape, dict):
        normalized_shape: dict[str, object] = {str(key): child for key, child in shape.items()}
        normalized["shape"] = normalized_shape
        block_id, parsed = editable
        raw_shape = value.get("shape")
        raw_text = raw_shape.get("text") if isinstance(raw_shape, dict) else None
        raw_elements = raw_text.get("textElements") if isinstance(raw_text, dict) else None
        if isinstance(raw_elements, list) and raw_elements:
            paragraph = raw_elements[0]
            normalized_shape["text"] = {
                "editableBlockId": block_id,
                "paragraphMarker": _strip_text_indexes(paragraph),
                "terminalNewline": True,
                "placeholderType": parsed.placeholder_type,
            }
    return normalized


def _strip_text_indexes(value: object) -> object:
    if not isinstance(value, dict):
        return value
    return {
        str(key): _strip_text_indexes(child)
        for key, child in value.items()
        if key not in {"startIndex", "endIndex"}
    }


def _is_expected_sarj_logo(
    value: dict[str, Any],
    *,
    slide_id: SlideId,
    group_path: tuple[SlideObjectId, ...],
) -> bool:
    object_id = value.get("objectId")
    if not isinstance(object_id, str) or not object_id.startswith(_SARJ_LOGO_OBJECT_ID_PREFIX):
        return False
    image = value.get("image")
    if (
        object_id != sarj_logo_object_id(slide_id)
        or group_path
        or not isinstance(image, dict)
        or image.get("sourceUrl") != CURRENT_BRAND_MANIFEST.assets.logo_uri
    ):
        raise UnsupportedSourceError
    return True


def _reject_reserved_sarj_logo_objects(value: object) -> None:
    if isinstance(value, list):
        for child in value:
            _reject_reserved_sarj_logo_objects(child)
        return
    if not isinstance(value, dict):
        return
    object_id = value.get("objectId")
    if isinstance(object_id, str) and object_id.startswith(_SARJ_LOGO_OBJECT_ID_PREFIX):
        raise UnsupportedSourceError
    for child in value.values():
        _reject_reserved_sarj_logo_objects(child)


def sarj_logo_object_id(slide_id: SlideId) -> SlideObjectId:
    digest = hashlib.sha256(slide_id.encode()).hexdigest()[:32]
    return SlideObjectId(f"{_SARJ_LOGO_OBJECT_ID_PREFIX}{digest}")


def _content_utf16_units(value: object) -> int:
    if isinstance(value, list):
        return sum(_content_utf16_units(child) for child in value)
    if not isinstance(value, dict):
        return 0
    return sum(
        utf16_length(child)
        if key == "content" and isinstance(child, str)
        else _content_utf16_units(child)
        for key, child in value.items()
    )


def _element_kind(element: dict[str, Any]) -> str:
    for kind in (
        "shape",
        "table",
        "image",
        "video",
        "sheetsChart",
        "elementGroup",
        "line",
        "wordArt",
    ):
        if kind in element:
            return kind
    return "unknown"


def _block_kind(placeholder_type: str | None) -> BlockKind:
    return (
        BlockKind.HEADING if _heading_level(placeholder_type) is not None else BlockKind.PARAGRAPH
    )


def _heading_level(placeholder_type: str | None) -> int | None:
    if placeholder_type in {"TITLE", "CENTERED_TITLE"}:
        return 1
    if placeholder_type == "SUBTITLE":
        return 2
    return None


def _block_id(*, slide_id: SlideId, object_id: SlideObjectId) -> str:
    digest = hashlib.sha256(f"{slide_id}\0{object_id}".encode()).hexdigest()
    return f"slides-{digest[:32]}"


def _required_identifier(value: dict[str, Any], key: str) -> str:
    candidate = value.get(key)
    if not isinstance(candidate, str) or not candidate or len(candidate) > _MAX_OBJECT_ID_LENGTH:
        raise UnsupportedSourceError
    return candidate


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise UnsupportedSourceError from exc
