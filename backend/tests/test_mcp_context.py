from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Self
import uuid

import httpx
import pytest

from app.auth.google_tokens import GoogleReauthenticationRequiredError
from app.drive.service import GoogleDriveService
from app.identifiers import UserId
from app.mcp.context import McpAuthorizationError, ValidatedMcpPrincipal, open_mcp_context
from app.mcp.schemas import McpCapability
from app.models.user import User


class Session:
    def __init__(self, user: User | None) -> None:
        self.user = user
        self.closed = False
        self.lookups: list[UserId] = []
        self.commits = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        self.closed = True

    async def get(self, model: type[User], user_id: UserId) -> User | None:
        assert model is User
        self.lookups.append(user_id)
        return self.user

    async def commit(self) -> None:
        self.commits += 1


@pytest.mark.parametrize("case", ["missing", "revision", "disconnected", "scope", "valid"])
async def test_context_requires_fresh_identity_and_connected_google(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    user = User(id=uuid.uuid7(), token_version=2, encrypted_refresh_token="encrypted-placeholder")
    if case == "disconnected":
        user.encrypted_refresh_token = None
    session = Session(None if case == "missing" else user)
    prepared_users: list[User] = []
    drive_users: list[User] = []

    def build_drive(
        candidate: User, *, cache_service: object, refresh_handler: Any
    ) -> GoogleDriveService:
        assert cache_service is None
        drive_users.append(candidate)
        return GoogleDriveService(candidate, refresh_handler=refresh_handler)

    class Manager:
        def __init__(self, db: object, http: object) -> None:
            assert db is session

        async def prepare(self, candidate: User) -> Any:
            prepared_users.append(candidate)
            return SimpleNamespace(user=candidate, refresh_handler=None)

    def sessionmaker() -> Any:
        return session

    monkeypatch.setattr("app.mcp.context.GoogleTokenManager", Manager)
    monkeypatch.setattr("app.mcp.context.GoogleDriveService", build_drive)
    principal = ValidatedMcpPrincipal(
        user.id,
        1 if case == "revision" else 2,
        frozenset() if case == "scope" else frozenset({McpCapability.READ}),
    )
    async with httpx.AsyncClient() as http:
        if case == "valid":
            async with open_mcp_context(principal, sessionmaker=sessionmaker, http=http) as context:
                assert context.principal == principal
                assert session.closed is False
            assert prepared_users == [user]
            assert drive_users == [user]
            assert session.commits == 1
        else:
            error = (
                GoogleReauthenticationRequiredError
                if case == "disconnected"
                else McpAuthorizationError
            )
            with pytest.raises(error):
                async with open_mcp_context(principal, sessionmaker=sessionmaker, http=http):
                    pytest.fail("Unauthorized context yielded")
            assert prepared_users == []
            assert drive_users == []
    assert session.lookups == ([] if case == "scope" else [user.id])
    assert session.closed is (case != "scope")
