import json
from typing import Any

from googleapiclient.errors import HttpError
from httplib2 import Response
import pytest

from app.document_transform.errors import DocumentGatewayError
from app.document_transform.provider_diagnostics import native_provider_diagnostics


def _diagnostics(body: object) -> dict[str, str | int]:
    error = DocumentGatewayError(http_status=400)
    error.__cause__ = HttpError(Response({"status": "400"}), json.dumps({"error": body}).encode())
    requests: list[dict[str, Any]] = [{"insertDate": {}}]
    return native_provider_diagnostics(error, requests, include_envelope=True)


@pytest.mark.parametrize(
    "status", ["INVALID_ARGUMENT", "UNIMPLEMENTED", "FAILED_PRECONDITION", "PERMISSION_DENIED"]
)
def test_fixed_status_does_not_guess_capability(status: str):
    result = _diagnostics({"status": status, "message": "SECRET"})
    assert result == {
        "provider_category": "unknown",
        "provider_status": status,
        "provider_detail_shape": "absent",
        "provider_field_evidence": "none",
    }


@pytest.mark.parametrize(
    "status", [None, {}, [], "SECRET", 10**400], ids=["null", "object", "list", "text", "huge"]
)
def test_unknown_status_never_emits_provider_value(status: object):
    result = _diagnostics({"status": status})
    assert result["provider_status"] == "unknown"
    assert "SECRET" not in json.dumps(result)


@pytest.mark.parametrize(
    ("field", "outcome"),
    [
        ("requests[0].insert_date.date_element_properties.time_zone_id", "recognized"),
        ("requests[1].insert_date.location.tab_id", "conflicting"),
        ("requests[0].delete_content_range.range.start_index", "conflicting"),
        ("requests[0].insert_date.date_element_properties.SECRET", "unrecognized"),
    ],
)
def test_field_aliases_require_exact_actual_operation(field: str, outcome: str):
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
    assert result["provider_detail_shape"] == "bad_request"
    assert result["provider_field_evidence"] == outcome
    assert "SECRET" not in json.dumps(result)
    if outcome == "recognized":
        assert result["provider_field"] == "timeZoneId"
        assert result["provider_operation"] == "insertDate"
    else:
        assert "provider_request_ordinal" not in result


@pytest.mark.parametrize(
    "details", [None, {}, [None], [{}] * 17], ids=["null", "object", "bad_item", "over_bound"]
)
def test_malformed_details_are_bounded(details: object):
    result = _diagnostics({"details": details})
    assert result["provider_detail_shape"] == "malformed"
    assert result["provider_field_evidence"] == "unrecognized"


def test_error_info_never_emits_reason_domain_or_metadata():
    result = _diagnostics(
        {
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "SECRET",
                    "domain": "SECRET",
                    "metadata": {"SECRET": "SECRET"},
                }
            ]
        }
    )
    assert result["provider_detail_shape"] == "error_info"
    assert result["provider_field_evidence"] == "none"
    assert "SECRET" not in json.dumps(result)
