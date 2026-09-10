import asyncio
from collections.abc import AsyncGenerator
from dataclasses import replace
from datetime import timedelta
import uuid

from cryptography.fernet import Fernet
from pydantic_settings import BaseSettings, SettingsConfigDict
import pytest
from sqlalchemy import Table, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from app.identifiers import GoogleUserId, UserId
from app.mcp.context import McpAuthorizationError
from app.mcp.grants import (
    SignedGrantBinding,
    enroll_approved_client,
    revoke_client,
    validate_live_grant,
)
from app.mcp.identity import McpClientId
from app.mcp.protection import OAuthStateProtector
from app.mcp.schemas import McpCapability
from app.mcp.storage import PostgresOAuthStorage
from app.models.mcp_grant import McpGrant, McpGrantId
from app.models.mcp_oauth import MCPOAuthEntry
from app.models.user import User


class _QASettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WIKI_MCP_QA_")
    database_url: str | None = None


@pytest.fixture
async def sessions() -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    dsn = _QASettings().database_url
    if not dsn:
        pytest.skip("Requires disposable MCP PostgreSQL QA database")
    url = make_url(dsn)
    if (
        url.host not in {"localhost", "127.0.0.1", "::1"}
        or not (
            (url.database or "").startswith("wiki_mcp_qa") or url.database == "wiki_polish29_qa"
        )
        or url.drivername != "postgresql+asyncpg"
    ):
        pytest.fail("Only loopback PostgreSQL named wiki_mcp_qa* or wiki_polish29_qa is allowed")
    schema = "grant_test_" + uuid.uuid7().hex
    engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with engine.begin() as connection:
            await connection.execute(CreateSchema(schema))
            for model in (User, McpGrant, MCPOAuthEntry):
                table = model.__table__
                assert isinstance(table, Table)
                await connection.run_sync(table.create)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with engine.begin() as connection:
            await connection.execute(DropSchema(schema, cascade=True))
        await engine.dispose()


async def _user(db: AsyncSession, subject: str) -> User:
    user = User(
        id=UserId(uuid.uuid7()),
        google_id=GoogleUserId(subject),
        email=f"{subject}@example.test",
        encrypted_refresh_token="encrypted-test-placeholder",
    )
    db.add(user)
    await db.flush()
    return user


def _binding(grant: McpGrant, user: User) -> SignedGrantBinding:
    return SignedGrantBinding(
        grant_id=grant.id,
        client_id=McpClientId(grant.client_id),
        issued_at=int(grant.created_at.timestamp()),
        google_subject=user.google_id,
    )


@pytest.mark.parametrize("outcome", ["commit", "rollback", "cancel", "deadline"])
async def test_real_postgres_grant_and_oauth_state_share_one_transaction(
    sessions: async_sessionmaker[AsyncSession], outcome: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit = outcome == "commit"
    protector = OAuthStateProtector(
        (Fernet.generate_key().decode(),), lock_key=Fernet.generate_key().decode()
    )
    writer = PostgresOAuthStorage(sessions, protector)
    observer = PostgresOAuthStorage(sessions, protector)
    collection = "mcp-authorization-codes"
    async with sessions.begin() as db:
        user = await _user(db, "atomic")
        previous = await enroll_approved_client(
            db, approving_user_id=user.id, client_id=McpClientId("client")
        )
        previous_binding = _binding(previous, user)
    await writer.put("code", {"approved": True}, collection=collection, ttl=60)
    replacements: list[McpGrantId] = []
    ready = asyncio.Event()
    if outcome == "deadline":
        monkeypatch.setattr("app.mcp.storage._OPERATION_TIMEOUT", timedelta(seconds=2))

    async def exchange() -> None:
        async with writer.operation("connection:atomic:client") as db:
            async with writer.operation("connection:atomic:client") as nested:
                assert nested is db
                replacement = await enroll_approved_client(
                    nested,
                    approving_user_id=user.id,
                    client_id=McpClientId("client"),
                    transform_approved=True,
                )
                replacements.append(replacement.id)
                assert await writer.delete("code", collection=collection)
                await writer.put(
                    "new-token",
                    {"grant": str(replacement.id)},
                    collection="mcp-jti-mappings",
                    ttl=60,
                )
            # Independent instance/session cannot observe either uncommitted write.
            assert await observer.get("code", collection=collection) == {"approved": True}
            assert await observer.get("new-token", collection="mcp-jti-mappings") is None
            async with sessions() as reader:
                assert await reader.get(McpGrant, replacement.id) is None
                await validate_live_grant(reader, previous_binding)
            ready.set()
            if outcome in {"cancel", "deadline"}:
                await asyncio.Event().wait()
            if outcome == "rollback":
                message = "abort exchange"
                raise RuntimeError(message)

    if outcome == "cancel":
        task = asyncio.create_task(exchange())
        try:
            await asyncio.wait_for(ready.wait(), timeout=10)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    elif outcome == "deadline":
        with pytest.raises(TimeoutError):
            await exchange()
        assert ready.is_set(), "Deadline must interrupt after all transaction writes"
    elif commit:
        await exchange()
    else:
        with pytest.raises(RuntimeError, match="abort exchange"):
            await exchange()
    assert len(replacements) == 1
    replacement_id = replacements[0]
    async with sessions() as reader:
        saved = await reader.get(McpGrant, replacement_id)
        if commit:
            assert saved is not None
            await validate_live_grant(
                reader, _binding(saved, user), required_capability=McpCapability.TRANSFORM
            )
            with pytest.raises(McpAuthorizationError):
                await validate_live_grant(reader, previous_binding)
        else:
            assert saved is None
            await validate_live_grant(reader, previous_binding)
    assert await observer.get("code", collection=collection) == (
        None if commit else {"approved": True}
    )
    assert await observer.get("new-token", collection="mcp-jti-mappings") == (
        {"grant": str(replacement_id)} if commit else None
    )


async def test_real_postgres_client_identity_revocation_and_generation(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions.begin() as db:
        user = await _user(db, "alice")
        other = await _user(db, "bob")
        read = await enroll_approved_client(
            db, approving_user_id=user.id, client_id=McpClientId("read")
        )
        write = await enroll_approved_client(
            db,
            approving_user_id=user.id,
            client_id=McpClientId("write"),
            transform_approved=True,
        )
        read_binding, write_binding = _binding(read, user), _binding(write, user)
    async with sessions() as db:
        principal = await validate_live_grant(db, read_binding)
        assert principal.capabilities == {McpCapability.READ}
        with pytest.raises(McpAuthorizationError):
            await validate_live_grant(db, read_binding, required_capability=McpCapability.TRANSFORM)
        for forged in (
            replace(read_binding, client_id=McpClientId("write")),
            replace(read_binding, google_subject=other.google_id),
            replace(read_binding, grant_id=McpGrantId(uuid.uuid7())),
            replace(read_binding, issued_at=read_binding.issued_at - 1),
            replace(read_binding, issued_at=read_binding.issued_at + 3600),
        ):
            with pytest.raises(McpAuthorizationError):
                await validate_live_grant(db, forged)
    async with sessions.begin() as db:
        elevated = await enroll_approved_client(
            db,
            approving_user_id=user.id,
            client_id=McpClientId("read"),
            transform_approved=True,
        )
        elevated_binding = _binding(elevated, user)
        assert elevated.id != read.id
    async with sessions() as db:
        with pytest.raises(McpAuthorizationError):
            await validate_live_grant(db, read_binding)
        await validate_live_grant(db, elevated_binding, required_capability=McpCapability.TRANSFORM)
    async with sessions.begin() as db:
        await revoke_client(db, user_id=user.id, client_id=McpClientId("read"))
    async with sessions() as db:
        with pytest.raises(McpAuthorizationError):
            await validate_live_grant(db, elevated_binding)
        await validate_live_grant(db, write_binding, required_capability=McpCapability.TRANSFORM)
        live_user = await db.get(User, user.id)
        assert live_user is not None
        assert live_user.encrypted_refresh_token
        assert live_user.token_version == user.token_version
    async with sessions.begin() as db:
        await db.execute(update(User).where(User.id == user.id).values(token_version=1))
    async with sessions() as db:
        with pytest.raises(McpAuthorizationError):
            await validate_live_grant(db, write_binding)
    async with sessions.begin() as db:
        reconnected = await enroll_approved_client(
            db,
            approving_user_id=user.id,
            client_id=McpClientId("write"),
        )
        reconnected_binding = _binding(reconnected, user)
    async with sessions() as db:
        with pytest.raises(McpAuthorizationError):
            await validate_live_grant(db, write_binding)
        assert (await validate_live_grant(db, reconnected_binding)).user_session_revision == 1


@pytest.mark.parametrize(
    "failure", ["expired", "credential_removed", "deleted_grant", "deleted_user"]
)
async def test_real_postgres_live_denials_do_not_autocreate_grants(
    sessions: async_sessionmaker[AsyncSession],
    failure: str,
) -> None:
    async with sessions.begin() as db:
        user = await _user(db, "alice")
        grant = await enroll_approved_client(
            db, approving_user_id=user.id, client_id=McpClientId("one")
        )
        binding = _binding(grant, user)
    async with sessions() as reader:
        await validate_live_grant(reader, binding)
        async with sessions.begin() as writer:
            match failure:
                case "expired":
                    await writer.execute(update(McpGrant).values(expires_at=grant.created_at))
                case "credential_removed":
                    await writer.execute(update(User).values(encrypted_refresh_token=None))
                case _:
                    target = await writer.get(
                        McpGrant if failure == "deleted_grant" else User,
                        grant.id if failure == "deleted_grant" else user.id,
                    )
                    assert target is not None
                    await writer.delete(target)
        with pytest.raises(McpAuthorizationError):
            await validate_live_grant(reader, binding)
        rows = list(await reader.scalars(select(McpGrant)))
        assert len(rows) == (0 if failure.startswith("deleted") else 1)


@pytest.mark.parametrize(
    "lifetime",
    [timedelta(0), timedelta(days=-1), timedelta(days=31)],
    ids=["zero", "negative", "over_maximum"],
)
async def test_enrollment_rejects_unbounded_lifetime(lifetime: timedelta) -> None:
    async with AsyncSession() as db:
        with pytest.raises(McpAuthorizationError):
            await enroll_approved_client(
                db,
                approving_user_id=UserId(uuid.uuid7()),
                client_id=McpClientId("client"),
                lifetime=lifetime,
            )


async def test_real_postgres_concurrent_enrollment_keeps_only_newest_generation(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions.begin() as db:
        user = await _user(db, "alice")

    async def enroll() -> SignedGrantBinding:
        async with sessions.begin() as db:
            grant = await enroll_approved_client(
                db,
                approving_user_id=user.id,
                client_id=McpClientId("same-client"),
            )
            return _binding(grant, user)

    bindings = await asyncio.gather(enroll(), enroll())
    assert bindings[0].grant_id != bindings[1].grant_id
    async with sessions() as db:
        rows = list(await db.scalars(select(McpGrant)))
        assert len(rows) == 2
        active = [grant for grant in rows if grant.revoked_at is None]
        assert len(active) == 1
        for binding in bindings:
            if binding.grant_id == active[0].id:
                await validate_live_grant(db, binding)
            else:
                with pytest.raises(McpAuthorizationError):
                    await validate_live_grant(db, binding)
