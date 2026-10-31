from dataclasses import asdict
import json

from google.genai import errors
import pytest

from app.document_transform.model_diagnostics import model_failure_details


@pytest.mark.parametrize("reason", ["SERVICE_DISABLED", "PRIVATE RESPONSE"])
def test_provider_diagnostics_never_include_messages_or_metadata(reason: str) -> None:
    error = errors.ClientError(
        403,
        {
            "error": {
                "message": "PRIVATE PROMPT",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": reason,
                        "metadata": {"token": "PRIVATE TOKEN"},
                    }
                ],
            }
        },
    )
    failure = model_failure_details(error)
    assert failure.http_status == 403
    assert failure.kind == "api_error"
    assert failure.reason == (reason if reason == "SERVICE_DISABLED" else "unknown")
    assert "PRIVATE" not in json.dumps(asdict(failure))


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(TimeoutError("PRIVATE PROMPT"), id="timeout"),
        pytest.param(ValueError("PRIVATE RESPONSE"), id="invalid-payload"),
    ],
)
def test_transport_and_schema_failure_details_are_private(error: TimeoutError | ValueError) -> None:
    failure = model_failure_details(error)
    assert failure.http_status == 0
    assert failure.reason == "unknown"
    assert "PRIVATE" not in json.dumps(asdict(failure))
