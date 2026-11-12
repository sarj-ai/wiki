from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from google.auth import exceptions as auth_errors
from google.genai import errors as genai_errors, types as genai_types
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.document_transform.errors import ModelUnavailableError
from app.document_transform.model_failures import classify_model_failure
from app.document_transform.schemas import TransformPhase


_KNOWN_REASONS = frozenset(
    {
        "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
        "BILLING_DISABLED",
        "CONSUMER_INVALID",
        "IAM_PERMISSION_DENIED",
        "LOCATION_POLICY_VIOLATED",
        "MODEL_NOT_FOUND",
        "PERMISSION_DENIED",
        "RESOURCE_EXHAUSTED",
        "SECURITY_POLICY_VIOLATED",
        "SERVICE_DISABLED",
    }
)
_HTTP_STATUS_MIN = 100
_HTTP_STATUS_MAX = 599
_MAX_VALIDATION_ISSUES = 5
_MAX_VALIDATION_LOCATION_PARTS = 4
_MAX_MODEL_TOKEN_COUNT = 1_000_000
_SAFE_FINISH_REASONS = frozenset(
    {
        "FINISH_REASON_UNSPECIFIED",
        "STOP",
        "MAX_TOKENS",
        "SAFETY",
        "RECITATION",
        "LANGUAGE",
        "OTHER",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
        "MALFORMED_FUNCTION_CALL",
        "IMAGE_SAFETY",
        "UNEXPECTED_TOOL_CALL",
        "TOO_MANY_TOOL_CALLS",
        "IMAGE_PROHIBITED_CONTENT",
        "NO_IMAGE",
        "IMAGE_RECITATION",
        "IMAGE_OTHER",
        "CONTINUATION",
        "MALFORMED_RESPONSE",
    }
)
_SAFE_VALIDATION_FIELDS = frozenset(
    {
        "patches",
        "reviews",
        "block_id",
        "expected_sha256",
        "replacement_sha256",
        "replacement",
        "rationale",
        "equivalent",
        "reason",
    }
)
_SAFE_VALIDATION_TYPES = frozenset(
    {
        "missing",
        "extra_forbidden",
        "string_type",
        "string_too_short",
        "string_too_long",
        "string_pattern_mismatch",
        "bool_type",
        "bool_parsing",
        "list_type",
        "too_long",
        "literal_error",
        "json_invalid",
        "value_error",
        "model_type",
        "int_type",
    }
)


class ModelEmptyResponseError(ValueError):
    pass


class ModelCoverageError(ValueError):
    pass


class ModelBindingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ModelValidationIssue:
    error_type: str
    location: tuple[str | int, ...]


class _ProviderDetail(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    kind: str = Field(default="", alias="@type", max_length=160)
    reason: str | None = Field(default=None, max_length=80)


class _ProviderError(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    details: list[_ProviderDetail] = Field(default_factory=list, max_length=20)


class _ProviderResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    error: _ProviderError


@dataclass(frozen=True, slots=True)
class ModelFailureDetails:
    kind: Literal["api_error", "timeout", "invalid_payload", "credentials", "transport"]
    http_status: int
    reason: str
    payload_category: Literal["none", "unknown", "empty", "coverage", "binding", "schema"] = "none"
    validation_issues: tuple[ModelValidationIssue, ...] = ()


def model_failure_details(
    error: Exception,
) -> ModelFailureDetails:
    if isinstance(error, ModelUnavailableError) and isinstance(error.__cause__, Exception):
        return model_failure_details(error.__cause__)
    if isinstance(error, auth_errors.TransportError):
        return ModelFailureDetails("transport", 0, "unknown")
    if isinstance(error, auth_errors.GoogleAuthError):
        return ModelFailureDetails("credentials", 0, "unknown")
    if isinstance(error, genai_errors.APIError):
        status = error.code if _HTTP_STATUS_MIN <= error.code <= _HTTP_STATUS_MAX else 0
        return ModelFailureDetails("api_error", status, _provider_reason(error))
    if isinstance(error, TimeoutError):
        return ModelFailureDetails("timeout", 0, "unknown")
    if isinstance(error, ModelEmptyResponseError):
        return ModelFailureDetails("invalid_payload", 0, "unknown", "empty")
    if isinstance(error, ModelCoverageError):
        return ModelFailureDetails("invalid_payload", 0, "unknown", "coverage")
    if isinstance(error, ModelBindingError):
        return ModelFailureDetails("invalid_payload", 0, "unknown", "binding")
    if isinstance(error, ValidationError):
        return ModelFailureDetails(
            "invalid_payload", 0, "unknown", "schema", _safe_validation_issues(error)
        )
    failure = classify_model_failure(error)
    if failure is not None and failure.code == "model_unavailable":
        return ModelFailureDetails("transport", 0, "unknown")
    return ModelFailureDetails("invalid_payload", 0, "unknown", "unknown")


def _safe_validation_issues(error: ValidationError) -> tuple[ModelValidationIssue, ...]:
    issues: list[ModelValidationIssue] = []
    for issue in error.errors(include_url=False, include_context=False, include_input=False)[
        :_MAX_VALIDATION_ISSUES
    ]:
        error_type = issue["type"]
        issues.append(
            ModelValidationIssue(
                error_type if error_type in _SAFE_VALIDATION_TYPES else "unknown",
                tuple(
                    _safe_location_part(part)
                    for part in issue["loc"][:_MAX_VALIDATION_LOCATION_PARTS]
                ),
            )
        )
    return tuple(issues)


def _safe_location_part(part: str | int) -> str | int:
    if isinstance(part, int) or part in _SAFE_VALIDATION_FIELDS:
        return part
    return "unknown"


def _provider_reason(error: genai_errors.APIError) -> str:
    try:
        response = _ProviderResponse.model_validate(error.details)
    except ValidationError:
        return "unknown"
    return next(
        (
            detail.reason
            for detail in response.error.details
            if detail.kind == "type.googleapis.com/google.rpc.ErrorInfo"
            and detail.reason is not None
            and detail.reason in _KNOWN_REASONS
        ),
        "unknown",
    )


def log_model_failure(
    *,
    operation: TransformPhase | Literal["review", "readability"],
    attempt: int,
    model: str,
    error: Exception,
) -> None:
    failure = model_failure_details(error)
    logger.warning(
        "document model attempt failed operation={model_operation} attempt={model_attempt} "
        "kind={model_error_kind} http_status={model_http_status} reason={model_error_reason}",
        model_operation=operation.value if isinstance(operation, TransformPhase) else operation,
        model_id=model,
        model_payload_category=failure.payload_category,
        model_validation_issues=[asdict(issue) for issue in failure.validation_issues],
        model_attempt=attempt,
        model_error_kind=failure.kind,
        model_http_status=failure.http_status,
        model_error_reason=failure.reason,
        model_error_code=error.code
        if isinstance(error, ModelUnavailableError)
        else "provider_attempt",
        model_retryable=error.retryable if isinstance(error, ModelUnavailableError) else None,
        model_attempt_retryable=error.retry_within_operation
        if isinstance(error, ModelUnavailableError)
        else None,
    )


def log_model_response_failure(
    *,
    model: str,
    response: genai_types.GenerateContentResponse,
    operation: TransformPhase | Literal["review"] = "review",
) -> None:
    finish_reason = response.candidates[0].finish_reason if response.candidates else None
    metadata: dict[str, str | int] = {
        "model_finish_reason": (
            finish_reason
            if isinstance(finish_reason, str) and finish_reason in _SAFE_FINISH_REASONS
            else "unknown"
        )
    }
    usage = response.usage_metadata
    if usage is not None:
        metadata.update(
            {
                field: value
                for field, value in (
                    ("model_prompt_token_count", usage.prompt_token_count),
                    ("model_candidate_token_count", usage.candidates_token_count),
                    ("model_thought_token_count", usage.thoughts_token_count),
                    ("model_total_token_count", usage.total_token_count),
                )
                if type(value) is int and 0 <= value <= _MAX_MODEL_TOKEN_COUNT
            }
        )
    logger.warning(
        "document model response payload invalid operation={model_operation}",
        model_operation=operation.value if isinstance(operation, TransformPhase) else operation,
        model_id=model,
        **metadata,
    )
