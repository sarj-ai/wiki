from collections.abc import AsyncGenerator
from dataclasses import dataclass
import uuid

from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import JsonValue
import pytest

from app.auth.dependencies import get_current_user
from app.config import document_transform_in_place_enabled
from app.drive.dependencies import drive_service_dep
from app.files.dependencies import files_service_dep
from app.main import app
from app.models.user import User
from app.tasks.dependencies import task_enqueuer_dep, task_service_dep
from tests.test_document_transform_by_id import ByIdHarness
from tests.test_files_service import _user


@dataclass
class IdHttpLab:
    client: AsyncClient
    harness: ByIdHarness


@pytest.fixture
async def route_client() -> AsyncGenerator[IdHttpLab]:
    harness = ByIdHarness()
    user = _user()
    user.id = harness.owner

    def authenticated() -> User:
        return user

    def services() -> ByIdHarness:
        return harness

    previous = app.dependency_overrides.copy()
    app.dependency_overrides.update(
        {
            get_current_user: authenticated,
            task_service_dep: services,
            task_enqueuer_dep: services,
            files_service_dep: services,
            drive_service_dep: services,
            document_transform_in_place_enabled: lambda: True,
        }
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield IdHttpLab(client, harness)
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.mark.parametrize("key_location", ["generated", "header", "body", "both"])
async def test_id_only_http_submission_returns_recoverable_task_headers(
    route_client: IdHttpLab, key_location: str
):
    key = str(uuid.uuid7())
    body: dict[str, JsonValue] = {"file_id": "source"}
    headers = {"Idempotency-Key": key} if key_location in {"header", "both"} else {}
    if key_location in {"body", "both"}:
        body["idempotency_key"] = key
    response = await route_client.client.post(
        "/document-transforms/by-id", json=body, headers=headers
    )
    assert response.status_code == 202
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Location"] == f"/tasks/{response.json()['id']}"
    assert response.headers["Retry-After"] == "1"
    assert response.headers["Idempotency-Replayed"] == "false"
    assert uuid.UUID(response.headers["Idempotency-Key"]) == route_client.harness.key
    if key_location != "generated":
        assert response.headers["Idempotency-Key"] == key
    assert route_client.harness.request.mode == "both"
    assert route_client.harness.request.output_strategy == "copy"
    assert route_client.harness.submissions == 1


@pytest.mark.parametrize(
    "case", ["bad-key", "conflicting-keys", "url-instead-of-id", "extra-owner", "missing-id"]
)
async def test_invalid_submissions_never_reach_admission(route_client: IdHttpLab, case: str):
    body: dict[str, JsonValue] = {"file_id": "source"}
    headers: dict[str, str] = {}
    match case:
        case "bad-key":
            headers["Idempotency-Key"] = "invalid"
        case "conflicting-keys":
            headers["Idempotency-Key"] = str(uuid.uuid7())
            body["idempotency_key"] = str(uuid.uuid7())
        case "url-instead-of-id":
            body["file_id"] = "https://docs.google.com/document/d/source/edit"
        case "extra-owner":
            body["owner_user_id"] = str(uuid.uuid7())
        case _:
            body.clear()
    response = await route_client.client.post(
        "/document-transforms/by-id", json=body, headers=headers
    )
    assert response.status_code == 422
    assert route_client.harness.submissions == 0


async def test_rest_requires_authenticated_google_session(route_client: IdHttpLab):
    async def unauthenticated() -> None:
        raise HTTPException(status_code=401, detail="Authentication required")

    app.dependency_overrides[get_current_user] = unauthenticated
    response = await route_client.client.post(
        "/document-transforms/by-id", json={"file_id": "source"}
    )
    assert response.status_code == 401
    assert route_client.harness.submissions == 0
