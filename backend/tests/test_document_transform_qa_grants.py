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
    ConsumedQaGrant,
    IssuedQaGrant,
    QaGrantError,
    consume_qa_grant,
    issue_qa_grant,
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
        user_id=user.id,
        session_id=session.id,
        release_sha="a" * 40,
        source_folder_id=_SOURCE_FOLDER_ID,
        destination_folder_id=_DESTINATION_FOLDER_ID,
        mode=TransformMode.BOTH,
    )


async def _consume(
    db: AsyncSession,
    token: str,
    execution_name: str,
    *,
    user_email: str = "qa@example.test",
    release_sha: str = "a" * 40,
    source_folder_id: FileId = _SOURCE_FOLDER_ID,
    destination_folder_id: FileId = _DESTINATION_FOLDER_ID,
    mode: TransformMode = TransformMode.BOTH,
) -> ConsumedQaGrant:
    return await consume_qa_grant(
        db,
        token=token,
        execution_name=execution_name,
        user_email=user_email,
        release_sha=release_sha,
        source_folder_id=source_folder_id,
        destination_folder_id=destination_folder_id,
        mode=mode,
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
        ("user_email", "other@example.test"),
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
        "user_email": "qa@example.test",
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
                user_email=str(mismatch["user_email"]),
                release_sha=str(mismatch["release_sha"]),
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
            await issue_qa_grant(
                db,
                user_id=UserId(uuid.uuid7()),
                session_id=AuthSessionId(uuid.uuid7()),
                lifetime=lifetime,
                release_sha="a" * 40,
                source_folder_id=FileId("source-folder"),
                destination_folder_id=FileId("destination-folder"),
                mode=TransformMode.BOTH,
            )


async def test_invalid_binding_is_rejected_before_database_access() -> None:
    async with AsyncSession() as db:
        with pytest.raises(QaGrantError):
            await issue_qa_grant(
                db,
                user_id=UserId(uuid.uuid7()),
                session_id=AuthSessionId(uuid.uuid7()),
                release_sha="latest",
                source_folder_id=FileId("same"),
                destination_folder_id=FileId("same"),
                mode=TransformMode.BOTH,
            )
