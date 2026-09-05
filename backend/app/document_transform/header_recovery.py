from typing import Any
import uuid

from loguru import logger

from app.document_transform.brand import mutation_sha256
from app.document_transform.gateway import InPlaceInspection, InPlaceStageResult, SourceSnapshot
from app.document_transform.ir import MAX_DOC_TABS, parse_google_document
from app.document_transform.schemas import InPlaceCheckpoint, InPlaceCheckpointStage


_SINGLE_LINE_SPACING_PERCENT = 100


def recover_empty_header_postcondition(
    snapshot: SourceSnapshot,
    inspection: InPlaceInspection,
    checkpoint: InPlaceCheckpoint,
    operation_id: uuid.UUID,
) -> InPlaceStageResult | None:
    checks: dict[str, bool | str] = {"header_recovery_reason": "state"}
    result = _recover_empty_header_postcondition(
        snapshot, inspection, checkpoint, operation_id, checks
    )
    logger.info("document header recovery checks={checks}", checks=checks)
    return result


def _recover_empty_header_postcondition(
    snapshot: SourceSnapshot,
    inspection: InPlaceInspection,
    checkpoint: InPlaceCheckpoint,
    operation_id: uuid.UUID,
    checks: dict[str, bool | str],
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
    checks["header_recovery_reason"] = "ownership"
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
    checks.update(
        {
            "header_recovery_reason": "invariants",
            "header_recovery_revision_equal": document.get("revisionId") == snapshot.revision_id,
            "header_recovery_document_equal": document.get("documentId") == checkpoint.document_id,
            "header_recovery_digest_equal": digest == checkpoint.mutation_sha256,
            "header_recovery_marker_name_equal": f"sarj-transform-{operation_id.hex}-headers-{digest[:20]}"
            == checkpoint.marker_name,
            "header_recovery_marker_range_equal": inspection.marker_range
            == checkpoint.marker_range,
            "header_recovery_custom_equal": inspection.custom_furniture
            == checkpoint.custom_furniture,
            "header_recovery_identity_equal": inspection.header_identity
            == checkpoint.header_identity,
            "header_recovery_structure_equal": inspection.structural_sha256
            == checkpoint.structural_sha256,
            "header_recovery_protected_equal": inspection.protected_structure_sha256
            == checkpoint.protected_structure_sha256,
            "header_recovery_body_equal": inspection.body_sha256 == checkpoint.body_sha256,
            "header_recovery_safe_equal": inspection.header_safe_sha256
            == checkpoint.header_safe_sha256,
        }
    )
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
    checks["header_recovery_reason"] = "semantic"
    if (
        parse_google_document(document, title=snapshot.title).semantic_sha256
        != checkpoint.original_semantic_sha256
    ):
        return None
    headers = {str(header.tab_id): str(header.header_id) for header in inspection.headers}
    checks["header_recovery_reason"] = "bindings"
    if len(headers) != len(inspection.headers) or set(headers) != set(targets):
        return None
    checks["header_recovery_reason"] = "inventory"
    if not _header_inventory_matches(document, headers, checkpoint, checks):
        return None
    checks["header_recovery_reason"] = "recovered"
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
    document: dict[str, Any],
    new_headers: dict[str, str],
    checkpoint: InPlaceCheckpoint,
    checks: dict[str, bool | str],
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
        checks.pop("header_recovery_empty_reason", None)
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
                or not _empty_header(headers.get(header_id), header_id, checks)
            ):
                return False
        children = tab.get("childTabs", [])
        if not isinstance(children, list):
            return False
        pending.extend(children)
    return set(expected) <= seen


def _empty_header(
    value: object, header_id: str, checks: dict[str, bool | str] | None = None
) -> bool:
    if checks is None:
        checks = {}
    checks["header_recovery_empty_reason"] = "segment"
    if not isinstance(value, dict) or not set(value) <= {"headerId", "content"}:
        return False
    if "headerId" in value and value.get("headerId") != header_id:
        return False
    content = value.get("content")
    if content == []:
        checks["header_recovery_empty_reason"] = "empty"
        return True
    checks["header_recovery_empty_reason"] = "content_count"
    if not isinstance(content, list) or len(content) != 1:
        return False
    element = content[0]
    checks["header_recovery_empty_reason"] = "paragraph_wrapper"
    if not isinstance(element, dict) or not _terminal_wrapper(element, "paragraph"):
        return False
    paragraph = element.get("paragraph")
    checks["header_recovery_empty_reason"] = "paragraph_fields"
    if not isinstance(paragraph, dict) or not set(paragraph) <= {"elements", "paragraphStyle"}:
        return False
    style = paragraph.get("paragraphStyle", {})
    checks["header_recovery_empty_reason"] = "paragraph_style"
    if not isinstance(style, dict) or not set(style) <= {"namedStyleType", "direction"}:
        _paragraph_style_diagnostics(style, checks)
        return False
    if (
        style.get("namedStyleType", "NORMAL_TEXT") != "NORMAL_TEXT"
        or style.get("direction", "LEFT_TO_RIGHT") != "LEFT_TO_RIGHT"
    ):
        _paragraph_style_diagnostics(style, checks)
        return False
    runs = paragraph.get("elements")
    checks["header_recovery_empty_reason"] = "run_wrapper"
    if not isinstance(runs, list) or len(runs) != 1 or not _terminal_wrapper(runs[0], "textRun"):
        return False
    wrapper = runs[0]
    if not isinstance(wrapper, dict):
        return False
    run = wrapper.get("textRun")
    checks["header_recovery_empty_reason"] = "text_run"
    matched = (
        isinstance(run, dict)
        and set(run) <= {"content", "textStyle"}
        and run.get("content") == "\n"
        and run.get("textStyle", {}) == {}
    )
    if matched:
        checks["header_recovery_empty_reason"] = "empty"
    else:
        checks["header_recovery_run_mapping"] = isinstance(run, dict)
        if isinstance(run, dict):
            checks["header_recovery_run_unknown_fields"] = not set(run) <= {"content", "textStyle"}
            checks["header_recovery_run_content_lf"] = run.get("content") == "\n"
            checks["header_recovery_run_style_present"] = "textStyle" in run
            checks["header_recovery_run_style_empty"] = run.get("textStyle", {}) == {}
    return matched


def _paragraph_style_diagnostics(value: object, checks: dict[str, bool | str]) -> None:
    checks["header_recovery_paragraph_mapping"] = isinstance(value, dict)
    if not isinstance(value, dict):
        return
    dimensions = ("spaceAbove", "spaceBelow", "indentStart", "indentEnd", "indentFirstLine")
    flags = ("keepWithNext", "keepTogether", "avoidWidowAndOrphan", "pageBreakBefore")
    checks["header_recovery_paragraph_unknown_fields"] = not set(value) <= {
        "namedStyleType",
        "alignment",
        "direction",
        "lineSpacing",
        *dimensions,
        *flags,
    }
    for field, expected in (("namedStyleType", "NORMAL_TEXT"), ("alignment", "START")):
        checks[f"header_recovery_paragraph_{field}_present"] = field in value
        checks[f"header_recovery_paragraph_{field}_default"] = value.get(field) == expected
    for field in dimensions:
        dimension = value.get(field)
        checks[f"header_recovery_paragraph_{field}_present"] = field in value
        checks[f"header_recovery_paragraph_{field}_unit_only"] = dimension == {"unit": "PT"}
        checks[f"header_recovery_paragraph_{field}_explicit_zero"] = (
            isinstance(dimension, dict)
            and set(dimension) == {"unit", "magnitude"}
            and dimension.get("unit") == "PT"
            and type(dimension.get("magnitude")) in {int, float}
            and dimension.get("magnitude") == 0
        )
    for field in flags:
        checks[f"header_recovery_paragraph_{field}_present"] = field in value
        checks[f"header_recovery_paragraph_{field}_false"] = value.get(field) is False
    checks["header_recovery_paragraph_direction_present"] = "direction" in value
    checks["header_recovery_paragraph_direction_ltr"] = value.get("direction") == "LEFT_TO_RIGHT"
    checks["header_recovery_paragraph_direction_rtl"] = value.get("direction") == "RIGHT_TO_LEFT"
    spacing = value.get("lineSpacing")
    checks["header_recovery_paragraph_lineSpacing_present"] = "lineSpacing" in value
    checks["header_recovery_paragraph_lineSpacing_zero"] = (
        type(spacing) in {int, float} and spacing == 0
    )
    checks["header_recovery_paragraph_lineSpacing_hundred"] = (
        type(spacing) in {int, float} and spacing == _SINGLE_LINE_SPACING_PERCENT
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
