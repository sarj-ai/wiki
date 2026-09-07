import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import uuid

from pydantic_settings import BaseSettings, SettingsConfigDict
import pytest
from sqlalchemy import Table
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from app.document_transform.qa_grants import (
    IssuedQaGrant,
    QaCapability,
    QaGrantError,
    consume_qa_grant,
    issue_qa_grant,
    renew_qa_capability,
)
from app.document_transform.schemas import TransformMode
from app.identifiers import (
    AuthSessionFamilyId,
    AuthSessionId,
    FileId,
    GoogleUserId,
    TokenId,
    UserId,
)
from app.models.auth_session import UserSession
from app.models.document_transform_qa_grant import DocumentTransformQaGrant
from app.models.user import User


_SOURCE_FOLDER_ID = FileId("source-folder")
_DESTINATION_FOLDER_ID = FileId("destination-folder")
_CONSUME_NONCE = uuid.UUID("11111111-1111-4111-8111-111111111111")
_RENEW_NONCE = uuid.UUID("22222222-2222-4222-8222-222222222222")
_DERIVATION_KEY = b"test-only-qa-capability-key-32!!"


class _QaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WIKI_MCP_QA_")
    database_url: str | None = None


@dataclass(frozen=True, slots=True)
class _Identity:
    user: User
    session: UserSession


@pytest.fixture
async def sessions() -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    dsn = _QaSettings().database_url
    if not dsn:
        pytest.skip("Requires disposable local PostgreSQL QA database")
    url = make_url(dsn)
    if (
        url.host not in {"localhost", "127.0.0.1", "::1"}
        or not (
            (url.database or "").startswith("wiki_mcp_qa") or url.database == "wiki_polish29_qa"
        )
        or url.drivername != "postgresql+asyncpg"
    ):
        pytest.fail("Only loopback PostgreSQL named wiki_mcp_qa* or wiki_polish29_qa is allowed")
    schema = "transform_qa_grant_" + uuid.uuid7().hex
    engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with engine.begin() as connection:
            await connection.execute(CreateSchema(schema))
            for model in (User, UserSession, DocumentTransformQaGrant):
                table = model.__table__
                assert isinstance(table, Table)
                await connection.run_sync(table.create)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with engine.begin() as connection:
            await connection.execute(DropSchema(schema, cascade=True))
        await engine.dispose()


async def _identity(db: AsyncSession) -> _Identity:
    user_id = UserId(uuid.uuid7())
    session_id = AuthSessionId(uuid.uuid7())
    user = User(
        id=user_id,
        google_id=GoogleUserId("qa-subject"),
        email="qa@example.test",
        encrypted_refresh_token="encrypted-test-placeholder",
    )
    db.add(user)
    await db.flush()
    session = UserSession(
        id=session_id,
        family_id=AuthSessionFamilyId(session_id),
        user_id=user_id,
        refresh_jti=TokenId(uuid.uuid7()),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db.add(session)
    await db.flush()
    return _Identity(user=user, session=session)


async def _issue(db: AsyncSession, user: User, session: UserSession) -> IssuedQaGrant:
    return await issue_qa_grant(
        db,
        issuer_user_id=user.id,
        user_id=user.id,
        session_id=session.id,
        release_sha="a" * 40,
        image_digest="sha256:" + "c" * 64,
        source_folder_id=_SOURCE_FOLDER_ID,
        destination_folder_id=_DESTINATION_FOLDER_ID,
        mode=TransformMode.BOTH,
    )


async def _consume(
    db: AsyncSession,
    token: str,
    execution_name: str,
    *,
    request_nonce: uuid.UUID = _CONSUME_NONCE,
    release_sha: str = "a" * 40,
    image_digest: str = "sha256:" + "c" * 64,
    source_folder_id: FileId = _SOURCE_FOLDER_ID,
    destination_folder_id: FileId = _DESTINATION_FOLDER_ID,
    mode: TransformMode = TransformMode.BOTH,
) -> QaCapability:
    return await consume_qa_grant(
        db,
        token=token,
        request_nonce=request_nonce,
        derivation_key=_DERIVATION_KEY,
        execution_name=execution_name,
        release_sha=release_sha,
        image_digest=image_digest,
        source_folder_id=source_folder_id,
        destination_folder_id=destination_folder_id,
        mode=mode,
    )


async def _renew(
    db: AsyncSession, token: str, *, request_nonce: uuid.UUID = _RENEW_NONCE
) -> QaCapability:
    return await renew_qa_capability(
        db,
        capability_token=token,
        request_nonce=request_nonce,
        derivation_key=_DERIVATION_KEY,
        execution_name="execution-a",
        release_sha="a" * 40,
        image_digest="sha256:" + "c" * 64,
        source_folder_id=_SOURCE_FOLDER_ID,
        destination_folder_id=_DESTINATION_FOLDER_ID,
        mode=TransformMode.BOTH,
    )


async def _invalid_issue(
    db: AsyncSession,
    *,
    lifetime: timedelta = timedelta(minutes=5),
    release_sha: str = "a" * 40,
    image_digest: str = "sha256:" + "c" * 64,
    source_folder_id: FileId = _SOURCE_FOLDER_ID,
    destination_folder_id: FileId = _DESTINATION_FOLDER_ID,
) -> IssuedQaGrant:
    return await issue_qa_grant(
        db,
        issuer_user_id=UserId(uuid.uuid7()),
        user_id=UserId(uuid.uuid7()),
        session_id=AuthSessionId(uuid.uuid7()),
        lifetime=lifetime,
        release_sha=release_sha,
        image_digest=image_digest,
        source_folder_id=source_folder_id,
        destination_folder_id=destination_folder_id,
        mode=TransformMode.BOTH,
    )


async def test_real_postgres_grant_is_exact_single_use(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions.begin() as db:
        identity = await _identity(db)
        issued = await _issue(db, identity.user, identity.session)

    async def consume(execution: str) -> bool:
        async with sessions.begin() as db:
            try:
                await _consume(db, issued.token, execution)
            except QaGrantError:
                return False
            return True

    outcomes = await asyncio.gather(consume("execution-a"), consume("execution-b"))
    assert sorted(outcomes) == [False, True]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("image_digest", "sha256:" + "d" * 64),
        ("release_sha", "b" * 40),
        ("source_folder_id", "other-source"),
        ("destination_folder_id", "other-destination"),
        ("mode", TransformMode.FORMAT),
    ],
)
async def test_real_postgres_mismatch_does_not_consume_grant(
    sessions: async_sessionmaker[AsyncSession], field: str, value: object
) -> None:
    async with sessions.begin() as db:
        identity = await _identity(db)
        issued = await _issue(db, identity.user, identity.session)
    mismatch: dict[str, object] = {
        "image_digest": "sha256:" + "c" * 64,
        "release_sha": "a" * 40,
        "source_folder_id": "source-folder",
        "destination_folder_id": "destination-folder",
        "mode": TransformMode.BOTH,
    }
    mismatch[field] = value
    async with sessions.begin() as db:
        with pytest.raises(QaGrantError):
            await _consume(
                db,
                issued.token,
                "wrong",
                release_sha=str(mismatch["release_sha"]),
                image_digest=str(mismatch["image_digest"]),
                source_folder_id=FileId(str(mismatch["source_folder_id"])),
                destination_folder_id=FileId(str(mismatch["destination_folder_id"])),
                mode=TransformMode(str(mismatch["mode"])),
            )
    async with sessions.begin() as db:
        consumed = await _consume(db, issued.token, "correct")
    assert consumed.user_id == identity.user.id


@pytest.mark.parametrize(
    "lifetime",
    [pytest.param(timedelta(0), id="zero"), pytest.param(timedelta(minutes=16), id="too-long")],
)
async def test_grant_lifetime_is_bounded_before_database_access(lifetime: timedelta) -> None:
    async with AsyncSession() as db:
        with pytest.raises(QaGrantError):
            await _invalid_issue(db, lifetime=lifetime)


async def test_invalid_binding_is_rejected_before_database_access() -> None:
    async with AsyncSession() as db:
        with pytest.raises(QaGrantError):
            await _invalid_issue(
                db,
                release_sha="latest",
                image_digest="latest",
                source_folder_id=FileId("same"),
                destination_folder_id=FileId("same"),
            )


async def test_real_postgres_renewal_rotates_capability_and_rechecks_token_version(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions.begin() as db:
        identity = await _identity(db)
        issued = await _issue(db, identity.user, identity.session)
    async with sessions.begin() as db:
        capability = await _consume(db, issued.token, "execution-a")
    async with sessions.begin() as db:
        renewed = await _renew(db, capability.token)
    assert renewed.token != capability.token
    async with sessions.begin() as db:
        replayed = await _renew(db, capability.token)
    assert replayed == renewed
    async with sessions.begin() as db:
        with pytest.raises(QaGrantError):
            await _renew(db, capability.token, request_nonce=uuid.uuid7())
        user = await db.get(User, identity.user.id)
        assert user is not None
        user.token_version += 1
    async with sessions.begin() as db:
        with pytest.raises(QaGrantError):
            await _renew(db, renewed.token)


async def test_real_postgres_consume_response_loss_replays_exact_capability(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions.begin() as db:
        identity = await _identity(db)
        issued = await _issue(db, identity.user, identity.session)
    async with sessions.begin() as db:
        first = await _consume(db, issued.token, "execution-a")
    async with sessions.begin() as db:
        replay = await _consume(db, issued.token, "execution-a")
    assert replay == first


async def test_real_postgres_concurrent_identical_consume_is_idempotent(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions.begin() as db:
        identity = await _identity(db)
        issued = await _issue(db, identity.user, identity.session)

    async def consume() -> QaCapability:
        async with sessions.begin() as db:
            return await _consume(db, issued.token, "execution-a")

    first, second = await asyncio.gather(consume(), consume())
    assert first == second


async def test_real_postgres_concurrent_identical_renewal_is_idempotent(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions.begin() as db:
        identity = await _identity(db)
        issued = await _issue(db, identity.user, identity.session)
    async with sessions.begin() as db:
        capability = await _consume(db, issued.token, "execution-a")

    async def renew() -> QaCapability:
        async with sessions.begin() as db:
            return await _renew(db, capability.token)

    first, second = await asyncio.gather(renew(), renew())
    assert first == second
