from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from google.genai import errors as genai_errors
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
    kind: Literal["api_error", "timeout", "invalid_payload"]
    http_status: int
    reason: str
    payload_category: Literal["none", "unknown", "empty", "coverage", "binding", "schema"] = "none"
    validation_issues: tuple[ModelValidationIssue, ...] = ()


def model_failure_details(
    error: TimeoutError | ValueError | genai_errors.APIError,
) -> ModelFailureDetails:
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
    operation: TransformPhase | Literal["review"],
    attempt: int,
    model: str,
    error: TimeoutError | ValueError | genai_errors.APIError,
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
    )
