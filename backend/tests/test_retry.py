from __future__ import annotations

from collections import UserDict
from typing import Any, cast

from googleapiclient.errors import HttpError
from googleapiclient.http import HttpRequest
import pytest

from app.drive.retry import (
    ProviderDeadlineError,
    execute_mutation,
    execute_with_retry,
    retry_after_seconds,
)


class _Resp(UserDict[str, str]):
    def __init__(self, status: int, headers: dict[str, str] | None = None) -> None:
        super().__init__(headers or {})
        self.status = status
        self.reason = "test"


def _http_error(status: int, headers: dict[str, str] | None = None) -> HttpError:
    return HttpError(cast("Any", _Resp(status, headers)), b'{"error": "test"}')


class _FlakyRequest:
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
    assert retry_after_seconds(_http_error(429, {"retry-after": "7"})) == 7.0
    assert retry_after_seconds(_http_error(429, {"retry-after": "9999"})) == 30.0
    assert retry_after_seconds(_http_error(429)) is None
    assert retry_after_seconds(_http_error(429, {"retry-after": "soon"})) is None
    assert retry_after_seconds(ValueError("not http")) is None


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


def test_provider_call_does_not_start_without_attempt_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = _FlakyRequest(0, _http_error(503))
    monkeypatch.setattr("app.drive.retry.provider_retry_remaining_seconds", lambda: 0.5)

    with pytest.raises(ProviderDeadlineError):
        execute_with_retry(cast("HttpRequest", req))

    assert req.attempts == 0
