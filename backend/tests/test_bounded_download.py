from __future__ import annotations

from collections import deque
from typing import NamedTuple, override

from googleapiclient.http import HttpMock, HttpRequest
from httplib2 import Response
from pydantic import JsonValue, ValidationError
import pytest

from app.document_transform.errors import DocumentGatewayError
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.drive.retry import (
    CompletedDownload,
    ContinuingDownload,
    execute_download_chunk_with_retry,
)
from tests.support import dependency


class _Progress(NamedTuple):
    total_size: JsonValue


class _Chunk(NamedTuple):
    status: _Progress | None
    done: JsonValue


class _Download:
    def __init__(self, chunk: _Chunk) -> None:
        self.chunk = chunk
        self.calls = 0

    def next_chunk(self, *, num_retries: int) -> _Chunk:
        assert num_retries == 0
        self.calls += 1
        return self.chunk


@pytest.mark.parametrize("total", [None, 0, 17])
@pytest.mark.parametrize("done", [False, True])
def test_download_reply_has_a_closed_phase_and_validated_total(
    total: int | None, done: bool
) -> None:
    download = _Download(_Chunk(_Progress(total), done))
    chunk = execute_download_chunk_with_retry(dependency(download))
    assert chunk == (CompletedDownload(total) if done else ContinuingDownload(total))
    assert download.calls == 1


@pytest.mark.parametrize("done", [None, 0, 1, "false", "true", [], {}])
def test_download_completion_is_not_coerced(done: JsonValue) -> None:
    download = _Download(_Chunk(_Progress(0), done))
    with pytest.raises(ValidationError, match="done"):
        execute_download_chunk_with_retry(dependency(download))
    assert download.calls == 1


@pytest.mark.parametrize("total", [False, True, -1, 1.5, "17", [], {}])
def test_download_size_is_not_coerced(total: JsonValue) -> None:
    download = _Download(_Chunk(_Progress(total), True))
    with pytest.raises(ValidationError, match="total_size"):
        execute_download_chunk_with_retry(dependency(download))
    assert download.calls == 1


@pytest.mark.parametrize(
    ("responses", "expected"),
    [
        ([({"status": "200", "content-length": "0"}, b"")], b""),
        ([({"status": "200", "content-length": "3"}, b"abc")], b"abc"),
        ([({"status": "200"}, b"abc")], b"abc"),
        ([({"status": "206", "content-range": "bytes 0-0/4"}, b"")], None),
        (
            [
                ({"status": "206", "content-range": "bytes 0-2/4"}, b"abc"),
                ({"status": "206", "content-range": "bytes 3-3/4"}, b""),
            ],
            None,
        ),
    ],
    ids=["empty", "known-total", "unknown-total", "stalled", "truncated"],
)
def test_bounded_download_requires_completion_and_real_progress(
    responses: list[tuple[dict[str, str], bytes]], expected: bytes | None
) -> None:
    transport = _ResponseTransport(responses)
    request = HttpRequest(transport, _response_bytes, uri="https://provider.test/document")
    if expected is None:
        with pytest.raises(DocumentGatewayError):
            GoogleDocumentGateway._execute_bounded_bytes(request, max_bytes=10)
    else:
        assert GoogleDocumentGateway._execute_bounded_bytes(request, max_bytes=10) == expected
    assert transport.calls == len(responses)


def _response_bytes(_response: Response, content: bytes) -> bytes:
    return content


class _ResponseTransport(HttpMock):
    def __init__(self, responses: list[tuple[dict[str, str], bytes]]) -> None:
        super().__init__()
        self.responses = deque(responses)
        self.calls = 0

    @override
    def request(
        self,
        uri: object,
        method: str = "GET",
        body: object = None,
        headers: object = None,
        redirections: int = 1,
        connection_type: object = None,
    ) -> tuple[Response, bytes]:
        assert body is None
        assert headers is not None
        assert redirections == 1
        assert connection_type is None
        assert uri == "https://provider.test/document"
        assert method == "GET"
        self.calls += 1
        response, content = self.responses.popleft()
        return Response(response), content
