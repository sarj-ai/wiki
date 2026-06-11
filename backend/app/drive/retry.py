"""Tenacity-based retry policy for Google Drive API calls.

Drive returns 429 / 500 / 502 / 503 / 504 with realistic frequency under
load (rate limits, sharded quota errors, transient backend failures).
Wrapping ``.execute()`` calls with exponential-jitter retry stops the
sidebar from emptying itself on a single transient blip.

Usage::

    from app.drive.retry import drive_retry, execute_with_retry

    # Direct .execute() call:
    raw = execute_with_retry(client.files().get(fileId=fid))

    # Or as a decorator on a tiny helper that contains a single Drive call:
    @drive_retry
    def _do(): ...
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from googleapiclient.errors import HttpError
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)
from tenacity.wait import wait_base


if TYPE_CHECKING:
    from googleapiclient.http import HttpRequest

_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

_MAX_RETRY_AFTER_SECONDS = 30.0


def _is_retryable_drive_error(exc: BaseException) -> bool:
    """True for transient Drive failures worth retrying.

    Strict equality on ``HttpError`` keeps us from retrying ``HTTPException``
    or other unrelated errors that happen to expose ``.resp.status``.
    """
    if not isinstance(exc, HttpError):
        return False
    return exc.resp.status in _RETRYABLE_STATUSES


def _is_rate_limit_error(exc: BaseException) -> bool:
    """True only for 429s — the one status where Drive guarantees the
    request was rejected before execution, so retrying a mutation is safe.
    """
    return isinstance(exc, HttpError) and exc.resp.status == 429


def _retry_after_seconds(exc: BaseException | None) -> float | None:
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
    """Honor a server-sent ``Retry-After``; otherwise use the fallback wait."""

    def __init__(self, fallback: wait_base) -> None:
        self._fallback = fallback

    @override
    def __call__(self, retry_state: RetryCallState) -> float:
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        suggested = _retry_after_seconds(exc)
        if suggested is not None:
            return suggested
        return self._fallback(retry_state)


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
    """Run an already-built Drive ``HttpRequest`` with retry on transient errors."""
    return req.execute()


@drive_mutation_retry
def execute_mutation(req: HttpRequest) -> Any:
    """Run a non-idempotent Drive mutation (create/copy).

    Retries only 429s — a 5xx is ambiguous (Drive may have applied the
    write), so retrying could silently duplicate the file.
    """
    return req.execute()
