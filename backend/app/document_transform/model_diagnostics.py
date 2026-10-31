from __future__ import annotations

from dataclasses import dataclass
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


def model_failure_details(
    error: TimeoutError | ValueError | genai_errors.APIError,
) -> ModelFailureDetails:
    if isinstance(error, genai_errors.APIError):
        status = error.code if _HTTP_STATUS_MIN <= error.code <= _HTTP_STATUS_MAX else 0
        return ModelFailureDetails("api_error", status, _provider_reason(error))
    kind: Literal["timeout", "invalid_payload"] = (
        "timeout" if isinstance(error, TimeoutError) else "invalid_payload"
    )
    return ModelFailureDetails(kind, 0, "unknown")


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
    error: TimeoutError | ValueError | genai_errors.APIError,
) -> None:
    failure = model_failure_details(error)
    logger.warning(
        "document model attempt failed operation={model_operation} attempt={model_attempt} "
        "kind={model_error_kind} http_status={model_http_status} reason={model_error_reason}",
        model_operation=operation.value if isinstance(operation, TransformPhase) else operation,
        model_attempt=attempt,
        model_error_kind=failure.kind,
        model_http_status=failure.http_status,
        model_error_reason=failure.reason,
    )
