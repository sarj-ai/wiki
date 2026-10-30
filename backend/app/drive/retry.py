from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any, NamedTuple, override

from googleapiclient.errors import HttpError
import httplib2
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)
from tenacity.wait import wait_base

from app.performance import increment, provider_retry_remaining_seconds, timed


if TYPE_CHECKING:
    from googleapiclient.http import BatchHttpRequest, HttpRequest, MediaIoBaseDownload

_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

_MAX_RETRY_AFTER_SECONDS = 30.0

_MIN_ATTEMPT_BUDGET = timedelta(seconds=1)


class ProviderDeadlineError(TimeoutError):
    pass


class DownloadProgress(NamedTuple):
    status: Any
    done: bool


def is_retryable_provider_status(status: int | None) -> bool:
    return status in _RETRYABLE_STATUSES


def provider_http_status(exc: HttpError) -> int | None:
    status = getattr(exc.resp, "status", None)
    return status if isinstance(status, int) else None


def is_transient_provider_transport_error(exc: BaseException) -> bool:
    return not isinstance(exc, ProviderDeadlineError) and isinstance(
        exc,
        (TimeoutError, ConnectionError, httplib2.ServerNotFoundError),
    )


def _is_retryable_drive_error(exc: BaseException) -> bool:
    if isinstance(exc, HttpError):
        return is_retryable_provider_status(provider_http_status(exc))
    return is_transient_provider_transport_error(exc)


def _is_rate_limit_error(exc: BaseException) -> bool:
    return isinstance(exc, HttpError) and provider_http_status(exc) == 429


def retry_after_seconds(exc: BaseException | None) -> float | None:
    if not isinstance(exc, HttpError):
        return None
    header_get = getattr(exc.resp, "get", None)
    raw: object = header_get("retry-after") if callable(header_get) else None
    if not isinstance(raw, str | int | float):
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    return min(max(seconds, 0.0), _MAX_RETRY_AFTER_SECONDS)


class _WaitRetryAfter(wait_base):
    def __init__(self, fallback: wait_base) -> None:
        self._fallback = fallback

    @override
    def __call__(self, retry_state: RetryCallState) -> float:
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        suggested = retry_after_seconds(exc)
        delay = suggested if suggested is not None else self._fallback(retry_state)
        remaining = provider_retry_remaining_seconds()
        if remaining is None:
            return delay
        return min(delay, max(remaining - _MIN_ATTEMPT_BUDGET.total_seconds(), 0.0))


_wait_policy = _WaitRetryAfter(wait_exponential_jitter(initial=1, max=10))

drive_retry = retry(
    stop=stop_after_attempt(3),
    wait=_wait_policy,
    retry=retry_if_exception(_is_retryable_drive_error),
    reraise=True,
)

drive_mutation_retry = retry(
    stop=stop_after_attempt(3),
    wait=_wait_policy,
    retry=retry_if_exception(_is_rate_limit_error),
    reraise=True,
)


@drive_retry
def execute_with_retry(req: HttpRequest) -> Any:
    _ensure_attempt_budget()
    increment("google_request_count")
    with timed("google"):
        return req.execute()


@drive_mutation_retry
def execute_mutation(req: HttpRequest) -> Any:
    _ensure_attempt_budget()
    increment("google_request_count")
    with timed("google"):
        return req.execute()


@drive_retry
def execute_batch_with_retry(batch: BatchHttpRequest) -> None:
    _ensure_attempt_budget()
    increment("google_request_count")
    with timed("google"):
        batch.execute()


@drive_retry
def execute_download_chunk_with_retry(download: MediaIoBaseDownload) -> DownloadProgress:
    _ensure_attempt_budget()
    increment("google_request_count")
    with timed("google"):
        status, done = download.next_chunk(num_retries=0)
    return DownloadProgress(status, bool(done))


def _ensure_attempt_budget() -> None:
    remaining = provider_retry_remaining_seconds()
    if remaining is not None and remaining < _MIN_ATTEMPT_BUDGET.total_seconds():
        raise ProviderDeadlineError
