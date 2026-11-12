from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from http import HTTPStatus
import math

from google.auth import exceptions as auth_errors
from google.genai import errors

from app.document_transform.errors import (
    ModelAuthenticationError,
    ModelConfigurationError,
    ModelDeadlineError,
    ModelOutputError,
    ModelPermissionError,
    ModelUnavailableError,
)
from app.performance import provider_retry_remaining_seconds


_TRANSIENT_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_PROVIDER_TRANSPORT_BASES = frozenset(
    {"TransportError", "ClientConnectionError", "ClientPayloadError"}
)
_MODEL_COMPLETION_SECONDS = 31
_MAX_RETRY_AFTER_HEADER_LENGTH = 100


def classify_model_failure(error: Exception) -> ModelUnavailableError | None:
    if isinstance(error, ModelUnavailableError):
        return error
    if isinstance(error, auth_errors.TransportError):
        return ModelUnavailableError()
    if isinstance(error, auth_errors.RefreshError):
        return ModelUnavailableError() if error.retryable else ModelAuthenticationError()
    if isinstance(error, auth_errors.GoogleAuthError | PermissionError | FileNotFoundError):
        return ModelAuthenticationError()
    if any(
        base.__module__.split(".", 1)[0] in {"httpx", "httpx2"}
        and base.__name__ in {"InvalidURL", "UnsupportedProtocol", "LocalProtocolError"}
        for base in type(error).__mro__
    ):
        return ModelConfigurationError()
    if isinstance(error, errors.APIError):
        return _api_failure(error)
    if isinstance(error, TimeoutError):
        remaining = provider_retry_remaining_seconds()
        return (
            ModelDeadlineError()
            if remaining is not None and remaining <= _MODEL_COMPLETION_SECONDS
            else ModelUnavailableError()
        )
    if isinstance(error, ValueError):
        return ModelOutputError()
    # google-genai 3 uses httpx2; older supported versions use httpx and the
    # optional aiohttp transport. Recognize their public exception base classes
    # without depending on SDK-private modules or requiring optional transports.
    if isinstance(error, OSError) or any(
        base.__module__.split(".", 1)[0] in {"httpx", "httpx2", "aiohttp"}
        and base.__name__ in _PROVIDER_TRANSPORT_BASES
        for base in type(error).__mro__
    ):
        return ModelUnavailableError()
    return None


def _api_failure(error: errors.APIError) -> ModelUnavailableError:
    if error.code == HTTPStatus.UNAUTHORIZED:
        return ModelAuthenticationError()
    if error.code == HTTPStatus.FORBIDDEN:
        return ModelPermissionError()
    if error.code in _TRANSIENT_STATUSES:
        return ModelUnavailableError(retry_after=_retry_after(error))
    return ModelConfigurationError()


def grouped_model_failure(error: BaseExceptionGroup) -> ModelUnavailableError:
    failures: list[ModelUnavailableError] = []

    def visit(group: BaseExceptionGroup) -> None:
        for child in group.exceptions:
            if isinstance(child, BaseExceptionGroup):
                visit(child)
            elif isinstance(child, ModelUnavailableError):
                failures.append(child)

    visit(error)
    chosen = next(
        (failure for failure in failures if not failure.retryable), ModelUnavailableError()
    )
    return exhausted_model_failure(chosen)


def exhausted_model_failure(error: Exception | None) -> ModelUnavailableError:
    failure = classify_model_failure(error) if error is not None else None
    if failure is None:
        return ModelUnavailableError()
    # A new exception must wrap the group. Re-raising one of the group's own
    # children with the group as its cause would create a cyclic cause graph.
    result = type(failure)()
    result.retryable = failure.retryable
    result.retry_after = failure.retry_after
    return result


def _retry_after(error: errors.APIError) -> float | None:
    response = error.response
    if response is None:
        return None
    value = response.headers.get("Retry-After")
    if not isinstance(value, str) or len(value) > _MAX_RETRY_AFTER_HEADER_LENGTH:
        return None
    try:
        seconds = float(value)
        return max(seconds, 0.0) if math.isfinite(seconds) else None
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            return max((date - datetime.now(UTC)).total_seconds(), 0.0)
        except ValueError, TypeError, OverflowError:
            return None
