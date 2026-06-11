"""Retry policy tests: mutation no-retry semantics + Retry-After handling."""

from __future__ import annotations

from typing import Any, cast

from googleapiclient.errors import HttpError
from googleapiclient.http import HttpRequest
import pytest

from app.drive.retry import _retry_after_seconds, execute_mutation, execute_with_retry


class _Resp(dict[str, str]):
    """Shape-compatible stand-in for httplib2.Response."""

    def __init__(self, status: int, headers: dict[str, str] | None = None) -> None:
        super().__init__(headers or {})
        self.status = status
        self.reason = "test"


def _http_error(status: int, headers: dict[str, str] | None = None) -> HttpError:
    return HttpError(cast("Any", _Resp(status, headers)), b'{"error": "test"}')


class _FlakyRequest:
    """Fails ``failures`` times with ``error``, then succeeds."""

    def __init__(self, failures: int, error: HttpError) -> None:
        self.attempts = 0
        self._failures = failures
        self._error = error

    def execute(self) -> dict[str, Any]:
        self.attempts += 1
        if self.attempts <= self._failures:
            raise self._error
        return {"ok": True}


def test_retry_after_parses_and_caps() -> None:
    assert _retry_after_seconds(_http_error(429, {"retry-after": "7"})) == 7.0
    assert _retry_after_seconds(_http_error(429, {"retry-after": "9999"})) == 30.0
    assert _retry_after_seconds(_http_error(429)) is None
    assert _retry_after_seconds(_http_error(429, {"retry-after": "soon"})) is None
    assert _retry_after_seconds(ValueError("not http")) is None


def test_execute_with_retry_retries_transient_5xx() -> None:
    req = _FlakyRequest(1, _http_error(503, {"retry-after": "0"}))
    assert execute_with_retry(cast("HttpRequest", req)) == {"ok": True}
    assert req.attempts == 2


def test_execute_mutation_retries_rate_limit() -> None:
    req = _FlakyRequest(1, _http_error(429, {"retry-after": "0"}))
    assert execute_mutation(cast("HttpRequest", req)) == {"ok": True}
    assert req.attempts == 2


def test_execute_mutation_does_not_retry_5xx() -> None:
    req = _FlakyRequest(99, _http_error(500, {"retry-after": "0"}))
    with pytest.raises(HttpError):
        execute_mutation(cast("HttpRequest", req))
    assert req.attempts == 1
