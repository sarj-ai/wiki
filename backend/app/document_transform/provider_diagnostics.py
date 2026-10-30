# Bounded non-content diagnostics; never used to decide whether a write is safe.

from __future__ import annotations

import re
from types import MappingProxyType
from typing import Any, NamedTuple

from googleapiclient.errors import HttpError
from pydantic import RootModel


MAX_ERROR_BYTES = 16_384
MAX_DETAILS = 16
MAX_REQUEST_ORDINAL = 200
MAX_TOPIC_CHARS = 2_048
MESSAGE_TOPICS = MappingProxyType(
    {
        "locale": re.compile(
            r"\A(?:invalid|unknown|unrecognized|unsupported) locale\b", re.IGNORECASE
        ),
        "date_format": re.compile(
            r"\A(?:invalid|unknown|unrecognized|unsupported) (?:date format|dateFormat)\b",
            re.IGNORECASE,
        ),
        "time_format": re.compile(
            r"\A(?:invalid|unknown|unrecognized|unsupported) (?:time format|timeFormat)\b",
            re.IGNORECASE,
        ),
        "time_zone": re.compile(
            r"\A(?:invalid|unknown|unrecognized|unsupported) (?:time zone|timezone|timeZoneId)\b",
            re.IGNORECASE,
        ),
        "timestamp": re.compile(
            r"\A(?:invalid|missing|malformed|out of range) (?:timestamp|date timestamp)\b",
            re.IGNORECASE,
        ),
        "location": re.compile(
            r"\A(?:(?:invalid|missing|out of bounds) (?:location|insertion index|index)|(?:the )?insertion index (?:must|cannot|is invalid))\b",
            re.IGNORECASE,
        ),
        "capability": re.compile(
            r"\A(?:(?:this operation|insertDate|date insertion|inserting dates) (?:is|are) not supported|unsupported (?:operation|request|insertDate))\b",
            re.IGNORECASE,
        ),
        "access": re.compile(
            r"\A(?:permission denied|access denied|insufficient permissions|the caller does not have permission)\b",
            re.IGNORECASE,
        ),
    }
)


class _ProviderErrorPayload(RootModel[dict[str, Any]]):
    """Validate the bounded provider error object used for diagnostic projection."""


PROPERTY_TOPIC_PATTERNS = MappingProxyType(
    {
        topic: re.compile(
            rf"\A(?:(?:the )?{noun} (?:is (?:invalid|missing|required|unsupported)|must (?:be|match))|must (?:specify|provide) (?:a |an |the )?(?:valid )?{noun})\b",
            re.IGNORECASE,
        )
        for topic, noun in {
            "locale": r"locale",
            "date_format": r"(?:date format|dateFormat)",
            "time_format": r"(?:time format|timeFormat)",
            "time_zone": r"(?:time zone|timezone|timeZoneId)",
            "timestamp": r"(?:timestamp|date timestamp)",
            "location": r"(?:location|insertion index|index)",
        }.items()
    }
)
FIELD_PATH = re.compile(
    r"requests\[([0-9]{1,3})\]\.(insertDate|deleteContentRange|createNamedRange|insert_date|delete_content_range|create_named_range)"
    r"(?:\.(dateElementProperties|date_element_properties|location|range)(?:\.([A-Za-z_]+))?)?"
)
_FIELD_ALIASES = MappingProxyType(
    {
        "insert_date": "insertDate",
        "delete_content_range": "deleteContentRange",
        "create_named_range": "createNamedRange",
        "date_element_properties": "dateElementProperties",
        "time_zone_id": "timeZoneId",
        "date_format": "dateFormat",
        "time_format": "timeFormat",
        "tab_id": "tabId",
        "segment_id": "segmentId",
        "start_index": "startIndex",
        "end_index": "endIndex",
    }
)
_STATUSES = frozenset(
    {"INVALID_ARGUMENT", "FAILED_PRECONDITION", "UNIMPLEMENTED", "PERMISSION_DENIED"}
)
MESSAGE_PREFIX = re.compile(
    r"\AInvalid requests\[([0-9]{1,3})\]\.(insertDate|deleteContentRange|createNamedRange):"
)


class FieldEvidence(NamedTuple):
    ordinal: int
    operation: str
    category: str
    leaf: str | None = None


class MessageEvidence(NamedTuple):
    topic: str
    outcome: str


def native_provider_diagnostics(
    error: Exception, requests: list[dict[str, Any]], *, include_envelope: bool = False
) -> dict[str, str | int]:
    result = _request_diagnostics(error, requests)
    return result | _envelope_diagnostics(error, result) if include_envelope else result


def _envelope_diagnostics(error: Exception, request: dict[str, str | int]) -> dict[str, str | int]:
    cause = error.__cause__
    if not isinstance(cause, HttpError) or len(cause.content) > MAX_ERROR_BYTES:
        return {}
    try:
        payload = _ProviderErrorPayload.model_validate_json(cause.content).root
    except ValueError, UnicodeError, RecursionError:
        return {}
    body = payload.get("error")
    if not isinstance(body, dict):
        return {}
    status = body.get("status")
    result: dict[str, str | int] = {
        "provider_status": status if isinstance(status, str) and status in _STATUSES else "unknown"
    }
    details = body.get("details", [])
    if (
        not isinstance(details, list)
        or len(details) > MAX_DETAILS
        or any(not isinstance(item, dict) for item in details)
    ):
        return result | {
            "provider_detail_shape": "malformed",
            "provider_field_evidence": "unrecognized",
        }
    kinds = {kind for item in details if isinstance((kind := item.get("@type")), str)}
    known = kinds & {
        "type.googleapis.com/google.rpc.BadRequest",
        "type.googleapis.com/google.rpc.ErrorInfo",
    }
    result["provider_detail_shape"] = (
        "absent"
        if not details
        else "mixed"
        if len(known) > 1
        else "bad_request"
        if "type.googleapis.com/google.rpc.BadRequest" in known
        else "error_info"
        if "type.googleapis.com/google.rpc.ErrorInfo" in known
        else "other"
    )
    evidence = _field_evidence(body)
    result["provider_field_evidence"] = (
        "unrecognized"
        if evidence is None
        else "none"
        if not evidence
        else "recognized"
        if "provider_request_ordinal" in request
        else "conflicting"
    )
    return result


def _request_diagnostics(error: Exception, requests: list[dict[str, Any]]) -> dict[str, str | int]:
    unknown: dict[str, str | int] = {"provider_category": "unknown"}
    cause = error.__cause__
    if not isinstance(cause, HttpError):
        return unknown
    if len(cause.content) > MAX_ERROR_BYTES:
        return unknown
    try:
        payload = _ProviderErrorPayload.model_validate_json(cause.content).root
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
    identities = {(item.ordinal, item.operation) for item in evidence}
    categories = {item.category for item in evidence if item.category != "unknown"}
    leaves = {item.leaf for item in evidence if item.leaf is not None}
    if len(identities) != 1 or len(categories) > 1 or len(leaves) > 1:
        return unknown
    ordinal, operation = next(iter(identities))
    if (
        ordinal > MAX_REQUEST_ORDINAL
        or ordinal >= len(requests)
        or set(requests[ordinal]) != {operation}
    ):
        return unknown
    result: dict[str, str | int] = {
        "provider_category": next(iter(categories), "unknown"),
        "provider_request_ordinal": ordinal,
        "provider_operation": next(iter(requests[ordinal])),
    }
    if leaves:
        result["provider_field"] = next(iter(leaves))
    if operation == "insertDate" and prefix is not None and isinstance(message, str):
        message_evidence = _message_topic(message[prefix.end() :])
        result["provider_message_topic"] = message_evidence.topic
        result["provider_message_outcome"] = message_evidence.outcome
    return result


def _message_topic(tail: str) -> MessageEvidence:
    # Diagnostic vocabulary only, never policy. Do not inspect quoted values,
    # interpolate matched text, or log a message digest. Multiple leading
    # clauses are recognized only when none contains a quoted provider value.
    if len(tail) > MAX_TOPIC_CHARS:
        return MessageEvidence("unknown", "oversized")
    lead = re.split(r"[\"'`\u2018\u2019\u201c\u201d]", tail, maxsplit=1)[0]
    if not lead.strip() and tail.strip():
        return MessageEvidence("unknown", "quoted_lead")
    patterns = (*MESSAGE_TOPICS.items(), *PROPERTY_TOPIC_PATTERNS.items())
    if not any(pattern.match(lead.split(";", 1)[0].strip()) for _, pattern in patterns):
        return MessageEvidence("unknown", "unrecognized_lead")
    topics = {
        topic
        for clause in lead.split(";")
        for topic, pattern in patterns
        if pattern.match(clause.strip())
    }
    if len(topics) > 1:
        return MessageEvidence("ambiguous", "ambiguous")
    return MessageEvidence(next(iter(topics)), "matched")


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
            operation = _FIELD_ALIASES.get(operation, operation)
            field = _FIELD_ALIASES.get(field, field) if field is not None else None
            leaf = _FIELD_ALIASES.get(leaf, leaf) if leaf is not None else None
            category = _field_category(operation, field, leaf)
            if category is None:
                return None
            evidence.append(FieldEvidence(int(ordinal), operation, category, leaf))
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
