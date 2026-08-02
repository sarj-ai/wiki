from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
import pytest

from app.aliases.dependencies import alias_service_dep
from app.aliases.errors import AliasConflictError, AliasNotFoundError
from app.aliases.schemas import DocumentAliasForFile, DocumentAliasRead, DocumentAliasValue
from app.aliases.service import AliasService
from app.auth.dependencies import get_current_user
from app.identifiers import FileId
from app.main import app
from tests.test_files_service import _user


class _Aliases(AliasService):
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


async def test_alias_api_requires_authentication(aliases: _Aliases) -> None:
    app.dependency_overrides[get_current_user] = _unauthorized
    app.dependency_overrides[alias_service_dep] = lambda: aliases
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async with client:
        response = await client.get("/aliases/eng-onboarding")

    assert response.status_code == 401
    assert response.headers.get("location") is None
