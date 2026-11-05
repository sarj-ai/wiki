from dataclasses import asdict
import json

from google.genai import errors
from loguru import logger
from pydantic import ValidationError
import pytest

from app.document_transform.model_diagnostics import (
    ModelBindingError,
    ModelCoverageError,
    ModelEmptyResponseError,
    log_model_failure,
    model_failure_details,
)
from app.document_transform.schemas import PatchEnvelope


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


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (ModelEmptyResponseError("PRIVATE RESPONSE"), "empty"),
        (ModelCoverageError("PRIVATE BLOCK ID"), "coverage"),
        (ModelBindingError("PRIVATE HASH"), "binding"),
        (ValueError("PRIVATE VALUE"), "unknown"),
    ],
)
def test_typed_payload_categories_never_log_private_exception_values(
    error: ValueError, category: str
) -> None:
    captured: list[str] = []
    sink = logger.add(captured.append, serialize=True)
    try:
        log_model_failure(
            operation="review", attempt=1, model="gemini-3.1-pro-preview", error=error
        )
    finally:
        logger.remove(sink)
    failure = model_failure_details(error)
    assert failure.kind == "invalid_payload"
    assert failure.payload_category == category
    assert not failure.validation_issues
    assert "PRIVATE" not in json.dumps(asdict(failure))
    assert "PRIVATE" not in "".join(captured)
    record = json.loads(captured[-1])["record"]["extra"]
    assert record["model_id"] == "gemini-3.1-pro-preview"
    assert record["model_payload_category"] == category


def test_schema_error_locations_and_types_are_bounded_and_private() -> None:
    value = {
        "patches": [
            {
                "block_id": "PRIVATE SOURCE",
                "expected_sha256": "PRIVATE HASH",
                "replacement": 123,
                "PRIVATE FIELD": "PRIVATE TOKEN",
            }
            for _ in range(8)
        ]
    }
    with pytest.raises(ValidationError) as caught:
        PatchEnvelope.model_validate(value)
    failure = model_failure_details(caught.value)
    assert failure.payload_category == "schema"
    assert len(failure.validation_issues) == 5
    assert all(len(issue.location) <= 4 for issue in failure.validation_issues)
    assert all(
        issue.error_type in {"string_pattern_mismatch", "string_type", "extra_forbidden"}
        for issue in failure.validation_issues
    )
    assert any("unknown" in issue.location for issue in failure.validation_issues)
    captured: list[str] = []
    sink = logger.add(captured.append, serialize=True)
    try:
        log_model_failure(
            operation="review", attempt=1, model="gemini-3.1-pro-preview", error=caught.value
        )
    finally:
        logger.remove(sink)
    assert "PRIVATE" not in json.dumps(asdict(failure))
    assert "PRIVATE" not in "".join(captured)
