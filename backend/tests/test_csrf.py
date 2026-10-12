from __future__ import annotations

from collections.abc import AsyncIterator
from typing import override

from fastapi import FastAPI, Response
from httpx import ASGITransport, AsyncClient
import pytest

from app.auth.dependencies import auth_service_dep
from app.auth.service import AuthService, AuthSession
from app.config import settings
from app.csrf import CookieOriginMiddleware
from app.models.user import User


FRONTEND_ORIGIN = "https://wiki.sarj.ai"
HOSTILE_SIBLING_ORIGIN = "https://attacker.sarj.ai"
UNSAFE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


@pytest.fixture
def unit_app() -> FastAPI:
    test_app = FastAPI()
    test_app.add_middleware(CookieOriginMiddleware, allowed_origin=FRONTEND_ORIGIN)

    @test_app.api_route("/mutation", methods=[*UNSAFE_METHODS], status_code=204)
    async def mutation() -> Response:
        return Response(status_code=204)

    @test_app.get("/oauth-callback", status_code=204)
    async def oauth_callback() -> Response:
        return Response(status_code=204)

    return test_app


@pytest.fixture
async def client(unit_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=unit_app), base_url="http://test") as value:
        yield value


class _FakeAuthService(AuthService):
    def __init__(self) -> None:
        self.revocations: list[str | None] = []

    @override
    async def exchange_code_for_session(
        self, code: str, state: str, expected_state: str | None
    ) -> AuthSession:
        raise NotImplementedError

    @override
    async def refresh_session(self, refresh_token: str) -> AuthSession:
        return AuthSession(jwt_access="new-access", jwt_refresh="new-refresh")

    @override
    async def revoke_session(self, access_token: str | None) -> None:
        self.revocations.append(access_token)

    @override
    async def resolve_current_user(self, jwt_token: str) -> User:
        raise NotImplementedError


@pytest.fixture
async def application_client() -> AsyncIterator[AsyncClient]:
    from app.main import app

    app.dependency_overrides[auth_service_dep] = _FakeAuthService
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
        yield value
    app.dependency_overrides.clear()


@pytest.mark.parametrize("method", UNSAFE_METHODS)
@pytest.mark.parametrize("cookie_name", ["access_token", "refresh_token"])
async def test_cookie_authenticated_mutation_accepts_exact_frontend_origin(
    client: AsyncClient,
    method: str,
    cookie_name: str,
) -> None:
    response = await client.request(
        method,
        "/mutation",
        headers={
            "Cookie": f"{cookie_name}=browser-session",
            "Origin": FRONTEND_ORIGIN,
        },
    )

    assert response.status_code == 204


@pytest.mark.parametrize("method", UNSAFE_METHODS)
@pytest.mark.parametrize("origin", [None, HOSTILE_SIBLING_ORIGIN, "null"])
async def test_cookie_authenticated_mutation_rejects_untrusted_origin(
    client: AsyncClient,
    method: str,
    origin: str | None,
) -> None:
    headers = {"Cookie": "access_token=browser-session"}
    if origin is not None:
        headers["Origin"] = origin
    response = await client.request(
        method,
        "/mutation",
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json() == {
        "code": "csrf_origin_invalid",
        "detail": "Request origin is not allowed",
    }


@pytest.mark.parametrize("method", UNSAFE_METHODS)
async def test_bearer_mutation_without_browser_cookies_does_not_require_origin(
    client: AsyncClient,
    method: str,
) -> None:
    response = await client.request(
        method,
        "/mutation",
        headers={"Authorization": "Bearer internal-service-token"},
    )

    assert response.status_code == 204


async def test_safe_oauth_callback_is_not_origin_guarded(client: AsyncClient) -> None:
    response = await client.get(
        "/oauth-callback",
        headers={
            "Cookie": "access_token=browser-session",
            "Origin": HOSTILE_SIBLING_ORIGIN,
        },
    )

    assert response.status_code == 204


@pytest.mark.parametrize(
    ("path", "cookie_name"),
    [("/auth/logout", "access_token"), ("/auth/refresh", "refresh_token")],
)
async def test_auth_cookie_mutations_are_guarded_in_application(
    application_client: AsyncClient,
    path: str,
    cookie_name: str,
) -> None:
    cookie = f"{cookie_name}=browser-session"
    missing = await application_client.post(path, headers={"Cookie": cookie})
    hostile = await application_client.post(
        path,
        headers={"Cookie": cookie, "Origin": HOSTILE_SIBLING_ORIGIN},
    )
    valid = await application_client.post(
        path,
        headers={"Cookie": cookie, "Origin": settings.frontend_origin},
    )

    assert missing.status_code == 403
    assert hostile.status_code == 403
    assert valid.status_code == 200


async def test_bearer_logout_without_origin_is_allowed_and_revokes_token() -> None:
    from app.main import app

    auth = _FakeAuthService()
    app.dependency_overrides[auth_service_dep] = lambda: auth
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/auth/logout",
            headers={"Authorization": "Bearer server-action-token"},
        )
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert auth.revocations == ["server-action-token"]


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        (
            [("Cookie", "unrelated=a"), ("Cookie", "access_token="), ("Origin", FRONTEND_ORIGIN)],
            204,
        ),
        (
            [("Cookie", "unrelated=a"), ("Cookie", "refresh_token=")],
            403,
        ),
        (
            [
                ("Cookie", 'access_token="a\\073b"'),
                ("Origin", HOSTILE_SIBLING_ORIGIN),
                ("Origin", FRONTEND_ORIGIN),
            ],
            403,
        ),
        (
            [
                ("Cookie", "access_token=a"),
                ("Origin", FRONTEND_ORIGIN),
                ("Origin", HOSTILE_SIBLING_ORIGIN),
            ],
            204,
        ),
    ],
    ids=[
        "later-empty-access-cookie",
        "later-empty-refresh-cookie",
        "first-origin-denied",
        "first-origin-allowed",
    ],
)
async def test_native_cookie_headers_merge_and_first_origin_is_authoritative(
    client: AsyncClient, headers: list[tuple[str, str]], status: int
) -> None:
    response = await client.post("/mutation", headers=headers)
    assert response.status_code == status
