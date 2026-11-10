from __future__ import annotations

import asyncio
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
import json
from typing import Annotated, Any, Literal, NamedTuple

from loguru import logger
from pydantic import Field, JsonValue

from app.document_transform.errors import OutputVerificationError
from app.document_transform.ir import utf16_length
from app.document_transform.tab_structure import (
    HEADER_FOOTER_LAYOUT_FIELDS,
    TabSection,
    TabStructurePlan,
    plan_tab_structure,
)
from app.identifiers import DocsRevisionId, DocsTabId, FileId


MAX_STRUCTURE_REQUESTS = 1_000
MAX_STRUCTURE_BYTES = 2 * 1024 * 1024


class StructureStage(StrEnum):
    PLANNED = "planned"
    CREATING = "creating"
    CREATED = "created"
    MOVING = "moving"
    VERIFIED = "verified"


class StructureStateError(OutputVerificationError):
    pass


@dataclass(frozen=True, slots=True)
class StructurePlanCheckpoint:
    plan: TabStructurePlan


@dataclass(frozen=True, slots=True)
class PlannedStructureCheckpoint(StructurePlanCheckpoint):
    stage: Literal[StructureStage.PLANNED] = StructureStage.PLANNED
    created_tab_ids: tuple[()] = ()
    revision_id: Literal[""] = ""


@dataclass(frozen=True, slots=True)
class CreatingStructureCheckpoint(StructurePlanCheckpoint):
    stage: Literal[StructureStage.CREATING] = StructureStage.CREATING
    created_tab_ids: tuple[()] = ()
    revision_id: Literal[""] = ""


@dataclass(frozen=True, slots=True)
class BoundStructureCheckpoint(StructurePlanCheckpoint):
    created_tab_ids: tuple[DocsTabId, ...]
    revision_id: DocsRevisionId


@dataclass(frozen=True, slots=True)
class CreatedStructureCheckpoint(BoundStructureCheckpoint):
    stage: Literal[StructureStage.CREATED] = StructureStage.CREATED


@dataclass(frozen=True, slots=True)
class MovingStructureCheckpoint(BoundStructureCheckpoint):
    stage: Literal[StructureStage.MOVING] = StructureStage.MOVING


@dataclass(frozen=True, slots=True)
class VerifiedStructureCheckpoint(BoundStructureCheckpoint):
    stage: Literal[StructureStage.VERIFIED] = StructureStage.VERIFIED


StructureCheckpoint = Annotated[
    PlannedStructureCheckpoint
    | CreatingStructureCheckpoint
    | CreatedStructureCheckpoint
    | MovingStructureCheckpoint
    | VerifiedStructureCheckpoint,
    Field(discriminator="stage"),
]


class StructureDispatch[T: StructureCheckpoint](NamedTuple):
    checkpoint: T
    body: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StructureProvider:
    read_sync: Callable[[FileId], dict[str, Any]]
    write_sync: Callable[[FileId, dict[str, Any]], None]
    comments_absent_sync: Callable[[FileId], bool]

    async def read(self, document_id: FileId) -> dict[str, Any]:
        return await asyncio.to_thread(self.read_sync, document_id)

    async def write(self, document_id: FileId, body: dict[str, Any]) -> None:
        await asyncio.to_thread(self.write_sync, document_id, body)

    async def plan(
        self, *, document_id: FileId, revision_id: DocsRevisionId, source_id: FileId
    ) -> StructureCheckpoint | None:
        document = await self.read(document_id)
        if document.get("revisionId") != revision_id:
            raise StructureStateError
        # This first call is eligibility-only and never authorizes a write.
        plan = plan_tab_structure(document, comments_verified_absent=True)
        checks = structure_eligibility_checks(document)
        checks["plan_available"] = plan is not None
        checks["executable_plan"] = plan is not None and executable_structure(plan, copy_mode=True)
        if not checks["executable_plan"]:
            logger.info("document tab structure eligibility checks={checks}", checks=checks)
            return None
        comments_absent = await asyncio.to_thread(self.comments_absent_sync, source_id)
        checks["comments_verified_absent"] = comments_absent
        logger.info("document tab structure eligibility checks={checks}", checks=checks)
        if not comments_absent or plan is None:
            return None
        return PlannedStructureCheckpoint(plan)


def structure_eligibility_checks(document: dict[str, Any]) -> dict[str, int | bool]:
    checks: dict[str, int | bool] = {
        "tab_count": 0,
        "word_count": 0,
        "h1_count": 0,
        "h2_count": 0,
        "rejected_native_fields_count": 0,
        "paragraph_tab_stops": False,
        "paragraph_borders": False,
        "paragraph_shading": False,
        "custom_header_footer_margins_present": False,
        "custom_header_footer_margins_enabled": False,
        "custom_header_footer_margins_malformed": False,
        "document_segment_reference_count": 0,
        "unknown_header_footer_style_count": 0,
    }
    tabs = document.get("tabs", [])
    pending = list(tabs) if isinstance(tabs, list) else []
    while pending:
        tab = pending.pop()
        if not isinstance(tab, dict):
            continue
        checks["tab_count"] += 1
        children = tab.get("childTabs", [])
        if isinstance(children, list):
            pending.extend(children)
        native = tab.get("documentTab", {})
        if not isinstance(native, dict):
            continue
        _accumulate_layout_checks(native, checks)
        checks["rejected_native_fields_count"] += sum(
            bool(value)
            for key, value in native.items()
            if key not in {"body", "documentStyle", "namedStyles", "lists"}
        )
        body = native.get("body", {})
        if isinstance(body, dict):
            _accumulate_paragraph_checks(body.get("content", []), checks)

    return checks


def _accumulate_paragraph_checks(content: object, checks: dict[str, int | bool]) -> None:
    if not isinstance(content, list):
        return
    for element in content:
        if not isinstance(element, dict):
            continue
        paragraph = element.get("paragraph", {})
        if not isinstance(paragraph, dict):
            continue
        style = paragraph.get("paragraphStyle", {})
        if not isinstance(style, dict):
            continue
        checks["h1_count"] += style.get("namedStyleType") == "HEADING_1"
        checks["h2_count"] += style.get("namedStyleType") == "HEADING_2"
        checks["paragraph_tab_stops"] |= "tabStops" in style
        checks["paragraph_borders"] |= any(field in style for field in _BORDER_FIELDS)
        checks["paragraph_shading"] |= "shading" in style
        checks["word_count"] += len(_paragraph_text(paragraph.get("elements", [])).split())


def _paragraph_text(runs: JsonValue) -> str:
    if not isinstance(runs, list):
        return ""
    text = []
    for run in runs:
        payload = run.get("textRun", {}) if isinstance(run, dict) else {}
        value = payload.get("content", "") if isinstance(payload, dict) else ""
        if isinstance(value, str):
            text.append(value)
    return "".join(text)


def _accumulate_layout_checks(native: object, checks: dict[str, int | bool]) -> None:
    if not isinstance(native, dict):
        return
    document_style = native.get("documentStyle", {})
    if not isinstance(document_style, dict):
        return
    present = "useCustomHeaderFooterMargins" in document_style
    custom = document_style.get("useCustomHeaderFooterMargins")
    checks["custom_header_footer_margins_present"] |= present
    checks["custom_header_footer_margins_enabled"] |= custom is True
    checks["custom_header_footer_margins_malformed"] |= present and not isinstance(custom, bool)
    checks["document_segment_reference_count"] += sum(
        key in document_style for key in _SEGMENT_REFERENCE_FIELDS
    )
    checks["unknown_header_footer_style_count"] += sum(
        isinstance(key, str)
        and ("header" in key.lower() or "footer" in key.lower())
        and key not in HEADER_FOOTER_LAYOUT_FIELDS
        and key not in _SEGMENT_REFERENCE_FIELDS
        for key in document_style
    )


_BORDER_FIELDS = frozenset(
    {"borderBetween", "borderTop", "borderBottom", "borderLeft", "borderRight"}
)
_SEGMENT_REFERENCE_FIELDS = frozenset(
    {
        "defaultHeaderId",
        "defaultFooterId",
        "firstPageHeaderId",
        "firstPageFooterId",
        "evenPageHeaderId",
        "evenPageFooterId",
    }
)


_TEXT_FIELDS = frozenset(
    {
        "bold",
        "italic",
        "underline",
        "strikethrough",
        "smallCaps",
        "fontSize",
        "weightedFontFamily",
        "foregroundColor",
        "backgroundColor",
        "baselineOffset",
    }
)
_PARAGRAPH_FIELDS = frozenset(
    {
        "namedStyleType",
        "alignment",
        "lineSpacing",
        "spaceAbove",
        "spaceBelow",
        "indentStart",
        "indentEnd",
        "indentFirstLine",
        "keepWithNext",
        "avoidWidowAndOrphan",
        "keepLinesTogether",
        "direction",
        "spacingMode",
        "pageBreakBefore",
    }
)


def executable_structure(plan: TabStructurePlan, *, copy_mode: bool) -> bool:
    if not copy_mode or plan.lists_json != "{}" or not plan.preserves_inventory(plan.sections):
        return False
    for section in plan.sections:
        for snapshot in section.paragraphs:
            paragraph = json.loads(snapshot)["paragraph"]
            if "bullet" in paragraph or set(
                paragraph.get("paragraphStyle", {})
            ) - _PARAGRAPH_FIELDS - {"headingId"}:
                return False
            for element in paragraph["elements"]:
                if set(element["textRun"].get("textStyle", {})) - _TEXT_FIELDS:
                    return False
    return True


def prepare_tab_creation(
    checkpoint: PlannedStructureCheckpoint,
) -> StructureDispatch[CreatingStructureCheckpoint]:
    _require_structure_state(checkpoint, PlannedStructureCheckpoint)
    if not executable_structure(checkpoint.plan, copy_mode=True):
        raise StructureStateError
    # Validate the largest subsequent write before creating even an empty tab.
    prepare_content_move(
        CreatedStructureCheckpoint(
            plan=checkpoint.plan,
            revision_id=checkpoint.plan.revision_id,
            created_tab_ids=tuple(
                DocsTabId(f"preflight-{index}")
                for index in range(len(checkpoint.plan.sections) - 1)
            ),
        )
    )
    # Persist this returned state before dispatch. An ambiguous response must NOT
    # be retried: Docs generates IDs and there is no documented idempotency key.
    intent = CreatingStructureCheckpoint(checkpoint.plan)
    requests: list[dict[str, JsonValue]] = [
        {
            "addDocumentTab": {
                "tabProperties": {
                    "title": section.title,
                    "index": index,
                    **({"iconEmoji": section.icon_emoji} if section.icon_emoji is not None else {}),
                }
            }
        }
        for index, section in enumerate(checkpoint.plan.sections[1:], start=1)
    ]
    requests.append(
        {
            "createNamedRange": {
                "name": _marker_name(checkpoint),
                "range": {
                    "tabId": checkpoint.plan.source_tab_id,
                    "startIndex": 1,
                    "endIndex": 1 + utf16_length(checkpoint.plan.sections[0].text[0]),
                },
            }
        }
    )
    return StructureDispatch(
        intent,
        {
            "requests": requests,
            "writeControl": {"requiredRevisionId": checkpoint.plan.revision_id},
        },
    )


def recover_created_tabs(
    checkpoint: StructureCheckpoint, document: dict[str, JsonValue]
) -> StructureCheckpoint:
    if checkpoint.stage is not StructureStage.CREATING:
        raise StructureStateError
    tabs = _structure_tabs(document)
    if len(tabs) != len(checkpoint.plan.sections) or not _has_marker(checkpoint, document):
        raise StructureStateError
    source = tabs[0]
    if not _creation_source_matches(checkpoint, source):
        raise StructureStateError
    ids = []
    for tab, section in zip(tabs[1:], checkpoint.plan.sections[1:], strict=True):
        if not _tab_properties_match(tab, section, tab_id=None):
            raise StructureStateError
        if not _native_styles_match(tab, source) or not _empty_created_tab(checkpoint, tab):
            raise StructureStateError
        properties = tab["tabProperties"]
        if not isinstance(properties, dict):
            raise StructureStateError
        tab_id = properties.get("tabId")
        if not isinstance(tab_id, str):
            raise StructureStateError
        ids.append(DocsTabId(tab_id))
    revision_id = document.get("revisionId")
    if not isinstance(revision_id, str):
        raise StructureStateError
    return bind_created_tabs(
        checkpoint,
        tab_ids=tuple(DocsTabId(value) for value in ids),
        revision_id=DocsRevisionId(revision_id),
    )


def _creation_source_matches(checkpoint: StructureCheckpoint, source: dict[str, JsonValue]) -> bool:
    if source.get("tabProperties", {}) != json.loads(checkpoint.plan.source_tab_properties_json):
        return False
    actual = [
        element
        for element in _native_body_content(source)
        if isinstance(element, dict) and "paragraph" in element
    ]
    expected = [
        json.loads(snapshot)
        for section in checkpoint.plan.sections
        for snapshot in section.paragraphs
    ]
    return actual == expected


def _marker_name(checkpoint: StructureCheckpoint) -> str:
    return "wiki.tab-structure." + checkpoint.plan.inventory_sha256


def _has_marker(checkpoint: StructureCheckpoint, document: dict[str, JsonValue]) -> bool:
    tabs = _structure_tabs(document)
    if not tabs:
        return False
    name = _marker_name(checkpoint)
    groups = _native_tab_payload(tabs[0]).get("namedRanges", {})
    if not isinstance(groups, dict):
        return False
    group = groups.get(name, {})
    if not isinstance(group, dict):
        return False
    definition = _single_mapping(group.get("namedRanges", []))
    if definition is None or definition.get("name") != name:
        return False
    span = _single_mapping(definition.get("ranges", []))
    return (
        span is not None
        and span.get("startIndex") == 1
        and span.get("endIndex") == 1 + utf16_length(checkpoint.plan.sections[0].text[0])
        and span.get("tabId", checkpoint.plan.source_tab_id) == checkpoint.plan.source_tab_id
        and not span.get("segmentId")
    )


def _single_mapping(value: JsonValue) -> dict[str, JsonValue] | None:
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        return None
    return value[0]


def bind_created_tabs(
    checkpoint: CreatingStructureCheckpoint,
    *,
    tab_ids: tuple[DocsTabId, ...],
    revision_id: DocsRevisionId,
) -> CreatedStructureCheckpoint:
    if (
        checkpoint.stage is not StructureStage.CREATING
        or len(tab_ids) != len(checkpoint.plan.sections) - 1
        or len(set(tab_ids)) != len(tab_ids)
        or any(not value or value == checkpoint.plan.source_tab_id for value in tab_ids)
        or not revision_id
    ):
        raise StructureStateError
    return CreatedStructureCheckpoint(checkpoint.plan, tab_ids, revision_id)


def prepare_content_move(
    checkpoint: CreatedStructureCheckpoint,
) -> StructureDispatch[MovingStructureCheckpoint]:
    _require_structure_state(checkpoint, CreatedStructureCheckpoint)
    if not checkpoint.revision_id:
        raise StructureStateError
    plan = checkpoint.plan
    if len(checkpoint.created_tab_ids) != len(plan.sections) - 1:
        raise StructureStateError
    requests: list[dict[str, JsonValue]] = []
    for section, tab_id in zip(plan.sections[1:], checkpoint.created_tab_ids, strict=True):
        # Each new native tab already contains its required terminal newline.
        requests.append(
            {"insertText": {"location": {"tabId": tab_id, "index": 1}, "text": section.text[:-1]}}
        )
        requests.extend(_restore_styles(section, tab_id))
    requests.append(
        {
            "deleteContentRange": {
                "range": {
                    "tabId": plan.source_tab_id,
                    "startIndex": plan.sections[0].end_index - 1,
                    "endIndex": plan.sections[-1].end_index - 1,
                }
            }
        }
    )
    requests.extend(_restore_styles(plan.sections[0], plan.source_tab_id))
    requests.append(
        {
            "updateDocumentTabProperties": {
                "tabProperties": {
                    "tabId": plan.source_tab_id,
                    "title": plan.sections[0].title,
                    **(
                        {"iconEmoji": plan.sections[0].icon_emoji}
                        if plan.sections[0].icon_emoji is not None
                        else {}
                    ),
                },
                "fields": "title,iconEmoji" if plan.sections[0].icon_emoji is not None else "title",
            }
        }
    )
    if (
        len(requests) > MAX_STRUCTURE_REQUESTS
        or len(json.dumps(requests, ensure_ascii=False).encode()) > MAX_STRUCTURE_BYTES
    ):
        raise StructureStateError
    # One atomic batch inserts all destinations and removes the source tail.
    # Persist MOVING before dispatch; recover only by exact complete readback.
    return StructureDispatch(
        MovingStructureCheckpoint(
            checkpoint.plan, checkpoint.created_tab_ids, checkpoint.revision_id
        ),
        {
            "requests": requests,
            "writeControl": {"requiredRevisionId": checkpoint.revision_id},
        },
    )


def verify_structure_content(checkpoint: StructureCheckpoint, document: dict[str, Any]) -> bool:
    if checkpoint.stage not in {StructureStage.MOVING, StructureStage.VERIFIED}:
        return False
    if not _has_marker(checkpoint, document):
        return False
    expected_ids = (checkpoint.plan.source_tab_id, *checkpoint.created_tab_ids)
    tabs: list[dict[str, Any]] = document.get("tabs", [])
    if len(tabs) != len(expected_ids):
        return False
    return all(
        _verified_section(checkpoint, tab, tab_id, section, tabs[0])
        for tab, tab_id, section in zip(tabs, expected_ids, checkpoint.plan.sections, strict=True)
    )


def _tab_properties_match(tab: JsonValue, section: TabSection, *, tab_id: DocsTabId | None) -> bool:
    if not isinstance(tab, dict):
        return False
    properties = tab.get("tabProperties", {})
    if not isinstance(properties, dict) or set(properties) - {
        "tabId",
        "title",
        "index",
        "parentTabId",
        "nestingLevel",
        "iconEmoji",
    }:
        return False
    if tab.get("childTabs") or properties.get("title") != section.title:
        return False
    if properties.get("iconEmoji") != section.icon_emoji:
        return False
    return tab_id is None or properties.get("tabId") == tab_id


def _native_styles_match(tab: JsonValue, source: JsonValue) -> bool:
    if not isinstance(tab, dict) or not isinstance(source, dict):
        return False
    native, original = tab.get("documentTab", {}), source.get("documentTab", {})
    if not isinstance(native, dict) or not isinstance(original, dict):
        return False
    return all(
        native.get(key, {}) == original.get(key, {}) for key in ("documentStyle", "namedStyles")
    )


def _empty_created_tab(checkpoint: StructureCheckpoint, tab: JsonValue) -> bool:
    if not isinstance(tab, dict):
        return False
    native = tab.get("documentTab", {})
    if not isinstance(native, dict) or any(
        value
        for key, value in native.items()
        if key not in {"body", "documentStyle", "namedStyles"}
    ):
        return False
    content = _native_body_content(tab)
    if not content or content[0] != json.loads(checkpoint.plan.section_break_json):
        return False
    paragraphs = [item for item in content if isinstance(item, dict) and "paragraph" in item]
    if len(paragraphs) != 1:
        return False
    paragraph = paragraphs[0]["paragraph"]
    if not isinstance(paragraph, dict):
        return False
    runs = paragraph.get("elements", [])
    if not isinstance(runs, list) or len(runs) != 1 or not isinstance(runs[0], dict):
        return False
    text = runs[0].get("textRun", {})
    return isinstance(text, dict) and text.get("content") == "\n"


def _verified_section(
    checkpoint: StructureCheckpoint,
    tab: JsonValue,
    tab_id: DocsTabId,
    section: TabSection,
    source: JsonValue,
) -> bool:
    if not isinstance(tab, dict) or not _tab_properties_match(tab, section, tab_id=tab_id):
        return False
    if not _native_styles_match(tab, source):
        return False
    native = _native_tab_payload(tab)
    ranges = native.get("namedRanges")
    if ranges and (
        not isinstance(ranges, dict)
        or tab_id != checkpoint.plan.source_tab_id
        or set(ranges) != {_marker_name(checkpoint)}
    ):
        return False
    if any(
        value
        for key, value in native.items()
        if key not in {"body", "documentStyle", "namedStyles", "namedRanges"}
    ):
        return False
    return _section_content_matches(checkpoint, section, _native_body_content(tab))


def _section_content_matches(
    checkpoint: StructureCheckpoint, section: TabSection, elements: object
) -> bool:
    if (
        not isinstance(elements, list)
        or not elements
        or elements[0] != json.loads(checkpoint.plan.section_break_json)
    ):
        return False
    paragraphs = [
        element for element in elements if isinstance(element, dict) and "paragraph" in element
    ]
    if len(paragraphs) != len(section.paragraphs):
        return False
    for actual, snapshot in zip(paragraphs, section.paragraphs, strict=True):
        if not _same_paragraph(actual, json.loads(snapshot), section.start_index - 1):
            return False
    return all("paragraph" in element or "sectionBreak" in element for element in elements)


def _restore_styles(section: TabSection, tab_id: str) -> list[dict[str, Any]]:
    requests: list[dict[str, JsonValue]] = []
    offset = section.start_index - 1
    for snapshot in section.paragraphs:
        element = json.loads(snapshot)
        paragraph = element["paragraph"]
        requests.append(
            {
                "updateParagraphStyle": {
                    "range": {
                        "tabId": tab_id,
                        "startIndex": element["startIndex"] - offset,
                        "endIndex": element["endIndex"] - offset,
                    },
                    "paragraphStyle": {
                        key: value
                        for key, value in paragraph.get("paragraphStyle", {}).items()
                        if key != "headingId"
                    },
                    "fields": ",".join(sorted(_PARAGRAPH_FIELDS)),
                }
            }
        )
        requests.extend(
            {
                "updateTextStyle": {
                    "range": {
                        "tabId": tab_id,
                        "startIndex": run["startIndex"] - offset,
                        "endIndex": run["endIndex"] - offset,
                    },
                    "textStyle": run["textRun"].get("textStyle", {}),
                    "fields": ",".join(sorted(_TEXT_FIELDS)),
                }
            }
            for run in paragraph["elements"]
        )
    return requests


def _same_paragraph(
    actual: dict[str, JsonValue], expected: dict[str, JsonValue], offset: int
) -> bool:
    if not _offset_paragraph(expected, offset):
        return False
    actual = deepcopy(actual)
    return (
        _remove_generated_heading(actual)
        and _remove_generated_heading(expected)
        and actual == expected
    )


def _offset_paragraph(value: dict[str, JsonValue], offset: int) -> bool:
    if not _offset_indices(value, offset):
        return False
    paragraph = value.get("paragraph")
    if not isinstance(paragraph, dict):
        return False
    runs = paragraph.get("elements")
    if not isinstance(runs, list):
        return False
    return all(isinstance(run, dict) and _offset_indices(run, offset) for run in runs)


def _offset_indices(value: dict[str, JsonValue], offset: int) -> bool:
    for key in ("startIndex", "endIndex"):
        index = value.get(key)
        if not isinstance(index, int) or isinstance(index, bool):
            return False
        value[key] = index - offset
    return True


def _remove_generated_heading(value: dict[str, JsonValue]) -> bool:
    paragraph = value.get("paragraph")
    if not isinstance(paragraph, dict):
        return False
    style = paragraph.get("paragraphStyle", {})
    if not isinstance(style, dict):
        return False
    style.pop("headingId", None)
    return True


def planned_structure_document(
    checkpoint: StructureCheckpoint, original: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    if checkpoint.stage is not StructureStage.VERIFIED:
        raise StructureStateError
    source = deepcopy(original)
    native = _native_tab_payload(_structure_tabs(source)[0])
    tabs: list[JsonValue] = []
    for index, (section, tab_id) in enumerate(
        zip(
            checkpoint.plan.sections,
            (checkpoint.plan.source_tab_id, *checkpoint.created_tab_ids),
            strict=True,
        )
    ):
        body = [json.loads(checkpoint.plan.section_break_json)]
        for snapshot in section.paragraphs:
            element = json.loads(snapshot)
            offset = section.start_index - 1
            if not _offset_paragraph(element, offset):
                raise StructureStateError
            body.append(element)
        tab_native = deepcopy(native)
        tab_native["body"] = {"content": body}
        tabs.append(
            {
                "tabProperties": {
                    "tabId": tab_id,
                    "title": section.title,
                    "index": index,
                    **({"iconEmoji": section.icon_emoji} if section.icon_emoji is not None else {}),
                },
                "documentTab": tab_native,
            }
        )
    source["tabs"] = tabs
    return source


def complete_structure(
    checkpoint: MovingStructureCheckpoint, revision_id: DocsRevisionId
) -> VerifiedStructureCheckpoint:
    _require_structure_state(checkpoint, MovingStructureCheckpoint)
    if not revision_id:
        raise StructureStateError
    return VerifiedStructureCheckpoint(checkpoint.plan, checkpoint.created_tab_ids, revision_id)


def _require_structure_state(value: object, expected: type[StructurePlanCheckpoint]) -> None:
    if not isinstance(value, expected):
        raise StructureStateError


def _structure_tabs(document: dict[str, JsonValue]) -> list[dict[str, JsonValue]]:
    value = document.get("tabs", [])
    if not isinstance(value, list):
        raise StructureStateError
    tabs = []
    for tab in value:
        if not isinstance(tab, dict):
            raise StructureStateError
        tabs.append(tab)
    return tabs


def _native_tab_payload(tab: dict[str, JsonValue]) -> dict[str, JsonValue]:
    native = tab.get("documentTab", {})
    if not isinstance(native, dict):
        raise StructureStateError
    return native


def _native_body_content(tab: dict[str, JsonValue]) -> list[JsonValue]:
    body = _native_tab_payload(tab).get("body", {})
    if not isinstance(body, dict):
        raise StructureStateError
    content = body.get("content", [])
    if not isinstance(content, list):
        raise StructureStateError
    return content
