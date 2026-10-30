import json
from typing import Any

from googleapiclient.errors import HttpError
from httplib2 import Response
import pytest

from app.document_transform.errors import DocumentGatewayError
from app.document_transform.provider_diagnostics import native_provider_diagnostics


def _diagnostics(body: object) -> dict[str, str | int]:
    error = DocumentGatewayError(http_status=400)
    error.__cause__ = HttpError(
        Response({"status": "400"}), json.dumps({"error": body}).encode(), uri="SECRET"
    )
    requests: list[dict[str, Any]] = [
        {"createNamedRange": {}},
        {"deleteContentRange": {}},
        {"insertDate": {}},
    ]
    return native_provider_diagnostics(error, requests)


@pytest.mark.parametrize(
    ("field", "ordinal", "operation", "category"),
    [
        ("requests[0].createNamedRange.range.startIndex", 0, "createNamedRange", "marker_range"),
        ("requests[1].deleteContentRange.range", 1, "deleteContentRange", "delete_range"),
        (
            "requests[2].insertDate.dateElementProperties.timestamp",
            2,
            "insertDate",
            "date_properties",
        ),
        ("requests[2].insertDate.location.index", 2, "insertDate", "date_location"),
    ],
)
def test_structured_fields_emit_only_finite_diagnostics(
    field: str, ordinal: int, operation: str, category: str
) -> None:
    result = _diagnostics(
        {
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.BadRequest",
                    "fieldViolations": [{"field": field, "description": "SECRET"}],
                }
            ]
        }
    )
    expected: dict[str, str | int] = {
        "provider_category": category,
        "provider_request_ordinal": ordinal,
        "provider_operation": operation,
    }
    if field.count(".") == 3:
        expected["provider_field"] = field.rsplit(".", 1)[1]
    assert result == expected
    assert "SECRET" not in json.dumps(result)


def test_prefix_discards_all_provider_description() -> None:
    result = _diagnostics({"message": "Invalid requests[2].insertDate: SECRET title URI token"})
    assert result == {
        "provider_category": "unknown",
        "provider_request_ordinal": 2,
        "provider_operation": "insertDate",
        "provider_message_topic": "unknown",
        "provider_message_outcome": "unrecognized_lead",
    }


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {"details": "SECRET"},
        {"details": [None]},
        {"message": "SECRET Invalid requests[2].insertDate:"},
        {"message": "Invalid requests[999].insertDate:"},
        {"message": "Invalid requests[1].insertDate:"},
        {"message": "Invalid requests[2].SECRET:"},
        {"message": "X" * 17000},
    ],
    ids=[
        "null",
        "array",
        "bad-details",
        "bad-entry",
        "unanchored",
        "out-of-range",
        "wrong-operation",
        "unknown-operation",
        "oversized",
    ],
)
def test_unknown_malformed_or_mismatched_evidence_is_unknown(body: object) -> None:
    assert _diagnostics(body) == {"provider_category": "unknown"}


def test_conflicting_prefix_and_field_are_unknown() -> None:
    assert _diagnostics(
        {
            "message": "Invalid requests[2].insertDate: SECRET",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.BadRequest",
                    "fieldViolations": [{"field": "requests[1].deleteContentRange.range"}],
                }
            ],
        }
    ) == {"provider_category": "unknown"}


def test_no_provider_cause_is_unknown() -> None:
    assert native_provider_diagnostics(DocumentGatewayError(), []) == {
        "provider_category": "unknown"
    }


@pytest.mark.parametrize("leaf", ["timestamp", "timeZoneId", "locale", "dateFormat", "timeFormat"])
def test_date_property_leaf_is_finite_and_bound_to_actual_operation(leaf: str) -> None:
    result = _diagnostics(
        {
            "message": "Invalid requests[2].insertDate: SECRET",
            "status": {"SECRET": "SECRET"},
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.BadRequest",
                    "fieldViolations": [
                        {"field": f"requests[2].insertDate.dateElementProperties.{leaf}"}
                    ],
                }
            ],
        }
    )
    assert result["provider_field"] == leaf
    assert "SECRET" not in json.dumps(result)


@pytest.mark.parametrize(
    "fields",
    [
        [
            "requests[2].insertDate.dateElementProperties.locale",
            "requests[2].insertDate.dateElementProperties.dateFormat",
        ],
        ["requests[1].insertDate.dateElementProperties.locale"],
        ["requests[201].insertDate.dateElementProperties.locale"],
        ["requests[2].insertDate.location.locale"],
        ["requests[2].insertDate.dateElementProperties.SECRET"],
    ],
    ids=["conflicting-leaves", "wrong-ordinal", "ordinal-budget", "wrong-parent", "unknown-leaf"],
)
def test_conflicting_or_unvalidated_leaf_never_escapes(fields: list[str]) -> None:
    assert _diagnostics(
        {
            "message": "Invalid requests[2].insertDate: Invalid locale SECRET",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.BadRequest",
                    "fieldViolations": [{"field": field} for field in fields],
                }
            ],
        }
    ) == {"provider_category": "unknown"}


@pytest.mark.parametrize(
    ("tail", "topic"),
    [
        ("Invalid time zone 'SECRET permission denied'", "time_zone"),
        ("Invalid timestamp: SECRET", "timestamp"),
        ("Invalid locale: SECRET", "locale"),
        ("Unsupported date format: SECRET", "date_format"),
        ("Unknown dateFormat: SECRET", "date_format"),
        ("Invalid timeFormat: SECRET", "time_format"),
        ("Unrecognized time format: SECRET", "time_format"),
        ("Invalid locale; Invalid date format", "ambiguous"),
        ("Invalid locale 'SECRET'; Invalid time format", "locale"),
        ('"Invalid locale"', "unknown"),
        ("Invalid value ‘Unsupported date format’", "unknown"),
        ("Invalid localeExtra SECRET", "unknown"),
        ("Invalid locale" + "x" * 3000, "unknown"),
        ("The insertion index must be inside a paragraph", "location"),
        ("This operation is not supported for this document", "capability"),
        ("Permission denied: SECRET", "access"),
        ("Invalid timestamp; Invalid location", "ambiguous"),
        ("Bad request", "unknown"),
        ("SECRET; Permission denied", "unknown"),
        ('"Permission denied"', "unknown"),
        ("Invalid value 'Invalid timestamp; Permission denied'", "unknown"),
        ("Invalid time zone 'SECRET'; Permission denied", "time_zone"),
        ("Invalid timestamp" + "x" * 3000, "unknown"),
    ],
)
def test_message_topics_are_bounded_hints_without_provider_values(tail: str, topic: str) -> None:
    result = _diagnostics({"message": f"Invalid requests[2].insertDate: {tail}"})
    assert result["provider_message_topic"] == topic
    assert "SECRET" not in json.dumps(result)


@pytest.mark.parametrize(
    ("noun", "topic"),
    [
        ("locale", "locale"),
        ("dateFormat", "date_format"),
        ("time format", "time_format"),
        ("timeZoneId", "time_zone"),
        ("timestamp", "timestamp"),
        ("insertion index", "location"),
    ],
)
@pytest.mark.parametrize(
    "template",
    [
        "The {} is invalid: SECRET",
        "{} must be valid: SECRET",
        "Must specify a valid {}: SECRET",
        "Must provide the {}: SECRET",
    ],
)
def test_noun_first_and_required_property_templates_emit_static_topics(
    noun: str, topic: str, template: str
) -> None:
    result = _diagnostics({"message": f"Invalid requests[2].insertDate: {template.format(noun)}"})
    assert result["provider_message_topic"] == topic
    assert result["provider_message_outcome"] == "matched"
    assert "SECRET" not in json.dumps(result)


@pytest.mark.parametrize(
    ("tail", "topic", "outcome"),
    [
        ("The locale is invalid; Must specify timestamp", "ambiguous", "ambiguous"),
        ("The locale is invalid 'SECRET'; Must specify timestamp", "locale", "matched"),
        ('"Must specify timestamp SECRET"', "unknown", "quoted_lead"),
        ("  ‘The locale is invalid SECRET’", "unknown", "quoted_lead"),
        ("`SECRET` The locale is invalid", "unknown", "quoted_lead"),
        ("Invalid value 'The locale is invalid SECRET'", "unknown", "unrecognized_lead"),
        ("SECRET; The locale is invalid", "unknown", "unrecognized_lead"),
        ("The locale is invalidated", "unknown", "unrecognized_lead"),
        ("Must specify a valid localeExtra", "unknown", "unrecognized_lead"),
        ("Must specify a valid SECRET", "unknown", "unrecognized_lead"),
        ("Bad request", "unknown", "unrecognized_lead"),
        ("", "unknown", "unrecognized_lead"),
        ("The locale is invalid " + "x" * 2048, "unknown", "oversized"),
        ('"' + "x" * 2048, "unknown", "oversized"),
    ],
)
def test_classifier_outcomes_never_inspect_quoted_values_or_unbounded_tails(
    tail: str, topic: str, outcome: str
) -> None:
    result = _diagnostics(
        {"message": f"Invalid requests[2].insertDate: {tail}", "status": "SECRET"}
    )
    assert result["provider_message_topic"] == topic
    assert result["provider_message_outcome"] == outcome
    assert "SECRET" not in json.dumps(result)


@pytest.mark.parametrize(
    "prefix",
    [
        "Invalid requests[1].insertDate:",
        "Invalid requests[201].insertDate:",
        "SECRET Invalid requests[2].insertDate:",
        "Invalid requests[1].deleteContentRange:",
    ],
)
def test_message_topics_require_exact_validated_insert_date_prefix(prefix: str) -> None:
    assert "provider_message_topic" not in _diagnostics({"message": f"{prefix} Permission denied"})


@pytest.mark.parametrize(
    "prefix",
    [
        "Invalid requests[1].insertDate:",
        "Invalid requests[201].insertDate:",
        "SECRET Invalid requests[2].insertDate:",
        "Invalid requests[1].deleteContentRange:",
    ],
)
def test_classifier_outcome_requires_validated_actual_insert_date(prefix: str) -> None:
    result = _diagnostics({"message": f"{prefix} Must specify a valid locale"})
    assert "provider_message_outcome" not in result
    assert "provider_message_topic" not in result


@pytest.mark.parametrize(
    "field",
    [
        "requests[2].insertDate.location.timestamp",
        "requests[2].insertDate.dateElementProperties.tabId",
        "requests[2].insertDate.range",
        "requests[0].createNamedRange.location",
    ],
)
def test_impossible_field_combinations_override_valid_prefix(field: str) -> None:
    assert _diagnostics(
        {
            "message": "Invalid requests[2].insertDate: SECRET",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.BadRequest",
                    "fieldViolations": [{"field": field}],
                }
            ],
        }
    ) == {"provider_category": "unknown"}


@pytest.mark.parametrize("ordinal", [200, 201])
def test_request_ordinal_is_bounded(ordinal: int) -> None:
    error = DocumentGatewayError()
    error.__cause__ = HttpError(
        Response({"status": "400"}),
        json.dumps(
            {"error": {"message": f"Invalid requests[{ordinal}].insertDate: SECRET"}}
        ).encode(),
    )
    result = native_provider_diagnostics(error, [{"insertDate": {}} for _ in range(202)])
    assert result.get("provider_request_ordinal") == (ordinal if ordinal == 200 else None)


@pytest.mark.parametrize("content", [b"not JSON SECRET", b"\xff", b"[" * 2000])
def test_invalid_json_is_unknown_without_raising(content: bytes) -> None:
    error = DocumentGatewayError(http_status=400, retryable=False)
    error.__cause__ = HttpError(Response({"status": "400"}), content)
    assert native_provider_diagnostics(error, []) == {"provider_category": "unknown"}
    assert error.http_status == 400
    assert error.retryable is False
