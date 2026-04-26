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

from typing import TYPE_CHECKING, Any

from googleapiclient.errors import HttpError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

if TYPE_CHECKING:
    from googleapiclient.http import HttpRequest

_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


def _is_retryable_drive_error(exc: BaseException) -> bool:
    """True for transient Drive failures worth retrying.

    Strict equality on ``HttpError`` keeps us from retrying ``HTTPException``
    or other unrelated errors that happen to expose ``.resp.status``.
    """
    if not isinstance(exc, HttpError):
        return False
    return exc.resp.status in _RETRYABLE_STATUSES


drive_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=10),
    retry=retry_if_exception(_is_retryable_drive_error),
    reraise=True,
)


@drive_retry
def execute_with_retry(req: HttpRequest) -> Any:
    """Run an already-built Drive ``HttpRequest`` with retry on transient errors."""
    return req.execute()
