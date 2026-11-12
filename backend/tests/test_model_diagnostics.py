from dataclasses import asdict
import json
from typing import Literal

from google.auth import exceptions as auth_errors
from google.genai import errors, types
import httpx
from loguru import logger
from pydantic import ValidationError
import pytest

from app.document_transform.errors import ModelUnavailableError
from app.document_transform.model_diagnostics import (
    ModelBindingError,
    ModelCoverageError,
    ModelEmptyResponseError,
    log_model_failure,
    log_model_response_failure,
    model_exception_chain,
    model_failure_details,
)
from app.document_transform.model_failures import exhausted_model_failure
from app.document_transform.schemas import PatchEnvelope, TransformPhase


def test_provider_deadline_retains_safe_exception_chain_and_canonical_status() -> None:
    provider = errors.ServerError(
        504, {"error": {"status": "DEADLINE_EXCEEDED", "message": "PRIVATE RESPONSE"}}
    )
    wrapped = ModelUnavailableError()
    wrapped.__cause__ = provider
    captured: list[str] = []
    sink = logger.add(captured.append, serialize=True)
    try:
        log_model_failure(
            operation="review", attempt=1, model="gemini-3.1-pro-preview", error=wrapped
        )
    finally:
        logger.remove(sink)
    record = json.loads(captured[-1])["record"]["extra"]
    assert record["model_exception_chain"] == ["ModelUnavailableError", "ServerError"]
    assert record["model_http_status"] == 504
    assert record["model_error_reason"] == "DEADLINE_EXCEEDED"
    assert "PRIVATE" not in "".join(captured)


def test_exception_chain_is_bounded_cycle_safe_and_excludes_untrusted_class_names() -> None:
    private = type("PRIVATE_CREDENTIAL", (ValueError,), {})("PRIVATE CONTENT")
    private.__cause__ = private
    assert model_exception_chain(private) == ("Exception",)
    group = ExceptionGroup("PRIVATE GROUP", [ValueError("PRIVATE VALUE") for _ in range(100)])
    chain = model_exception_chain(group)
    assert len(chain) == 8
    assert chain[0] == "ExceptionGroup"
    assert "PRIVATE" not in json.dumps(chain)


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
    ("error", "kind"),
    [
        (httpx.ConnectError("PRIVATE TRANSPORT RESPONSE"), "transport"),
        (auth_errors.RefreshError("PRIVATE CREDENTIALS"), "credentials"),
    ],
)
def test_wrapped_transport_and_credentials_keep_their_category_without_private_values(
    error: Exception, kind: str
) -> None:
    wrapped = exhausted_model_failure(error)
    wrapped.__cause__ = error
    failure = model_failure_details(wrapped)
    assert failure.kind == kind
    assert failure.http_status == 0
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


@pytest.mark.parametrize("finish_reason", [types.FinishReason.STOP, types.FinishReason.MAX_TOKENS])
@pytest.mark.parametrize("count", [0, 1_000_000])
@pytest.mark.parametrize("operation", ["review", TransformPhase.UNSLOP, TransformPhase.FORMAT])
def test_response_failure_metadata_logs_only_bounded_counts_and_finite_reason(
    finish_reason: types.FinishReason,
    count: int,
    operation: TransformPhase | Literal["review"],
) -> None:
    response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                finish_reason=finish_reason,
                finish_message="PRIVATE REASON",
                content=types.Content(parts=[types.Part(text="PRIVATE RESPONSE")]),
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=count,
            candidates_token_count=count,
            thoughts_token_count=count,
            total_token_count=count,
        ),
        response_id="PRIVATE ID",
        model_version="PRIVATE VERSION",
    )
    captured: list[str] = []
    sink = logger.add(captured.append, serialize=True)
    try:
        log_model_response_failure(model="gemini-3.8-flash", response=response, operation=operation)
    finally:
        logger.remove(sink)
    extra = {
        key: value
        for key, value in json.loads(captured[-1])["record"]["extra"].items()
        if key.startswith("model_")
    }
    assert extra == {
        "model_operation": operation,
        "model_id": "gemini-3.8-flash",
        "model_finish_reason": finish_reason,
        "model_prompt_token_count": count,
        "model_candidate_token_count": count,
        "model_thought_token_count": count,
        "model_total_token_count": count,
    }
    assert "PRIVATE" not in "".join(captured)


@pytest.mark.parametrize("count", [None, True, -1, 1_000_001, "PRIVATE"])
def test_response_failure_metadata_omits_malformed_values_without_masking_failure(
    count: int | str | None,
) -> None:
    response = types.GenerateContentResponse.model_construct(
        candidates=[types.Candidate.model_construct(finish_reason="PRIVATE REASON")],
        usage_metadata=types.GenerateContentResponseUsageMetadata.model_construct(
            prompt_token_count=count,
            candidates_token_count=count,
            thoughts_token_count=count,
            total_token_count=count,
        ),
    )
    captured: list[str] = []
    sink = logger.add(captured.append, serialize=True)
    try:
        log_model_response_failure(model="gemini-3.8-flash", response=response)
    finally:
        logger.remove(sink)
    extra = {
        key: value
        for key, value in json.loads(captured[-1])["record"]["extra"].items()
        if key.startswith("model_")
    }
    assert extra == {
        "model_operation": "review",
        "model_id": "gemini-3.8-flash",
        "model_finish_reason": "unknown",
    }
    assert "PRIVATE" not in "".join(captured)
