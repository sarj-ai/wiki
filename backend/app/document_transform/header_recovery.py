from typing import Any
import uuid

from app.document_transform.brand import mutation_sha256
from app.document_transform.gateway import InPlaceInspection, InPlaceStageResult, SourceSnapshot
from app.document_transform.ir import MAX_DOC_TABS, parse_google_document
from app.document_transform.schemas import InPlaceCheckpoint, InPlaceCheckpointStage


def recover_empty_header_postcondition(
    snapshot: SourceSnapshot,
    inspection: InPlaceInspection,
    checkpoint: InPlaceCheckpoint,
    operation_id: uuid.UUID,
) -> InPlaceStageResult | None:
    document = snapshot.google_document
    targets = _requested_tabs(checkpoint.requests)
    digest = mutation_sha256(checkpoint.requests)
    if (
        checkpoint.stage is not InPlaceCheckpointStage.HEADERS
        or document is None
        or snapshot.revision_id is None
        or snapshot.revision_id == checkpoint.current_revision_id
        or not targets
    ):
        return None
    if checkpoint.marker_range is None or any(
        (
            checkpoint.headers,
            checkpoint.preserved_headers,
            inspection.unknown_header_tab_ids,
            inspection.missing_header_tab_ids,
            inspection.preserved_headers,
        )
    ):
        return None
    if (
        document.get("revisionId"),
        document.get("documentId"),
        digest,
        f"sarj-transform-{operation_id.hex}-headers-{digest[:20]}",
        inspection.marker_range,
        inspection.custom_furniture,
        inspection.header_identity,
        inspection.structural_sha256,
        inspection.protected_structure_sha256,
        inspection.body_sha256,
        inspection.header_safe_sha256,
    ) != (
        snapshot.revision_id,
        checkpoint.document_id,
        checkpoint.mutation_sha256,
        checkpoint.marker_name,
        checkpoint.marker_range,
        checkpoint.custom_furniture,
        checkpoint.header_identity,
        checkpoint.structural_sha256,
        checkpoint.protected_structure_sha256,
        checkpoint.body_sha256,
        checkpoint.header_safe_sha256,
    ):
        return None
    if (
        parse_google_document(document, title=snapshot.title).semantic_sha256
        != checkpoint.original_semantic_sha256
    ):
        return None
    headers = {str(header.tab_id): str(header.header_id) for header in inspection.headers}
    if len(headers) != len(inspection.headers) or set(headers) != set(targets):
        return None
    if not _header_inventory_matches(document, headers, checkpoint):
        return None
    return InPlaceStageResult(snapshot.revision_id, document)


def _requested_tabs(requests: list[dict[str, Any]]) -> tuple[str, ...] | None:
    if not 0 < len(requests) <= MAX_DOC_TABS:
        return None
    tabs: list[str] = []
    for request in requests:
        if set(request) != {"createHeader"}:
            return None
        creation = request["createHeader"]
        if not isinstance(creation, dict) or set(creation) != {"type", "sectionBreakLocation"}:
            return None
        location = creation["sectionBreakLocation"]
        if (
            creation["type"] != "DEFAULT"
            or not isinstance(location, dict)
            or set(location) != {"index", "tabId"}
        ):
            return None
        if (
            (type(location["index"]), location["index"]) != (int, 0)
            or not isinstance(location["tabId"], str)
            or not location["tabId"]
            or location["tabId"] in tabs
        ):
            return None
        tabs.append(location["tabId"])
    return tuple(tabs)


def _header_inventory_matches(
    document: dict[str, Any], new_headers: dict[str, str], checkpoint: InPlaceCheckpoint
) -> bool:
    expected = {tab_id: {header_id} for tab_id, header_id in new_headers.items()}
    for binding in checkpoint.custom_furniture:
        if binding.tab_id in new_headers and binding.header_ids:
            return False
        expected.setdefault(binding.tab_id, set()).update(binding.header_ids)
    pending = document.get("tabs")
    if not isinstance(pending, list):
        return False
    pending = list(pending)
    seen: set[str] = set()
    while pending:
        tab = pending.pop()
        if not isinstance(tab, dict):
            return False
        properties, native = tab.get("tabProperties"), tab.get("documentTab")
        if not isinstance(properties, dict) or not isinstance(native, dict):
            return False
        tab_id = properties.get("tabId")
        if not isinstance(tab_id, str) or not tab_id or tab_id in seen:
            return False
        seen.add(tab_id)
        if len(seen) > MAX_DOC_TABS:
            return False
        headers = native.get("headers", {})
        if not isinstance(headers, dict) or set(headers) != expected.get(tab_id, set()):
            return False
        if tab_id in new_headers:
            style = native.get("documentStyle")
            header_id = new_headers[tab_id]
            if (
                not isinstance(style, dict)
                or style.get("defaultHeaderId") != header_id
                or any(field in style for field in ("firstPageHeaderId", "evenPageHeaderId"))
                or not _empty_header(headers.get(header_id), header_id)
            ):
                return False
        children = tab.get("childTabs", [])
        if not isinstance(children, list):
            return False
        pending.extend(children)
    return set(expected) <= seen


def _empty_header(value: object, header_id: str) -> bool:
    if not isinstance(value, dict) or not set(value) <= {"headerId", "content"}:
        return False
    if "headerId" in value and value.get("headerId") != header_id:
        return False
    content = value.get("content")
    if content == []:
        return True
    if not isinstance(content, list) or len(content) != 1:
        return False
    element = content[0]
    if not isinstance(element, dict) or not _terminal_wrapper(element, "paragraph"):
        return False
    paragraph = element.get("paragraph")
    if not isinstance(paragraph, dict) or not set(paragraph) <= {"elements", "paragraphStyle"}:
        return False
    style = paragraph.get("paragraphStyle", {})
    if not isinstance(style, dict) or not set(style) <= {"namedStyleType"}:
        return False
    if style and style != {"namedStyleType": "NORMAL_TEXT"}:
        return False
    runs = paragraph.get("elements")
    if not isinstance(runs, list) or len(runs) != 1 or not _terminal_wrapper(runs[0], "textRun"):
        return False
    wrapper = runs[0]
    if not isinstance(wrapper, dict):
        return False
    run = wrapper.get("textRun")
    return (
        isinstance(run, dict)
        and set(run) <= {"content", "textStyle"}
        and run.get("content") == "\n"
        and run.get("textStyle", {}) == {}
    )


def _terminal_wrapper(value: object, field: str) -> bool:
    return (
        isinstance(value, dict)
        and set(value) <= {field, "startIndex", "endIndex"}
        and field in value
        and (
            "startIndex" not in value
            or (type(value.get("startIndex")) is int and value.get("startIndex") == 0)
        )
        and (
            "endIndex" not in value
            or (type(value.get("endIndex")) is int and value.get("endIndex") == 1)
        )
    )
