from __future__ import annotations

import asyncio
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, replace
from enum import StrEnum
import json
from typing import Any, NamedTuple

from app.document_transform.errors import OutputVerificationError
from app.document_transform.ir import utf16_length
from app.document_transform.tab_structure import TabSection, TabStructurePlan, plan_tab_structure
from app.identifiers import DocsRevisionId, DocsTabId, FileId


MAX_STRUCTURE_REQUESTS = 1_000
MAX_STRUCTURE_BYTES = 2 * 1024 * 1024
_EMPTY_REVISION = DocsRevisionId("")


class StructureStage(StrEnum):
    PLANNED = "planned"
    CREATING = "creating"
    CREATED = "created"
    MOVING = "moving"
    VERIFIED = "verified"


class StructureStateError(OutputVerificationError):
    pass


@dataclass(frozen=True, slots=True)
class StructureCheckpoint:
    plan: TabStructurePlan
    stage: StructureStage = StructureStage.PLANNED
    created_tab_ids: tuple[DocsTabId, ...] = ()
    revision_id: DocsRevisionId = _EMPTY_REVISION


class StructureDispatch(NamedTuple):
    checkpoint: StructureCheckpoint
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
        if plan is None or not executable_structure(plan, copy_mode=True):
            return None
        if not await asyncio.to_thread(self.comments_absent_sync, source_id):
            return None
        return StructureCheckpoint(plan)


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
    checkpoint: StructureCheckpoint,
) -> StructureDispatch:
    if checkpoint.stage is not StructureStage.PLANNED or not executable_structure(
        checkpoint.plan, copy_mode=True
    ):
        raise StructureStateError
    # Validate the largest subsequent write before creating even an empty tab.
    prepare_content_move(
        replace(
            checkpoint,
            stage=StructureStage.CREATED,
            revision_id=checkpoint.plan.revision_id,
            created_tab_ids=tuple(
                DocsTabId(f"preflight-{index}")
                for index in range(len(checkpoint.plan.sections) - 1)
            ),
        )
    )
    # Persist this returned state before dispatch. An ambiguous response must NOT
    # be retried: Docs generates IDs and there is no documented idempotency key.
    intent = replace(checkpoint, stage=StructureStage.CREATING)
    requests: list[dict[str, Any]] = [
        {"addDocumentTab": {"tabProperties": {"title": section.title, "index": index}}}
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
    checkpoint: StructureCheckpoint, document: dict[str, Any]
) -> StructureCheckpoint:
    if checkpoint.stage is not StructureStage.CREATING:
        raise StructureStateError
    tabs = document.get("tabs", [])
    if len(tabs) != len(checkpoint.plan.sections) or not _has_marker(checkpoint, document):
        raise StructureStateError
    source = tabs[0]
    if source.get("tabProperties", {}).get("tabId") != checkpoint.plan.source_tab_id:
        raise StructureStateError
    actual = [
        element
        for element in source.get("documentTab", {}).get("body", {}).get("content", [])
        if "paragraph" in element
    ]
    expected = [
        json.loads(snapshot)
        for section in checkpoint.plan.sections
        for snapshot in section.paragraphs
    ]
    if actual != expected:
        raise StructureStateError
    ids = []
    for tab, section in zip(tabs[1:], checkpoint.plan.sections[1:], strict=True):
        properties = tab.get("tabProperties", {})
        if tab.get("childTabs") or properties.get("title") != section.title:
            raise StructureStateError
        native = tab.get("documentTab", {})
        if any(
            native.get(key, {}) != source.get("documentTab", {}).get(key, {})
            for key in ("documentStyle", "namedStyles")
        ):
            raise StructureStateError
        if any(
            value
            for key, value in native.items()
            if key not in {"body", "documentStyle", "namedStyles"}
        ):
            raise StructureStateError
        content = native.get("body", {}).get("content", [])
        if not content or content[0] != json.loads(checkpoint.plan.section_break_json):
            raise StructureStateError
        paragraphs = [item for item in content if "paragraph" in item]
        if len(paragraphs) != 1:
            raise StructureStateError
        runs = paragraphs[0]["paragraph"].get("elements", [])
        if len(runs) != 1 or runs[0].get("textRun", {}).get("content") != "\n":
            raise StructureStateError
        ids.append(properties.get("tabId", ""))
    return bind_created_tabs(
        checkpoint,
        tab_ids=tuple(DocsTabId(value) for value in ids),
        revision_id=DocsRevisionId(document.get("revisionId", "")),
    )


def _marker_name(checkpoint: StructureCheckpoint) -> str:
    return "wiki.tab-structure." + checkpoint.plan.inventory_sha256


def _has_marker(checkpoint: StructureCheckpoint, document: dict[str, Any]) -> bool:
    tabs = document.get("tabs", [])
    if not tabs:
        return False
    name = _marker_name(checkpoint)
    groups = tabs[0].get("documentTab", {}).get("namedRanges", {}).get(name, {})
    ranges = groups.get("namedRanges", [])
    if len(ranges) != 1 or ranges[0].get("name") != name:
        return False
    actual = ranges[0].get("ranges", [])
    if len(actual) != 1:
        return False
    span = actual[0]
    return (
        span.get("startIndex") == 1
        and span.get("endIndex") == 1 + utf16_length(checkpoint.plan.sections[0].text[0])
        and span.get("tabId", checkpoint.plan.source_tab_id) == checkpoint.plan.source_tab_id
        and not span.get("segmentId")
    )


def bind_created_tabs(
    checkpoint: StructureCheckpoint, *, tab_ids: tuple[DocsTabId, ...], revision_id: DocsRevisionId
) -> StructureCheckpoint:
    if (
        checkpoint.stage is not StructureStage.CREATING
        or len(tab_ids) != len(checkpoint.plan.sections) - 1
        or len(set(tab_ids)) != len(tab_ids)
        or any(not value or value == checkpoint.plan.source_tab_id for value in tab_ids)
        or not revision_id
    ):
        raise StructureStateError
    return replace(
        checkpoint, stage=StructureStage.CREATED, created_tab_ids=tab_ids, revision_id=revision_id
    )


def prepare_content_move(
    checkpoint: StructureCheckpoint,
) -> StructureDispatch:
    if checkpoint.stage is not StructureStage.CREATED or not checkpoint.revision_id:
        raise StructureStateError
    plan = checkpoint.plan
    if len(checkpoint.created_tab_ids) != len(plan.sections) - 1:
        raise StructureStateError
    requests: list[dict[str, Any]] = []
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
                "tabProperties": {"tabId": plan.source_tab_id, "title": plan.sections[0].title},
                "fields": "title",
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
        replace(checkpoint, stage=StructureStage.MOVING),
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
    for tab, tab_id, section in zip(tabs, expected_ids, checkpoint.plan.sections, strict=True):
        properties = tab.get("tabProperties", {})
        if (
            tab.get("childTabs")
            or properties.get("tabId") != tab_id
            or properties.get("title") != section.title
        ):
            return False
        native = tab.get("documentTab", {})
        if any(
            native.get(key, {}) != tabs[0].get("documentTab", {}).get(key, {})
            for key in ("documentStyle", "namedStyles")
        ):
            return False
        if native.get("namedRanges") and (
            tab_id != checkpoint.plan.source_tab_id
            or set(native["namedRanges"]) != {_marker_name(checkpoint)}
        ):
            return False
        if any(
            value
            for key, value in native.items()
            if key not in {"body", "documentStyle", "namedStyles", "namedRanges"}
        ):
            return False
        elements = native.get("body", {}).get("content", [])
        if not elements or elements[0] != json.loads(checkpoint.plan.section_break_json):
            return False
        paragraphs = [element for element in elements if "paragraph" in element]
        if len(paragraphs) != len(section.paragraphs):
            return False
        for actual, snapshot in zip(paragraphs, section.paragraphs, strict=True):
            expected = json.loads(snapshot)
            if not _same_paragraph(actual, expected, section.start_index - 1):
                return False
        if any(
            "paragraph" not in element and "sectionBreak" not in element for element in elements
        ):
            return False
    return True


def _restore_styles(section: TabSection, tab_id: str) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
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


def _same_paragraph(actual: dict[str, Any], expected: dict[str, Any], offset: int) -> bool:
    # Fail closed on any provider normalization not explicitly represented in
    # this exact reconstruction contract. Never accept word-count equivalence.
    expected["startIndex"] -= offset
    expected["endIndex"] -= offset
    for run in expected["paragraph"]["elements"]:
        run["startIndex"] -= offset
        run["endIndex"] -= offset
    # COPY-only output headings get provider-generated IDs. Internal links and
    # bookmarks are rejected by eligibility; external links target the original.
    actual = deepcopy(actual)
    actual["paragraph"].get("paragraphStyle", {}).pop("headingId", None)
    expected["paragraph"].get("paragraphStyle", {}).pop("headingId", None)
    return actual == expected


def planned_structure_document(
    checkpoint: StructureCheckpoint, original: dict[str, Any]
) -> dict[str, Any]:
    if checkpoint.stage is not StructureStage.VERIFIED:
        raise StructureStateError
    source = deepcopy(original)
    native = source["tabs"][0]["documentTab"]
    tabs = []
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
            element["startIndex"] -= offset
            element["endIndex"] -= offset
            for run in element["paragraph"]["elements"]:
                run["startIndex"] -= offset
                run["endIndex"] -= offset
            body.append(element)
        tab_native = deepcopy(native)
        tab_native["body"] = {"content": body}
        tabs.append(
            {
                "tabProperties": {"tabId": tab_id, "title": section.title, "index": index},
                "documentTab": tab_native,
            }
        )
    source["tabs"] = tabs
    return source
