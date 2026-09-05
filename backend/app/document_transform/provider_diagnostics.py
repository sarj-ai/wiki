# Bounded non-content diagnostics; never used to decide whether a write is safe.

from __future__ import annotations

import re
from typing import Any, NamedTuple

from googleapiclient.errors import HttpError
from pydantic import TypeAdapter


MAX_ERROR_BYTES = 16_384
MAX_DETAILS = 16
MAX_REQUEST_ORDINAL = 200
ERROR_JSON = TypeAdapter(dict[str, Any])
FIELD_PATH = re.compile(
    r"requests\[([0-9]{1,3})\]\.(insertDate|deleteContentRange|createNamedRange)"
    r"(?:\.(dateElementProperties|location|range)(?:\.([A-Za-z]+))?)?"
)
MESSAGE_PREFIX = re.compile(
    r"\AInvalid requests\[([0-9]{1,3})\]\.(insertDate|deleteContentRange|createNamedRange):"
)


class FieldEvidence(NamedTuple):
    ordinal: int
    operation: str
    category: str


def native_provider_diagnostics(
    error: Exception, requests: list[dict[str, Any]]
) -> dict[str, str | int]:
    unknown: dict[str, str | int] = {"provider_category": "unknown"}
    cause = error.__cause__
    if not isinstance(cause, HttpError):
        return unknown
    if len(cause.content) > MAX_ERROR_BYTES:
        return unknown
    try:
        payload = ERROR_JSON.validate_json(cause.content)
    except ValueError, UnicodeError, RecursionError:
        return unknown
    if not isinstance(payload.get("error"), dict):
        return unknown
    body = payload["error"]
    evidence = _field_evidence(body)
    if evidence is None:
        return unknown
    message = body.get("message")
    prefix = MESSAGE_PREFIX.match(message) if isinstance(message, str) else None
    if prefix is not None:
        evidence.append(FieldEvidence(int(prefix[1]), prefix[2], "unknown"))
    if not evidence:
        return unknown
    identities = {(ordinal, operation) for ordinal, operation, _ in evidence}
    categories = {category for _, _, category in evidence if category != "unknown"}
    if len(identities) != 1 or len(categories) > 1:
        return unknown
    ordinal, operation = next(iter(identities))
    if (
        ordinal > MAX_REQUEST_ORDINAL
        or ordinal >= len(requests)
        or set(requests[ordinal]) != {operation}
    ):
        return unknown
    return {
        "provider_category": next(iter(categories), "unknown"),
        "provider_request_ordinal": ordinal,
        "provider_operation": next(iter(requests[ordinal])),
    }


def _field_evidence(body: dict[str, Any]) -> list[FieldEvidence] | None:
    details = body.get("details", [])
    if not isinstance(details, list) or len(details) > MAX_DETAILS:
        return None
    evidence: list[FieldEvidence] = []
    for detail in details:
        if not isinstance(detail, dict):
            return None
        if detail.get("@type") != "type.googleapis.com/google.rpc.BadRequest":
            continue
        violations = detail.get("fieldViolations", [])
        if not isinstance(violations, list) or len(violations) > MAX_DETAILS:
            return None
        for violation in violations:
            if not isinstance(violation, dict) or not isinstance(violation.get("field"), str):
                return None
            match = FIELD_PATH.fullmatch(violation["field"])
            if match is None:
                return None
            ordinal, operation, field, leaf = match.groups()
            category = _field_category(operation, field, leaf)
            if category is None:
                return None
            evidence.append(FieldEvidence(int(ordinal), operation, category))
    return evidence


def _field_category(operation: str, field: str | None, leaf: str | None) -> str | None:
    if field is None:
        return "unknown"
    if operation == "insertDate" and field == "dateElementProperties":
        return (
            "date_properties"
            if leaf in {None, "timestamp", "timeZoneId", "locale", "dateFormat", "timeFormat"}
            else None
        )
    if operation == "insertDate" and field == "location":
        return "date_location" if leaf in {None, "index", "tabId", "segmentId"} else None
    if field == "range" and leaf in {None, "startIndex", "endIndex", "tabId", "segmentId"}:
        return {"deleteContentRange": "delete_range", "createNamedRange": "marker_range"}.get(
            operation
        )
    return None
