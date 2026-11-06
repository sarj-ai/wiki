from typing import Literal, NamedTuple, Never

from loguru import logger
from pydantic import JsonValue

from app.document_transform.errors import InPlaceNeedsReviewError
from app.identifiers import DocsSegmentId, DocsTabId


SafetyStage = Literal[
    "footer_plan",
    "footer_create",
    "footer_populate",
    "footer_advance",
    "footer_coverage",
    "footer_projection_empty",
    "footer_projection_populated",
    "footer_tab",
    "tab_naming_plan",
    "tab_naming_pre",
    "tab_naming_post",
    "date_plan",
    "date_pre",
]
SafetyReason = Literal[
    "revision",
    "topology",
    "marker",
    "segment",
    "shape",
    "invariant",
    "action",
    "preimage",
]


FooterShapeCheck = Literal[
    "footer_container",
    "footer_fields",
    "content",
    "paragraph_count",
    "paragraph_container",
    "paragraph_fields",
    "runs",
    "run_container",
    "run_fields",
    "text_run",
    "text_run_fields",
    "text_content",
    "caption",
    "text_style",
    "link",
    "font_family",
    "font_size",
    "bold",
    "italic",
    "alignment",
    "named_style",
]


class FooterShapeFailure(NamedTuple):
    check: FooterShapeCheck
    reason: Literal["missing", "mismatch"]


def native_safety_error(
    stage: SafetyStage,
    reason: SafetyReason,
    *,
    footer_shape: FooterShapeFailure | None = None,
) -> Never:
    # Static caller vocabulary only: no provider content, identifiers or hashes.
    # Raising the existing error preserves every caller's fail-closed policy.
    logger.warning(
        "document native safety stop stage={safety_stage} reason={safety_reason}",
        safety_stage=stage,
        safety_reason=reason,
        **(
            {"footer_shape_check": footer_shape.check, "footer_shape_reason": footer_shape.reason}
            if footer_shape is not None
            else {}
        ),
    )
    raise InPlaceNeedsReviewError


MAX_FOOTER_SNAPSHOT_ITEMS = 64
MAX_FOOTER_SNAPSHOT_DEPTH = 8
FooterSnapshotPhase = Literal["create_input", "projection_empty", "create_reply"]
FooterSnapshotState = Literal[
    "unavailable", "missing", "null", "empty", "nonempty", "present", "malformed"
]


class _FooterTabSnapshot(NamedTuple):
    tab: JsonValue
    matches: int
    complete: bool


def _footer_snapshot_field(parent: JsonValue, key: str) -> JsonValue:
    return parent.get(key) if isinstance(parent, dict) else None


def _footer_snapshot_state(
    parent: JsonValue, key: str, kind: Literal["map", "list", "id"]
) -> FooterSnapshotState:
    if not isinstance(parent, dict):
        return "unavailable"
    if key not in parent:
        return "missing"
    value = parent[key]
    if value is None:
        return "null"
    match kind, value:
        case ("map", dict()) | ("list", list()):
            return "nonempty" if value else "empty"
        case "id", str():
            return "present" if value else "empty"
        case _:
            return "malformed"


def _footer_snapshot_tab(document: JsonValue, tab_id: DocsTabId) -> _FooterTabSnapshot:
    tabs = _footer_snapshot_field(document, "tabs")
    if not isinstance(tabs, list):
        return _FooterTabSnapshot(tab=None, matches=0, complete=False)
    pending = [(tab, 0) for tab in reversed(tabs[:MAX_FOOTER_SNAPSHOT_ITEMS])]
    complete = len(tabs) <= MAX_FOOTER_SNAPSHOT_ITEMS
    selected: JsonValue = None
    matches = 0
    visited = 0
    while pending and visited < MAX_FOOTER_SNAPSHOT_ITEMS:
        tab, depth = pending.pop()
        visited += 1
        properties = _footer_snapshot_field(tab, "tabProperties")
        if _footer_snapshot_field(properties, "tabId") == tab_id:
            matches += 1
            selected = _footer_snapshot_field(tab, "documentTab")
        children = _footer_snapshot_field(tab, "childTabs")
        if children is None:
            continue
        if not isinstance(children, list) or depth >= MAX_FOOTER_SNAPSHOT_DEPTH:
            complete = False
            continue
        complete = complete and len(children) <= MAX_FOOTER_SNAPSHOT_ITEMS
        pending.extend(
            (child, depth + 1) for child in reversed(children[:MAX_FOOTER_SNAPSHOT_ITEMS])
        )
    return _FooterTabSnapshot(selected if matches == 1 else None, matches, complete and not pending)


class _FooterSnapshotCounts(NamedTuple):
    paragraphs: int
    elements: int
    complete: bool


def footer_snapshot_fields(
    document: JsonValue, tab_id: DocsTabId, *, created_id: DocsSegmentId | None = None
) -> dict[str, str | int | bool]:
    selected = _footer_snapshot_tab(document, tab_id)
    return {
        "footer_snapshot_tab_matches": selected.matches,
        "footer_snapshot_tabs_complete": selected.complete,
        **_footer_snapshot_scope(document, "document", created_id),
        **_footer_snapshot_scope(selected.tab, "tab", created_id),
    }


def log_footer_snapshot(document: JsonValue, tab_id: DocsTabId, phase: FooterSnapshotPhase) -> None:
    logger.info(
        "document footer shape snapshot phase={footer_snapshot_phase} diagnostics={diagnostics}",
        footer_snapshot_phase=phase,
        diagnostics=footer_snapshot_fields(document, tab_id),
    )


class _CreatedFooterResponse(NamedTuple):
    footer_id: DocsSegmentId | None
    state: FooterSnapshotState
    reply_count: int
    complete: bool


def log_footer_created_reply(
    document: JsonValue, requests: list[dict[str, JsonValue]], response: JsonValue
) -> None:
    creation = [
        request["createFooter"]
        for request in requests[:MAX_FOOTER_SNAPSHOT_ITEMS]
        if "createFooter" in request
    ]
    if len(creation) != 1:
        return
    location = _footer_snapshot_field(creation[0], "sectionBreakLocation")
    tab_id = _footer_snapshot_field(location, "tabId")
    if not isinstance(tab_id, str) or not tab_id:
        return
    footer_id, state, count, complete = _created_footer_response(response)
    logger.info(
        "document footer shape snapshot phase={footer_snapshot_phase} diagnostics={diagnostics}",
        footer_snapshot_phase="create_reply",
        diagnostics={
            **footer_snapshot_fields(document, DocsTabId(tab_id), created_id=footer_id),
            "footer_snapshot_created_id_state": state,
            "footer_snapshot_created_reply_count": count,
            "footer_snapshot_reply_complete": complete
            and len(requests) <= MAX_FOOTER_SNAPSHOT_ITEMS,
        },
    )


def _created_footer_response(response: JsonValue) -> _CreatedFooterResponse:
    replies = _footer_snapshot_field(response, "replies")
    if not isinstance(replies, list):
        return _CreatedFooterResponse(
            footer_id=None,
            state=_footer_snapshot_state(response, "replies", "list"),
            reply_count=0,
            complete=True,
        )
    created = [
        reply["createFooter"]
        for reply in replies[:MAX_FOOTER_SNAPSHOT_ITEMS]
        if isinstance(reply, dict) and "createFooter" in reply
    ]
    complete = len(replies) <= MAX_FOOTER_SNAPSHOT_ITEMS
    if len(created) != 1:
        return _CreatedFooterResponse(
            None, "missing" if not created else "malformed", len(created), complete
        )
    state = _footer_snapshot_state(created[0], "footerId", "id")
    footer_id = _footer_snapshot_field(created[0], "footerId")
    return _CreatedFooterResponse(
        DocsSegmentId(footer_id) if isinstance(footer_id, str) and footer_id else None,
        state,
        1,
        complete,
    )


def _footer_snapshot_scope(
    parent: JsonValue, scope: Literal["document", "tab"], created_id: DocsSegmentId | None
) -> dict[str, str | int | bool]:
    style = _footer_snapshot_field(parent, "documentStyle")
    reference = _footer_snapshot_field(style, "defaultFooterId")
    footers = _footer_snapshot_field(parent, "footers")
    selected_id = created_id or (reference if isinstance(reference, str) and reference else None)
    footer = footers.get(selected_id) if isinstance(footers, dict) and selected_id else None
    paragraphs, elements, complete = _footer_snapshot_counts(
        _footer_snapshot_field(footer, "content")
    )
    prefix = f"footer_snapshot_{scope}"
    return {
        f"{prefix}_map_state": _footer_snapshot_state(parent, "footers", "map"),
        f"{prefix}_map_count": (
            min(len(footers), MAX_FOOTER_SNAPSHOT_ITEMS) if isinstance(footers, dict) else 0
        ),
        f"{prefix}_ref_state": _footer_snapshot_state(style, "defaultFooterId", "id"),
        f"{prefix}_ref_in_map": (
            isinstance(reference, str) and isinstance(footers, dict) and reference in footers
        ),
        f"{prefix}_created_in_map": (
            created_id is not None and isinstance(footers, dict) and created_id in footers
        ),
        f"{prefix}_created_matches_ref": created_id is not None and created_id == reference,
        f"{prefix}_content_state": _footer_snapshot_state(footer, "content", "list"),
        f"{prefix}_paragraph_count": paragraphs,
        f"{prefix}_element_count": elements,
        f"{prefix}_complete": (
            complete
            and (not isinstance(footers, dict) or len(footers) <= MAX_FOOTER_SNAPSHOT_ITEMS)
        ),
    }


def _footer_snapshot_counts(content: JsonValue) -> _FooterSnapshotCounts:
    if not isinstance(content, list):
        return _FooterSnapshotCounts(paragraphs=0, elements=0, complete=True)
    paragraphs = 0
    elements = 0
    complete = len(content) <= MAX_FOOTER_SNAPSHOT_ITEMS
    for entry in content[:MAX_FOOTER_SNAPSHOT_ITEMS]:
        paragraph = _footer_snapshot_field(entry, "paragraph")
        if not isinstance(paragraph, dict):
            continue
        paragraphs += 1
        runs = paragraph.get("elements")
        if isinstance(runs, list):
            elements += min(len(runs), MAX_FOOTER_SNAPSHOT_ITEMS)
            complete = complete and len(runs) <= MAX_FOOTER_SNAPSHOT_ITEMS
    return _FooterSnapshotCounts(paragraphs, elements, complete)
