import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import datetime, timedelta
import traceback
from typing import Literal
from urllib.parse import parse_qs, urlsplit
import uuid

from cryptography.fernet import Fernet
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from pydantic_settings import BaseSettings, SettingsConfigDict
import pytest
from sqlalchemy import Table, func, insert, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from app.identifiers import GoogleUserId, UserId
from app.mcp.native_admission import PostgresRegistrationAdmission
from app.mcp.native_core import NativeOAuthCore
from app.mcp.native_provider import NativeWikiOAuthProvider
from app.mcp.native_request_context import (
    NativeOAuthEndpoint,
    NativeOAuthRequestContext,
    native_oauth_request,
    require_native_oauth_request,
)
from app.mcp.native_schemas import NativeCredential, NativeOAuthError, WikiConsentIdentity
from app.mcp.native_state import NativeStateStore, native_database_time
from app.mcp.protection import OAuthStateProtector
from app.mcp.redirect_policy import McpRedirectPolicy
from app.mcp.schemas import McpCapability
from app.mcp.storage import PostgresOAuthStorage
from app.models.mcp_grant import McpGrant
from app.models.mcp_native_state import McpNativeState
from app.models.mcp_registration_admission import McpRegistrationAdmission
from app.models.user import User


_RESOURCE = "https://api.example.test/mcp"
_BROWSER = "a" * 64
_CALLBACK = "http://127.0.0.1:4567/callback"
_PUBLIC_AUTH = "none"


class QASettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WIKI_MCP_QA_")
    database_url: str | None = None


def request(endpoint: Literal["authorize", "token", "register", "revoke"]) -> None:
    native_oauth_request.set(
        NativeOAuthRequestContext(_RESOURCE, _BROWSER, NativeOAuthEndpoint(endpoint))
    )


@dataclass
class Lab:
    sessions: async_sessionmaker[AsyncSession]
    providers: list[NativeWikiOAuthProvider]
    user: WikiConsentIdentity
    other: WikiConsentIdentity
    client: OAuthClientInformationFull

    async def intent(self, *, callback: str = _CALLBACK) -> str:
        request("authorize")
        url = await self.providers[0].authorize(
            self.client,
            AuthorizationParams(
                state="client-state",
                scopes=list(McpCapability),
                code_challenge="a" * 43,
                redirect_uri=AnyUrl(callback),
                redirect_uri_provided_explicitly=True,
                resource=_RESOURCE,
            ),
        )
        return parse_qs(urlsplit(url).query)["intent"][0]

    async def code(self, *, transform: bool = False) -> AuthorizationCode:
        provider = self.providers[0]
        intent = await self.intent()
        view = await provider.core.consent_view(intent, identity=self.user, browser=_BROWSER)
        redirect = await provider.core.decide(
            intent,
            identity=self.user,
            browser=_BROWSER,
            csrf=view.csrf,
            approve=True,
            transform_approved=transform,
        )
        key = parse_qs(urlsplit(redirect).query)["code"][0]
        code = await provider.load_authorization_code(self.client, key)
        assert code is not None
        request("token")
        return code

    async def tokens(self, *, transform: bool = False) -> OAuthToken:
        return await self.providers[0].exchange_authorization_code(
            self.client, await self.code(transform=transform)
        )


@pytest.fixture
async def lab() -> AsyncGenerator[Lab]:
    dsn = QASettings().database_url
    if dsn is None:
        pytest.skip("Requires disposable native MCP PostgreSQL QA database")
    url = make_url(dsn)
    if (
        url.host not in {"127.0.0.1", "localhost", "::1"}
        or not (
            (url.database or "").startswith("wiki_mcp_qa") or url.database == "wiki_polish29_qa"
        )
        or url.drivername != "postgresql+asyncpg"
    ):
        pytest.fail("Only loopback PostgreSQL named wiki_mcp_qa* or wiki_polish29_qa is allowed")
    schema = "native_test_" + uuid.uuid7().hex
    engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    protector = OAuthStateProtector(
        (Fernet.generate_key().decode(),), lock_key=Fernet.generate_key().decode()
    )
    providers = [
        NativeWikiOAuthProvider(
            core=NativeOAuthCore(
                PostgresOAuthStorage(sessions, protector),
                NativeStateStore(protector),
                _RESOURCE,
                issuer="https://api.example.test",
            ),
            base_url="https://api.example.test",
            consent_url="https://wiki.example.test/mcp/consent",
            redirects=McpRedirectPolicy(loopback_paths=("/callback",)),
            request_guard=require_native_oauth_request,
            registration_admission=PostgresRegistrationAdmission(sessions).admit,
        )
        for _ in range(2)
    ]
    context_token = native_oauth_request.set(None)
    try:
        async with engine.begin() as connection:
            await connection.execute(CreateSchema(schema))
            for model in (User, McpGrant, McpNativeState, McpRegistrationAdmission):
                table = model.__table__
                assert isinstance(table, Table)
                await connection.run_sync(table.create)
        users = [
            User(
                id=UserId(uuid.uuid7()),
                google_id=GoogleUserId(name),
                email=f"{name}@example.test",
                encrypted_refresh_token="encrypted-test-placeholder",
            )
            for name in ("alice", "bob")
        ]
        async with sessions.begin() as db:
            db.add_all(users)
        client = OAuthClientInformationFull(
            client_id="client",
            client_name="Untrusted Client",
            redirect_uris=[AnyUrl(_CALLBACK)],
            token_endpoint_auth_method=_PUBLIC_AUTH,
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope=" ".join(McpCapability),
        )
        request("register")
        await providers[0].register_client(client)
        yield Lab(
            sessions,
            providers,
            WikiConsentIdentity(user_id=users[0].id, session_revision=users[0].token_version),
            WikiConsentIdentity(user_id=users[1].id, session_revision=users[1].token_version),
            client,
        )
    finally:
        native_oauth_request.reset(context_token)
        async with engine.begin() as connection:
            await connection.execute(DropSchema(schema, cascade=True))
        await engine.dispose()


async def test_consent_read_and_cross_user_csrf_cannot_grant(lab: Lab) -> None:
    core = lab.providers[0].core
    intent = await lab.intent()
    view = await core.consent_view(intent, identity=lab.user, browser=_BROWSER)
    assert await core.list_connections(lab.user) == []
    for identity, browser, csrf in (
        (lab.other, _BROWSER, view.csrf),
        (lab.user, "wrong", view.csrf),
        (lab.user, _BROWSER, "wrong"),
    ):
        with pytest.raises(NativeOAuthError):
            await core.decide(intent, identity=identity, browser=browser, csrf=csrf, approve=True)
    assert await core.list_connections(lab.user) == []
    denied = await core.decide(
        intent, identity=lab.user, browser=_BROWSER, csrf=view.csrf, approve=False
    )
    assert parse_qs(urlsplit(denied).query) == {
        "iss": [core.issuer],
        "error": ["access_denied"],
        "state": ["client-state"],
    }
    with pytest.raises(NativeOAuthError):
        await core.decide(intent, identity=lab.user, browser=_BROWSER, csrf=view.csrf, approve=True)


async def test_authorize_accepts_variable_loopback_port_but_not_host_or_path(lab: Lab) -> None:
    assert await lab.intent(callback="http://127.0.0.1:9876/callback")
    for callback in (
        "http://localhost:9876/callback",
        "http://127.0.0.1:9876/other",
    ):
        with pytest.raises(AuthorizeError):
            await lab.intent(callback=callback)


async def test_two_instance_code_and_refresh_race_never_reissues(lab: Lab) -> None:
    code = await lab.code()
    results = await asyncio.gather(
        *(provider.exchange_authorization_code(lab.client, code) for provider in lab.providers),
        return_exceptions=True,
    )
    tokens = [result for result in results if isinstance(result, OAuthToken)]
    assert len(tokens) == 1
    assert sum(isinstance(result, TokenError) for result in results) == 1
    assert tokens[0].scope == McpCapability.READ
    refresh = tokens[0].refresh_token
    assert refresh is not None
    loaded = await lab.providers[0].load_refresh_token(lab.client, refresh)
    assert loaded is not None
    rotated = await asyncio.gather(
        *(
            provider.exchange_refresh_token(lab.client, loaded, [McpCapability.READ])
            for provider in lab.providers
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, OAuthToken) for result in rotated) == 1
    assert sum(isinstance(result, TokenError) for result in rotated) == 1
    issued = next(result for result in rotated if isinstance(result, OAuthToken))
    assert await lab.providers[0].load_access_token(issued.access_token) is None
    async with lab.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(McpGrant)) == 1


async def test_read_cannot_elevate_and_old_generation_replay_does_not_revoke_new(lab: Lab) -> None:
    old = await lab.tokens()
    assert old.refresh_token is not None
    refresh = await lab.providers[0].load_refresh_token(lab.client, old.refresh_token)
    assert refresh is not None
    with pytest.raises(TokenError):
        await lab.providers[0].exchange_refresh_token(lab.client, refresh, list(McpCapability))
    new = await lab.tokens(transform=True)
    assert new.scope == " ".join(McpCapability)
    with pytest.raises(TokenError):
        await lab.providers[1].exchange_refresh_token(lab.client, refresh, [McpCapability.READ])
    assert await lab.providers[0].load_access_token(old.access_token) is None
    assert await lab.providers[1].load_access_token(new.access_token) is not None


@pytest.mark.parametrize("failure", ["logout", "google-disconnect", "expiry"])
async def test_logout_and_disconnected_google_deny_next_token_use(lab: Lab, failure: str) -> None:
    tokens = await lab.tokens()
    async with lab.sessions.begin() as db:
        if failure == "expiry":
            await db.execute(update(McpNativeState).values(expires_at=func.clock_timestamp()))
        else:
            changes = (
                {"token_version": User.token_version + 1}
                if failure == "logout"
                else {"encrypted_refresh_token": None}
            )
            await db.execute(update(User).where(User.id == lab.user.user_id).values(**changes))
    assert await lab.providers[1].load_access_token(tokens.access_token) is None
    if failure == "expiry":
        return
    with pytest.raises(NativeOAuthError):
        await lab.providers[0].core.consent_view(
            await lab.intent(), identity=lab.user, browser=_BROWSER
        )


async def test_code_exchange_requires_request_guard_and_immutable_loaded_fields(lab: Lab) -> None:
    code = await lab.code()
    native_oauth_request.set(None)
    with pytest.raises(TokenError):
        await lab.providers[0].exchange_authorization_code(lab.client, code)
    request("token")
    with pytest.raises(TokenError):
        await lab.providers[0].exchange_authorization_code(
            lab.client, code.model_copy(update={"code_challenge": "b" * 43})
        )
    tokens = await lab.providers[0].exchange_authorization_code(lab.client, code)
    assert await lab.providers[1].load_access_token(tokens.access_token) is not None


async def test_state_is_encrypted_and_credentials_are_purpose_separated(lab: Lab) -> None:
    tokens = await lab.tokens()
    assert await lab.providers[0].load_access_token(tokens.refresh_token or "") is None
    assert await lab.providers[0].load_refresh_token(lab.client, tokens.access_token) is None
    async with lab.sessions() as db:
        rows = list(await db.scalars(select(McpNativeState)))
        assert rows
        for row in rows:
            assert tokens.access_token not in row.ciphertext + row.key_digest
            assert "encrypted-test-placeholder" not in row.ciphertext
            assert "Untrusted Client" not in row.ciphertext


async def test_failed_issuance_rolls_back_code_consumption_and_both_tokens(
    lab: Lab,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = lab.providers[0]
    code = await lab.code()
    original = provider.core.issue
    minted: list[OAuthToken] = []

    async def fail_after_issue(
        db: AsyncSession, credential: NativeCredential, scopes: list[McpCapability]
    ) -> OAuthToken:
        minted.append(await original(db, credential, scopes))
        message = "secret-canary provider detail"
        raise RuntimeError(message)

    monkeypatch.setattr(provider.core, "issue", fail_after_issue)
    with pytest.raises(TokenError) as error:
        await provider.exchange_authorization_code(lab.client, code)
    assert "secret-canary" not in str(error.value)
    assert "secret-canary" not in "".join(traceback.format_exception(error.value))
    assert len(minted) == 1
    assert await lab.providers[1].load_access_token(minted[0].access_token) is None
    assert (
        await lab.providers[1].load_refresh_token(lab.client, minted[0].refresh_token or "") is None
    )
    assert await lab.providers[1].load_authorization_code(lab.client, code.code) == code
    monkeypatch.setattr(provider.core, "issue", original)
    result = await provider.exchange_authorization_code(lab.client, code)
    assert await lab.providers[1].load_access_token(result.access_token) is not None


async def test_consent_decision_race_and_cross_user_disconnect(lab: Lab) -> None:
    intent = await lab.intent()
    view = await lab.providers[0].core.consent_view(intent, identity=lab.user, browser=_BROWSER)
    results = await asyncio.gather(
        *(
            provider.core.decide(
                intent, identity=lab.user, browser=_BROWSER, csrf=view.csrf, approve=True
            )
            for provider in lab.providers
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, str) for result in results) == 1
    assert sum(isinstance(result, NativeOAuthError) for result in results) == 1
    connections = await lab.providers[0].core.list_connections(lab.user)
    assert len(connections) == 1
    await lab.providers[1].core.disconnect(lab.other, connections[0].grant_id)
    assert await lab.providers[0].core.list_connections(lab.user) == connections
    await lab.providers[1].core.disconnect(lab.user, connections[0].grant_id)
    assert await lab.providers[0].core.list_connections(lab.user) == []


async def test_quotas_fail_closed_without_revoking_existing_connection(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = await lab.tokens()
    monkeypatch.setattr("app.mcp.native_core._MAX_USER_GRANTS", 1)
    lab.client = lab.client.model_copy(update={"client_id": "another-client"})
    request("register")
    await lab.providers[0].register_client(lab.client)
    intent = await lab.intent()
    core = lab.providers[0].core
    view = await core.consent_view(intent, identity=lab.user, browser=_BROWSER)
    with pytest.raises(NativeOAuthError):
        await core.decide(intent, identity=lab.user, browser=_BROWSER, csrf=view.csrf, approve=True)
    assert await lab.providers[1].load_access_token(token.access_token) is not None
    monkeypatch.setattr("app.mcp.native_state._CLIENT_STATE_LIMIT", 0)
    with pytest.raises(AuthorizeError):
        await lab.intent()


async def test_full_intent_quota_allows_existing_decision_and_rejects_new_intent(lab: Lab) -> None:
    core = lab.providers[0].core
    intent = await lab.intent()
    view = await core.consent_view(intent, identity=lab.user, browser=_BROWSER)
    async with lab.sessions.begin() as db:
        await db.execute(
            insert(McpNativeState).values(
                purpose="intent", expires_at=func.clock_timestamp() + timedelta(minutes=20)
            ),
            [
                {"key_digest": f"{index:064x}", "ciphertext": "synthetic-unread-quota-slot"}
                for index in range(999)
            ],
        )
    with pytest.raises(AuthorizeError):
        await lab.intent()
    denied = await core.decide(
        intent, identity=lab.user, browser=_BROWSER, csrf=view.csrf, approve=False
    )
    assert parse_qs(urlsplit(denied).query)["error"] == ["access_denied"]
    assert (
        await core.consent_status(intent, identity=lab.user, browser=_BROWSER)
    ).status == "denied"
    assert await core.list_connections(lab.user) == []


async def test_wrong_client_cannot_load_or_exchange_code(lab: Lab) -> None:
    code = await lab.code()
    other = lab.client.model_copy(update={"client_id": "another-client"})
    assert await lab.providers[1].load_authorization_code(other, code.code) is None
    with pytest.raises(TokenError):
        await lab.providers[1].exchange_authorization_code(other, code)
    assert await lab.providers[0].exchange_authorization_code(lab.client, code)


@pytest.mark.parametrize("approve", [True, False], ids=["approved", "denied"])
async def test_ambiguous_decision_status_is_terminal_without_reissuing(
    lab: Lab, approve: bool
) -> None:
    core = lab.providers[0].core
    intent = await lab.intent()
    view = await core.consent_view(intent, identity=lab.user, browser=_BROWSER)
    assert (
        await core.consent_status(intent, identity=lab.user, browser=_BROWSER)
    ).status == "pending"
    await core.decide(intent, identity=lab.user, browser=_BROWSER, csrf=view.csrf, approve=approve)
    status = await lab.providers[1].core.consent_status(intent, identity=lab.user, browser=_BROWSER)
    assert status.status == ("approved" if approve else "denied")
    assert status.restart_required is True
    assert (
        await core.consent_view(intent, identity=lab.user, browser=_BROWSER)
    ).status == status.status
    with pytest.raises(NativeOAuthError):
        await core.consent_status(intent, identity=lab.other, browser=_BROWSER)
    with pytest.raises(NativeOAuthError):
        await core.decide(intent, identity=lab.user, browser=_BROWSER, csrf=view.csrf, approve=True)
    async with lab.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(McpGrant)) == int(approve)


async def test_consumed_refresh_load_revokes_only_original_family(lab: Lab) -> None:
    original = await lab.tokens()
    assert original.refresh_token is not None
    loaded = await lab.providers[0].load_refresh_token(lab.client, original.refresh_token)
    assert loaded is not None
    rotated = await lab.providers[0].exchange_refresh_token(
        lab.client, loaded, [McpCapability.READ]
    )
    assert await lab.providers[1].load_access_token(rotated.access_token) is not None
    assert await lab.providers[1].load_refresh_token(lab.client, original.refresh_token) is None
    assert await lab.providers[0].load_access_token(rotated.access_token) is None
    replacement = await lab.tokens(transform=True)
    assert await lab.providers[1].load_refresh_token(lab.client, original.refresh_token) is None
    with pytest.raises(TokenError):
        await lab.providers[1].exchange_refresh_token(lab.client, loaded, [McpCapability.READ])
    assert await lab.providers[0].load_access_token(replacement.access_token) is not None


async def test_cancellation_propagates_and_rolls_back_native_issuance(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = lab.providers[0]
    code = await lab.code()
    original = provider.core.issue
    ready = asyncio.Event()
    minted: list[OAuthToken] = []

    async def interrupted_issue(
        db: AsyncSession, credential: NativeCredential, scopes: list[McpCapability]
    ) -> OAuthToken:
        minted.append(await original(db, credential, scopes))
        ready.set()
        await asyncio.Event().wait()
        pytest.fail("Cancelled issuance resumed")

    monkeypatch.setattr(provider.core, "issue", interrupted_issue)
    task = asyncio.create_task(provider.exchange_authorization_code(lab.client, code))
    try:
        await asyncio.wait_for(ready.wait(), timeout=10)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(minted) == 1
    assert await lab.providers[1].load_access_token(minted[0].access_token) is None
    assert (
        await lab.providers[1].load_refresh_token(lab.client, minted[0].refresh_token or "") is None
    )
    assert await lab.providers[1].load_authorization_code(lab.client, code.code) == code


async def test_issuer_and_resource_configuration_cannot_rebind_credentials(lab: Lab) -> None:
    token = await lab.tokens()
    original = lab.providers[0]
    other = NativeWikiOAuthProvider(
        core=NativeOAuthCore(
            original.core.operations,
            original.core.state,
            _RESOURCE,
            issuer="https://other.example.test",
        ),
        base_url="https://other.example.test",
        consent_url="https://wiki.example.test/mcp/consent",
        redirects=original.redirects,
        request_guard=require_native_oauth_request,
        registration_admission=PostgresRegistrationAdmission(lab.sessions).admit,
    )
    assert await other.load_access_token(token.access_token) is None
    with pytest.raises(NativeOAuthError):
        original.get_routes("/wrong-resource")
    assert original.get_routes("/mcp")
    assert original.issuer_responses_enabled is False


@pytest.mark.parametrize("history", ["revoked", "expired", "old-revision"])
async def test_historical_grants_do_not_exhaust_active_quota(
    lab: Lab, monkeypatch: pytest.MonkeyPatch, history: str
) -> None:
    await lab.tokens()
    async with lab.sessions.begin() as db:
        match history:
            case "revoked":
                await db.execute(update(McpGrant).values(revoked_at=func.clock_timestamp()))
            case "expired":
                await db.execute(update(McpGrant).values(expires_at=func.clock_timestamp()))
            case _:
                await db.execute(update(McpGrant).values(user_session_revision=1))
    lab.client = lab.client.model_copy(update={"client_id": "replacement-client"})
    request("register")
    await lab.providers[0].register_client(lab.client)
    monkeypatch.setattr("app.mcp.native_core._MAX_USER_GRANTS", 1)
    token = await lab.tokens()
    assert await lab.providers[1].load_access_token(token.access_token) is not None
    async with lab.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(McpGrant)) == 2


async def test_same_client_reconsent_does_not_use_another_quota_slot(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = await lab.tokens()
    monkeypatch.setattr("app.mcp.native_core._MAX_USER_GRANTS", 1)
    second = await lab.tokens(transform=True)
    assert await lab.providers[0].load_access_token(first.access_token) is None
    assert await lab.providers[1].load_access_token(second.access_token) is not None


async def test_expired_state_backlog_does_not_prevent_quota_recovery(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with lab.sessions.begin() as db:
        await db.execute(
            insert(McpNativeState).values(expires_at=func.clock_timestamp() - timedelta(days=1)),
            [
                {"purpose": "intent", "key_digest": f"{index:064x}", "ciphertext": "expired-unread"}
                for index in range(201)
            ],
        )
    monkeypatch.setattr("app.mcp.native_state._CLIENT_STATE_LIMIT", 1)
    assert await lab.intent()
    async with lab.sessions() as db:
        remaining = await db.scalar(
            select(func.count())
            .select_from(McpNativeState)
            .where(McpNativeState.expires_at <= func.clock_timestamp())
        )
        assert remaining is not None
        assert remaining > 0


async def client_expiry(lab: Lab) -> datetime:
    async with lab.sessions() as db:
        expiry = await db.scalar(
            select(McpNativeState.expires_at).where(McpNativeState.purpose == "client")
        )
        assert expiry is not None
        return expiry


async def test_failed_registration_consumes_independent_admission(lab: Lab) -> None:
    request("register")
    with pytest.raises(RegistrationError):
        await lab.providers[0].register_client(lab.client)
    async with lab.sessions() as db:
        history = await db.scalar(select(McpRegistrationAdmission.admitted_at))
        assert history is not None
        assert len(history) == 2
        assert (
            await db.scalar(
                select(func.count())
                .select_from(McpNativeState)
                .where(McpNativeState.purpose == "client")
            )
            == 1
        )


async def test_pending_registration_is_short_and_reads_denial_never_extend(lab: Lab) -> None:
    initial = await client_expiry(lab)
    async with lab.sessions() as db:
        assert (
            timedelta(minutes=29)
            < initial - await native_database_time(db)
            <= timedelta(minutes=30)
        )
    provider = lab.providers[0]
    assert await provider.get_client("client") is not None
    intent = await lab.intent()
    view = await provider.core.consent_view(intent, identity=lab.user, browser=_BROWSER)
    await provider.core.consent_status(intent, identity=lab.user, browser=_BROWSER)
    assert await client_expiry(lab) == initial
    await provider.core.decide(
        intent, identity=lab.user, browser=_BROWSER, csrf=view.csrf, approve=False
    )
    assert await client_expiry(lab) == initial
    async with lab.sessions.begin() as db:
        await db.execute(
            update(McpNativeState)
            .where(McpNativeState.purpose == "client")
            .values(expires_at=func.clock_timestamp())
        )
    assert await provider.get_client("client") is None
    with pytest.raises(AuthorizeError):
        await lab.intent()


async def test_approval_promotes_existing_client_at_capacity_and_never_shortens(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.mcp.native_state._CLIENT_STATE_LIMIT", 1)
    await lab.code()
    promoted = await client_expiry(lab)
    async with lab.sessions() as db:
        assert promoted == await db.scalar(select(McpGrant.expires_at))
    async with lab.providers[0].core.operations.operation("promotion-test") as db:
        await lab.providers[0].core.state.promote_client(
            db, "client", await native_database_time(db) + timedelta(hours=1)
        )
    assert await client_expiry(lab) == promoted


async def test_failed_approval_rolls_back_registration_promotion_and_grant(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    core = lab.providers[0].core
    initial = await client_expiry(lab)
    intent = await lab.intent()
    view = await core.consent_view(intent, identity=lab.user, browser=_BROWSER)
    original = core.state.promote_client

    async def fail_after_promotion(db: AsyncSession, key: str, expiry: datetime) -> None:
        await original(db, key, expiry)
        raise NativeOAuthError

    monkeypatch.setattr(core.state, "promote_client", fail_after_promotion)
    with pytest.raises(NativeOAuthError):
        await core.decide(intent, identity=lab.user, browser=_BROWSER, csrf=view.csrf, approve=True)
    assert await client_expiry(lab) == initial
    assert (
        await core.consent_status(intent, identity=lab.user, browser=_BROWSER)
    ).status == "pending"
    async with lab.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(McpGrant)) == 0
        assert (
            await db.scalar(
                select(func.count())
                .select_from(McpNativeState)
                .where(McpNativeState.purpose == "code")
            )
            == 0
        )
