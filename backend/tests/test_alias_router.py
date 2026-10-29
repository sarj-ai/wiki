from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
import pytest

from app.aliases.dependencies import alias_service_dep
from app.aliases.errors import (
    AliasConflictError,
    AliasEditForbiddenError,
    AliasNotFoundError,
    AliasTargetUnsupportedError,
)
from app.aliases.schemas import (
    DocumentAliasForFile,
    DocumentAliasRead,
    DocumentAliasValue,
    RecentDocumentAlias,
)
from app.auth.dependencies import get_current_user
from app.identifiers import FileId
from app.main import app
from tests.alias_support import StrictAliasService
from tests.test_files_service import _user


class _Aliases(StrictAliasService):
    def __init__(self) -> None:
        self.current: str | None = "eng-onboarding"

    async def get_for_file(self, file_id: str) -> DocumentAliasForFile:
        del file_id
        return DocumentAliasForFile(alias=self.current)

    async def resolve(self, alias: DocumentAliasValue) -> DocumentAliasRead:
        if alias != self.current:
            raise AliasNotFoundError
        return DocumentAliasRead(alias=alias, file_id=FileId("google-file-id"))

    async def save(self, file_id: str, alias: DocumentAliasValue) -> DocumentAliasRead:
        if alias == "taken-short-link":
            raise AliasConflictError
        self.current = alias
        return DocumentAliasRead(alias=alias, file_id=FileId(file_id))

    async def retire(self, file_id: str) -> None:
        del file_id
        self.current = None

    async def ensure_polish_for_file(self, file_id: FileId) -> DocumentAliasRead:
        if file_id == "forbidden":
            raise AliasEditForbiddenError
        if file_id == "unsupported":
            raise AliasTargetUnsupportedError
        if file_id == "conflict":
            raise AliasConflictError
        self.current = self.current or "generated-link"
        return DocumentAliasRead(alias=self.current, file_id=file_id)

    async def resolve_recent(self, aliases: list[DocumentAliasValue]) -> list[RecentDocumentAlias]:
        if "eng-onboarding" not in aliases:
            return []
        return [
            RecentDocumentAlias(
                alias="eng-onboarding",
                file_id=FileId("file-1"),
                name="Engineering onboarding",
                mime_type="application/vnd.google-apps.document",
                drive_name="Engineering",
            )
        ]


async def _unauthorized() -> None:
    raise HTTPException(status_code=401, detail="Not authenticated")


async def _no_db() -> AsyncIterator[None]:
    yield None


@pytest.fixture
def aliases() -> _Aliases:
    return _Aliases()


@pytest.fixture
def client(aliases: _Aliases) -> AsyncClient:
    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[alias_service_dep] = lambda: aliases
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


async def test_alias_api_round_trip(client: AsyncClient) -> None:
    async with client:
        current = await client.get("/aliases/files/google-file-id")
        renamed = await client.put(
            "/aliases/files/google-file-id",
            json={"alias": "people-org-chart"},
        )
        resolved = await client.get("/aliases/people-org-chart")
        retired = await client.delete("/aliases/files/google-file-id")

    assert current.json() == {"alias": "eng-onboarding"}
    assert renamed.json() == {"alias": "people-org-chart", "file_id": "google-file-id"}
    assert resolved.json() == {"alias": "people-org-chart", "file_id": "google-file-id"}
    assert retired.status_code == 204


async def test_alias_api_ensures_without_transform_submission(client: AsyncClient) -> None:
    async with client:
        response = await client.post("/aliases/files/google-file-id/ensure")

    assert response.status_code == 200
    assert response.json() == {"alias": "eng-onboarding", "file_id": "google-file-id"}
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize(
    ("file_id", "expected_status", "expected_code"),
    [
        ("forbidden", 403, "alias_edit_forbidden"),
        ("unsupported", 400, "alias_target_unsupported"),
        ("conflict", 409, "alias_conflict"),
    ],
)
async def test_alias_ensure_maps_fresh_authorization_and_candidate_failures(
    client: AsyncClient, file_id: FileId, expected_status: int, expected_code: str
) -> None:
    async with client:
        response = await client.post(f"/aliases/files/{file_id}/ensure")

    assert response.status_code == expected_status
    assert response.json()["code"] == expected_code


async def test_alias_ensure_requires_authentication(aliases: _Aliases) -> None:
    app.dependency_overrides[get_current_user] = _unauthorized
    app.dependency_overrides[alias_service_dep] = lambda: aliases
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async with client:
        response = await client.post("/aliases/files/google-file-id/ensure")

    assert response.status_code == 401
    assert response.headers.get("location") is None


async def test_alias_api_maps_conflicts_and_validates_format(client: AsyncClient) -> None:
    async with client:
        conflict = await client.put(
            "/aliases/files/google-file-id",
            json={"alias": "taken-short-link"},
        )
        malformed = await client.put(
            "/aliases/files/google-file-id",
            json={"alias": "Google_File_ID"},
        )

    assert conflict.status_code == 409
    assert conflict.json()["code"] == "alias_conflict"
    assert malformed.status_code == 422


async def test_recent_alias_api_bulk_resolves_requested_aliases(client: AsyncClient) -> None:
    async with client:
        recent = await client.post("/aliases/recent", json={"aliases": ["eng-onboarding"]})

    assert recent.json() == [
        {
            "alias": "eng-onboarding",
            "file_id": "file-1",
            "name": "Engineering onboarding",
            "mime_type": "application/vnd.google-apps.document",
            "drive_name": "Engineering",
        }
    ]


async def test_recent_alias_api_hard_rejects_more_than_fifty(client: AsyncClient) -> None:
    async with client:
        response = await client.post(
            "/aliases/recent",
            json={"aliases": ["eng-onboarding"] * 51},
        )

    assert response.status_code == 422


async def test_alias_api_requires_authentication(aliases: _Aliases) -> None:
    app.dependency_overrides[get_current_user] = _unauthorized
    app.dependency_overrides[alias_service_dep] = lambda: aliases
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async with client:
        response = await client.get("/aliases/eng-onboarding")

    assert response.status_code == 401
    assert response.headers.get("location") is None
