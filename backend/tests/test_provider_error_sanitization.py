from __future__ import annotations

from collections.abc import Iterator
import secrets
from typing import Self
import uuid

from googleapiclient.errors import HttpError
from httplib2 import Response
from httpx import ASGITransport, AsyncClient
from loguru import logger
import pytest

from app.drive.service import GoogleDriveService
from app.files.dependencies import files_service_dep
from app.files.errors import DriveUpstreamError
from app.files.service import GoogleFilesService
from app.identifiers import FileId, GoogleUserId, UserId
from app.main import app
from app.models.user import User


_PROVIDER_URL = "https://www.googleapis.com/drive/v3/files/private-resource-id"
_PROVIDER_TOKEN = secrets.token_urlsafe()
_PROVIDER_BODY = f'{{"error":{{"message":"Bearer {_PROVIDER_TOKEN}"}}}}'


def _raw_provider_error(status: int = 500) -> HttpError:
    response = Response({"reason": "provider failure", "status": str(status), "retry-after": "0"})
    return HttpError(response, _PROVIDER_BODY.encode(), uri=_PROVIDER_URL)


class _FailingCall:
    def __init__(self, status: int = 500) -> None:
        self._status = status

    def execute(self) -> None:
        raise _raw_provider_error(self._status)


class _FailingGoogleClient:
    def __init__(self, status: int = 500) -> None:
        self._status = status

    def files(self) -> Self:
        return self

    def drives(self) -> Self:
        return self

    def get(self, **_kwargs: object) -> _FailingCall:
        return _FailingCall(self._status)

    def list(self, **_kwargs: object) -> _FailingCall:
        return _FailingCall(self._status)


def _user() -> User:
    user = User()
    user.id = UserId(uuid.uuid7())
    user.email = "test@sarj.ai"
    user.google_id = GoogleUserId("provider-error-test")
    user.token_version = 0
    return user


async def _translated_provider_error(monkeypatch: pytest.MonkeyPatch) -> DriveUpstreamError:
    user = _user()
    monkeypatch.setattr(
        GoogleDriveService,
        "build_drive_client",
        staticmethod(lambda _user: _FailingGoogleClient()),
    )
    files = GoogleFilesService(user=user, drive=GoogleDriveService(user))
    with pytest.raises(DriveUpstreamError) as excinfo:
        await files.get(FileId("private-resource-id"))
    return excinfo.value


class _FailingFiles:
    def __init__(self, error: DriveUpstreamError) -> None:
        self._error = error

    async def get_html_content(self, file_id: FileId) -> bytes:
        raise self._error


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


async def test_provider_error_response_contains_only_stable_public_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = await _translated_provider_error(monkeypatch)
    app.dependency_overrides[files_service_dep] = lambda: _FailingFiles(error)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/files/private-resource-id/content")

    assert response.status_code == 502
    assert response.json() == {
        "code": "drive_upstream",
        "detail": "Google Drive request could not be completed",
    }
    serialized = response.text
    assert _PROVIDER_URL not in serialized
    assert _PROVIDER_TOKEN not in serialized
    assert _PROVIDER_BODY not in serialized


async def test_translated_provider_error_discards_raw_provider_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = await _translated_provider_error(monkeypatch)

    assert error.public_detail == "Google Drive request could not be completed"
    assert _PROVIDER_URL not in str(error)
    assert _PROVIDER_TOKEN not in str(error)
    assert _PROVIDER_BODY not in str(error)


async def test_provider_failure_log_contains_only_operation_and_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        GoogleDriveService,
        "build_drive_client",
        staticmethod(lambda _user: _FailingGoogleClient(status=503)),
    )
    messages: list[str] = []
    sink = logger.add(messages.append, format="{message}")
    try:
        await GoogleDriveService(_user()).list_drives()
    finally:
        logger.remove(sink)

    rendered = "".join(messages)
    assert "Google Drive request failed operation=my_drive_root status=503" in rendered
    assert "Google Drive request failed operation=list_drives status=503" in rendered
    assert _PROVIDER_URL not in rendered
    assert _PROVIDER_TOKEN not in rendered
    assert _PROVIDER_BODY not in rendered
