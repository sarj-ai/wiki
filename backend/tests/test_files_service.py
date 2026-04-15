"""Files service — error mapping and payload shape.

Full CRUD round-trips are covered at the router layer in integration
tests. Here we focus on the two bits that can silently go wrong:

- Drive HTTP error → HTTPException status mapping
- Unsupported mimeType rejection at create time
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from googleapiclient.errors import HttpError

from app.files import service as files_service
from app.models.user import User


def _user() -> User:
    u = User()
    u.id = uuid.uuid4()
    u.email = "test@sarj.ai"
    u.google_id = "gid"
    u.token_version = 0
    return u


class _FakeResp:
    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = "test"


def _make_http_error(status_code: int) -> HttpError:
    return HttpError(_FakeResp(status_code), b'{"error": "test"}')


async def test_create_rejects_unsupported_mime_type() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await files_service.create_file(
            _user(),
            parent_folder_id="p",
            name="x",
            mime_type="application/x-evil",
        )
    assert excinfo.value.status_code == 400


async def test_http_404_maps_to_404(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeFiles:
        def get(self, **_: Any) -> _FakeFiles:
            return self

        def execute(self) -> dict[str, Any]:
            raise _make_http_error(404)

    class _FakeService:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    monkeypatch.setattr(files_service, "get_drive_service", lambda _u: _FakeService())
    with pytest.raises(HTTPException) as excinfo:
        await files_service.get_file(_user(), "missing")
    assert excinfo.value.status_code == 404


async def test_http_500_maps_to_502(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeFiles:
        def get(self, **_: Any) -> _FakeFiles:
            return self

        def execute(self) -> dict[str, Any]:
            raise _make_http_error(500)

    class _FakeService:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    monkeypatch.setattr(files_service, "get_drive_service", lambda _u: _FakeService())
    with pytest.raises(HTTPException) as excinfo:
        await files_service.get_file(_user(), "x")
    assert excinfo.value.status_code == 502


async def test_create_success_returns_file_response(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeFiles:
        def create(self, **_: Any) -> _FakeFiles:
            return self

        def execute(self) -> dict[str, Any]:
            return {
                "id": "new-id",
                "name": "Doc",
                "mimeType": "application/vnd.google-apps.document",
                "parents": ["p"],
                "driveId": "d1",
                "modifiedTime": "2026-04-15T00:00:00Z",
            }

    class _FakeService:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    monkeypatch.setattr(files_service, "get_drive_service", lambda _u: _FakeService())
    result = await files_service.create_file(
        _user(),
        parent_folder_id="p",
        name="Doc",
        mime_type="application/vnd.google-apps.document",
    )
    assert result.id == "new-id"
    assert result.parent_folder_id == "p"
    assert result.drive_id == "d1"
