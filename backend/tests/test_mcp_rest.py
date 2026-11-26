from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TypedDict
import uuid

from fastmcp.server.auth import StaticTokenVerifier
from fastmcp.server.dependencies import get_access_token
from httpx import ASGITransport, AsyncClient
import pytest

from app.identifiers import IdempotencyKey, TaskId, UserId
from app.mcp.context import McpApplicationContext, ValidatedMcpPrincipal
from app.mcp.schemas import McpCapability
from app.mcp.server import create_mcp_server
from app.tasks.errors import TaskNotFoundError
from app.tasks.schemas import TaskAdmission, TaskKind, TaskRead, TaskStatus
from tests.support import dependency
from tests.test_document_transform_by_id import ByIdHarness
from tests.test_mcp_native_runtime import running_lifespan


class TokenDetails(TypedDict):
    client_id: str
    scopes: list[str]


class RestHarness(ByIdHarness):
    def __init__(self) -> None:
        super().__init__()
        self.owners: list[tuple[TaskId, UserId]] = []
        self.admission_owners: list[UserId] = []
        self.missing_task = False

    async def get_for_owner(self, task_id: TaskId, owner: UserId) -> TaskRead:
        self.owners.append((task_id, owner))
        if self.missing_task or owner != self.owner:
            raise TaskNotFoundError
        return self.reservation.task

    async def get_admission(
        self, owner: UserId, kind: TaskKind, key: IdempotencyKey
    ) -> TaskAdmission:
        assert kind is TaskKind.FILE_DOCUMENT_TRANSFORM
        assert key == self.key
        self.admission_owners.append(owner)
        return TaskAdmission(task_id=self.reservation.task.id, status=TaskStatus.PENDING)


@dataclass
class RestLab:
    client: AsyncClient
    harness: RestHarness
    tokens: dict[str, TokenDetails]

    @property
    def owner(self) -> UserId:
        return self.harness.owner

    @property
    def task(self) -> TaskRead:
        return self.harness.reservation.task


@pytest.fixture
async def api() -> AsyncGenerator[RestLab]:
    harness = RestHarness()
    tokens: dict[str, TokenDetails] = {
        "synthetic-write": {"client_id": "client", "scopes": ["wiki:read", "wiki:transform"]},
        "synthetic-read": {"client_id": "client", "scopes": ["wiki:read"]},
    }

    @asynccontextmanager
    async def contexts() -> AsyncGenerator[McpApplicationContext]:
        token = get_access_token()
        assert token is not None
        yield McpApplicationContext(
            principal=ValidatedMcpPrincipal(
                harness.owner, 1, frozenset(McpCapability(scope) for scope in token.scopes)
            ),
            drive=dependency(harness),
            files=dependency(harness),
            tasks=dependency(harness),
            aliases=dependency(harness),
        )

    server = create_mcp_server(
        auth=StaticTokenVerifier(dependency(tokens)),
        context_factory=contexts,
        enqueuer=dependency(harness),
    )
    http = server.http_app(
        path="/mcp",
        stateless_http=True,
        json_response=True,
        host_origin_protection=True,
        allowed_hosts=["api.test"],
        allowed_origins=["https://wiki.test"],
    )
    async with (
        running_lifespan(http),
        AsyncClient(transport=ASGITransport(app=http), base_url="https://api.test") as client,
    ):
        yield RestLab(client, harness, tokens)


@pytest.mark.parametrize("credential", [None, "invalid-token", "synthetic-session-jwt"])
@pytest.mark.parametrize("route", ["polish", "tasks", "admissions"])
async def test_rest_requires_mcp_oauth_not_browser_sessions(
    api: RestLab, credential: str | None, route: str
):
    headers = (
        {"Authorization": f"Bearer {credential}"}
        if credential is not None
        else {"Cookie": "access_token=synthetic-session-jwt"}
    )
    if route == "polish":
        response = await api.client.post(
            "/mcp/v1/polish", headers=headers, json={"file_id": "source"}
        )
    else:
        response = await api.client.get(f"/mcp/v1/{route}/{uuid.uuid7()}", headers=headers)
    assert response.status_code == 401
    assert "resource_metadata" in response.headers["WWW-Authenticate"]
    assert response.headers["Cache-Control"] == "private, no-store"
    assert api.harness.submissions == 0
    assert api.harness.owners == []


async def test_id_only_oauth_submission_and_task_recovery_use_authenticated_owner(api: RestLab):
    headers = {"Authorization": "Bearer synthetic-write"}
    response = await api.client.post("/mcp/v1/polish", json={"file_id": "source"}, headers=headers)
    assert response.status_code == 202, response.text
    body = response.json()
    key = uuid.UUID(body["idempotency_key"])
    assert str(key) == response.headers["Idempotency-Key"]
    assert response.headers["Idempotency-Replayed"] == "false"
    assert response.headers["Retry-After"] == "1"
    assert api.harness.request.mode == "both"
    assert api.harness.request.output_strategy == "copy"
    assert api.harness.owners[0][1] == api.owner
    task = await api.client.get(response.headers["Location"], headers=headers)
    assert task.status_code == 200
    assert task.json()["task"]["id"] == str(api.task.id)
    assert api.harness.owners == [(api.task.id, api.owner)] * 2
    admission = await api.client.get(f"/mcp/v1/admissions/{key}", headers=headers)
    assert admission.status_code == 200
    assert admission.json()["admission"]["task_id"] == str(api.task.id)
    assert api.harness.admission_owners == [api.owner]


async def test_read_only_oauth_cannot_dispatch_a_polish(api: RestLab):
    response = await api.client.post(
        "/mcp/v1/polish",
        json={"file_id": "source"},
        headers={"Authorization": "Bearer synthetic-read"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "insufficient_scope"
    assert "wiki:transform" in response.headers["WWW-Authenticate"]
    assert api.harness.submissions == 0


async def test_oversized_stream_is_rejected_before_reading_the_remaining_body(api: RestLab):
    async def chunks() -> AsyncGenerator[bytes]:
        yield b"x" * 16_385
        pytest.fail("The server must stop consuming an oversized body")

    response = await api.client.post(
        "/mcp/v1/polish",
        content=chunks(),
        headers={
            "Authorization": "Bearer synthetic-write",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 413
    assert api.harness.submissions == 0


async def test_revocation_is_effective_on_the_next_rest_request(api: RestLab):
    headers = {"Authorization": "Bearer synthetic-read"}
    first = await api.client.get(f"/mcp/v1/tasks/{api.task.id}", headers=headers)
    assert first.status_code == 200
    api.tokens.pop("synthetic-read")
    second = await api.client.get(f"/mcp/v1/tasks/{api.task.id}", headers=headers)
    assert second.status_code == 401
    assert len(api.harness.owners) == 1


@pytest.mark.parametrize(
    "case",
    [
        "malformed-json",
        "oversized",
        "wrong-media-type",
        "injected-owner",
        "bad-key",
        "different-keys",
    ],
)
async def test_rest_bounds_and_validates_before_provider_work(api: RestLab, case: str):
    headers = {"Authorization": "Bearer synthetic-write", "Content-Type": "application/json"}
    body = '{"file_id":"source"}'
    status = 422
    match case:
        case "malformed-json":
            body = "{secret-canary"
        case "oversized":
            body = '{"file_id":"' + "x" * 17_000 + '"}'
            status = 413
        case "wrong-media-type":
            headers["Content-Type"] = "text/plain"
            status = 415
        case "injected-owner":
            body = '{"file_id":"source","owner_user_id":"secret-canary"}'
        case "bad-key":
            headers["Idempotency-Key"] = "secret-canary"
        case _:
            headers["Idempotency-Key"] = str(uuid.uuid7())
            body = '{"file_id":"source","idempotency_key":"' + str(uuid.uuid7()) + '"}'
    response = await api.client.post("/mcp/v1/polish", content=body, headers=headers)
    assert response.status_code == status
    assert "secret-canary" not in response.text
    assert api.harness.submissions == 0


async def test_rest_preserves_explicit_retry_key(api: RestLab):
    key = str(uuid.uuid7())
    response = await api.client.post(
        "/mcp/v1/polish",
        json={"file_id": "source"},
        headers={"Authorization": "Bearer synthetic-write", "Idempotency-Key": key},
    )
    assert response.status_code == 202
    assert response.json()["idempotency_key"] == key
    assert str(api.harness.key) == key


async def test_foreign_or_missing_tasks_are_not_exposed(api: RestLab):
    api.harness.missing_task = True
    response = await api.client.get(
        f"/mcp/v1/tasks/{uuid.uuid7()}", headers={"Authorization": "Bearer synthetic-read"}
    )
    assert response.status_code == 404
    assert response.json()["code"] == "task_not_found"


async def test_provider_failure_never_returns_credentials_or_tracebacks(
    api: RestLab, caplog: pytest.LogCaptureFixture
):
    api.harness.file_error = RuntimeError("https://provider.test?access_token=secret-canary")
    response = await api.client.post(
        "/mcp/v1/polish",
        json={"file_id": "source"},
        headers={"Authorization": "Bearer synthetic-write"},
    )
    assert response.status_code == 502
    assert "secret-canary" not in response.text
    assert "secret-canary" not in caplog.text


@pytest.mark.parametrize(
    ("header", "value"), [("Host", "attacker.test"), ("Origin", "https://attacker.test")]
)
async def test_host_and_origin_guards_cover_rest_routes(api: RestLab, header: str, value: str):
    response = await api.client.post(
        "/mcp/v1/polish",
        json={"file_id": "source"},
        headers={"Authorization": "Bearer synthetic-write", header: value},
    )
    assert response.status_code in {400, 403, 421}
    assert api.harness.submissions == 0
