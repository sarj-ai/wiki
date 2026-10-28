import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import uuid

from pydantic_settings import BaseSettings, SettingsConfigDict
import pytest
from sqlalchemy import Table, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from app.document_transform.qa_grants import (
    IssuedQaGrant,
    QaAuthority,
    QaCapability,
    QaGrantError,
    authorize_qa_capability,
    bind_qa_bootstrap_execution,
    bind_qa_canary_submission,
    bootstrap_attested_qa_execution,
    bootstrap_qa_run,
    consume_qa_grant,
    delete_expired_qa_grants,
    fail_qa_bootstrap_launch,
    issue_qa_grant,
    load_qa_campaign_authority_snapshot,
    load_qa_canary_submission,
    prepare_idempotent_qa_launch,
    prepare_qa_bootstrap_run,
    renew_qa_capability,
)
from app.document_transform.schemas import TransformMode
from app.files.schemas import FileRename
from app.identifiers import (
    AuthSessionFamilyId,
    AuthSessionId,
    FileId,
    GoogleUserId,
    IdempotencyKey,
    QaPlanId,
    QaRunId,
    TaskId,
    TokenId,
    UserId,
)
from app.models.auth_session import UserSession
from app.models.document_transform_qa_grant import DocumentTransformQaGrant
from app.models.document_transform_qa_run import DocumentTransformQaRun
from app.models.task import Task
from app.models.task_replay_key import TaskReplayKey
from app.models.user import User
from app.tasks.schemas import TaskKind, TaskStatus
from app.tasks.service import PostgresTaskService


_SOURCE_FOLDER_ID = FileId("source-folder")
_DESTINATION_FOLDER_ID = FileId("destination-folder")
_CONSUME_NONCE = uuid.UUID("11111111-1111-4111-8111-111111111111")
_RENEW_NONCE = uuid.UUID("22222222-2222-4222-8222-222222222222")
_DERIVATION_KEY = b"test-only-qa-capability-key-32!!"
_EXECUTION_UID = uuid.UUID("12345678-1234-4234-8234-123456789abc")
_EXECUTION_JOB = "projects/test-project/locations/me-central2/jobs/qa"


class _QaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WIKI_MCP_QA_")
    database_url: str | None = None


@dataclass(frozen=True, slots=True)
class _Identity:
    user: User
    session: UserSession


@dataclass(frozen=True, slots=True)
class _PreparedBoundRun:
    issued: IssuedQaGrant
    run_id: QaRunId


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
            for model in (
                User,
                UserSession,
                DocumentTransformQaGrant,
                Task,
                TaskReplayKey,
                DocumentTransformQaRun,
            ):
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


async def _prepared_bound_run(
    db: AsyncSession,
    user: User,
    session: UserSession,
    *,
    execution_name: str = "execution-a",
) -> _PreparedBoundRun:
    issued = await _issue(db, user, session)
    prepared = await prepare_qa_bootstrap_run(
        db,
        grant_id=issued.grant_id,
        release_sha="a" * 40,
        image_digest="sha256:" + "c" * 64,
        source_folder_id=_SOURCE_FOLDER_ID,
        destination_folder_id=_DESTINATION_FOLDER_ID,
        mode=TransformMode.BOTH,
    )
    run = await db.get(DocumentTransformQaRun, prepared.run_id)
    assert run is not None
    claimed_at = datetime.now(UTC)
    run.launch_idempotency_key = uuid.uuid7()
    run.launch_claimed_at = claimed_at
    run.launch_lease_expires_at = claimed_at + timedelta(minutes=1)
    run.state = "launch_claimed"
    await _bind_launch(db, prepared.run_id, execution_name=execution_name)
    return _PreparedBoundRun(issued, prepared.run_id)


async def _bind_launch(
    db: AsyncSession,
    run_id: QaRunId,
    *,
    execution_name: str = "execution-a",
    execution_uid: uuid.UUID = _EXECUTION_UID,
    execution_job_name: str = _EXECUTION_JOB,
) -> None:
    await bind_qa_bootstrap_execution(
        db,
        run_id=run_id,
        execution_name=execution_name,
        execution_uid=execution_uid,
        execution_job_name=execution_job_name,
        release_sha="a" * 40,
        image_digest="sha256:" + "c" * 64,
        source_folder_id=_SOURCE_FOLDER_ID,
        destination_folder_id=_DESTINATION_FOLDER_ID,
        mode=TransformMode.BOTH,
    )


async def _prepare_launch(
    db: AsyncSession,
    user: User,
    session: UserSession,
    *,
    idempotency_key: uuid.UUID,
):
    return await prepare_idempotent_qa_launch(
        db,
        idempotency_key=IdempotencyKey(idempotency_key),
        issuer_user_id=user.id,
        user_id=user.id,
        session_id=session.id,
        release_sha="a" * 40,
        image_digest="sha256:" + "c" * 64,
        source_folder_id=_SOURCE_FOLDER_ID,
        destination_folder_id=_DESTINATION_FOLDER_ID,
        mode=TransformMode.BOTH,
    )


async def _bootstrap(
    db: AsyncSession,
    run_id: QaRunId,
    *,
    execution_name: str = "execution-a",
    request_nonce: uuid.UUID = _CONSUME_NONCE,
    release_sha: str = "a" * 40,
    image_digest: str = "sha256:" + "c" * 64,
    source_folder_id: FileId = _SOURCE_FOLDER_ID,
    destination_folder_id: FileId = _DESTINATION_FOLDER_ID,
    mode: TransformMode = TransformMode.BOTH,
) -> QaCapability:
    return await bootstrap_qa_run(
        db,
        run_id=run_id,
        execution_name=execution_name,
        request_nonce=request_nonce,
        derivation_key=_DERIVATION_KEY,
        release_sha=release_sha,
        image_digest=image_digest,
        source_folder_id=source_folder_id,
        destination_folder_id=destination_folder_id,
        mode=mode,
    )


async def _renew(
    db: AsyncSession,
    token: str,
    *,
    request_nonce: uuid.UUID = _RENEW_NONCE,
    require_submitted_task: bool = False,
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
        require_submitted_task=require_submitted_task,
    )


def _submission_task(*, user_id: UserId, plan_id: QaPlanId, task_id: TaskId) -> Task:
    now = datetime.now(UTC)
    return Task(
        id=task_id,
        owner_user_id=user_id,
        kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
        status=TaskStatus.PENDING,
        idempotency_key=IdempotencyKey(plan_id),
        request_hash="d" * 64,
        payload={},
        attempt_count=0,
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )


async def _authorize(
    db: AsyncSession, token: str, *, execution_name: str = "execution-a"
) -> QaAuthority:
    return await authorize_qa_capability(
        db,
        capability_token=token,
        execution_name=execution_name,
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


async def test_real_postgres_secretless_bootstrap_replays_only_same_execution_and_nonce(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions.begin() as db:
        identity = await _identity(db)
        prepared = await _prepared_bound_run(db, identity.user, identity.session)
        issued, run_id = prepared.issued, prepared.run_id
    async with sessions.begin() as db:
        first = await _bootstrap(db, run_id)
    async with sessions.begin() as db:
        replay = await _bootstrap(db, run_id)
    assert replay == first
    async with sessions.begin() as db:
        with pytest.raises(QaGrantError):
            await _bootstrap(db, run_id, request_nonce=uuid.uuid7())
        with pytest.raises(QaGrantError):
            await _bootstrap(db, run_id, execution_name="execution-b")
        with pytest.raises(QaGrantError):
            await _consume(db, issued.token, "execution-a")
        authority = await _authorize(db, first.token)
    assert authority.user_id == identity.user.id
    async with sessions.begin() as db:
        renewed = await _renew(db, first.token)
    assert renewed.token != first.token


async def test_real_postgres_concurrent_secretless_bootstrap_has_one_exact_winner(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions.begin() as db:
        identity = await _identity(db)
        run_id = (await _prepared_bound_run(db, identity.user, identity.session)).run_id
    nonces = (_CONSUME_NONCE, _RENEW_NONCE)

    async def bootstrap(nonce: uuid.UUID) -> bool:
        async with sessions.begin() as db:
            try:
                await _bootstrap(db, run_id, request_nonce=nonce)
            except QaGrantError:
                return False
            return True

    outcomes = await asyncio.gather(*(bootstrap(nonce) for nonce in nonces))
    assert sorted(outcomes) == [False, True]


async def test_real_postgres_secretless_bootstrap_persists_only_digests(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions.begin() as db:
        identity = await _identity(db)
        prepared = await _prepared_bound_run(db, identity.user, identity.session)
        issued, run_id = prepared.issued, prepared.run_id
    async with sessions.begin() as db:
        capability = await _bootstrap(db, run_id)
    async with sessions.begin() as db:
        grant = await db.get(DocumentTransformQaGrant, issued.grant_id)
        run = await db.get(DocumentTransformQaRun, run_id)
        assert grant is not None
        assert run is not None
        expected = hashlib.sha256(capability.token.encode()).digest()
        assert grant.capability_digest == run.bootstrap_capability_digest == expected
        assert issued.token.encode() not in bytes(grant.token_digest)
        assert capability.token.encode() not in bytes(expected)
        assert run.bootstrap_request_nonce == _CONSUME_NONCE
        assert run.state == "bootstrapped"


async def test_real_postgres_campaign_snapshot_is_grant_bound_and_survives_renewal(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions.begin() as db:
        identity = await _identity(db)
        prepared = await _prepared_bound_run(db, identity.user, identity.session)
        capability = await _bootstrap(db, prepared.run_id)
        authority = await _authorize(db, capability.token)
        first = await load_qa_campaign_authority_snapshot(
            db,
            authority=authority,
            for_update=True,
        )
    assert first.run_id == prepared.run_id
    assert first.grant_id == prepared.issued.grant_id
    assert first.user_id == identity.user.id
    assert first.execution_name == "execution-a"
    assert first.execution_uid == _EXECUTION_UID
    assert first.execution_job_name == _EXECUTION_JOB
    assert first.bootstrap_capability_digest == hashlib.sha256(capability.token.encode()).digest()

    async with sessions.begin() as db:
        renewed = await _renew(db, capability.token)
        renewed_authority = await _authorize(db, renewed.token)
        replay = await load_qa_campaign_authority_snapshot(
            db,
            authority=renewed_authority,
        )
    assert replay == first


async def test_real_postgres_submission_binding_is_atomic_immutable_and_replayable(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    plan_id = QaPlanId(uuid.uuid7())
    task_id = TaskId(uuid.uuid7())
    async with sessions.begin() as db:
        identity = await _identity(db)
        prepared = await _prepared_bound_run(db, identity.user, identity.session)
        capability = await _bootstrap(db, prepared.run_id)
        authority = await _authorize(db, capability.token)
        db.add(_submission_task(user_id=identity.user.id, plan_id=plan_id, task_id=task_id))
        await db.flush()
        first = await bind_qa_canary_submission(
            db,
            authority=authority,
            plan_id=plan_id,
            task_id=task_id,
        )
        replay = await bind_qa_canary_submission(
            db,
            authority=authority,
            plan_id=plan_id,
            task_id=task_id,
        )
        assert replay == first
    async with sessions.begin() as db:
        authority = await _authorize(db, capability.token)
        loaded = await load_qa_canary_submission(db, authority=authority)
        run = await db.get(DocumentTransformQaRun, prepared.run_id)
        assert loaded == first
        assert run is not None
        assert run.submitted_plan_id == plan_id
        assert run.submitted_task_id == task_id
        assert run.submitted_at is not None
        with pytest.raises(QaGrantError):
            await bind_qa_canary_submission(
                db,
                authority=authority,
                plan_id=QaPlanId(uuid.uuid7()),
                task_id=task_id,
            )


async def test_bound_task_snapshot_does_not_block_expiry_cleanup_or_unrelated_admission(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    plan_id = QaPlanId(uuid.uuid7())
    task_id = TaskId(uuid.uuid7())
    async with sessions.begin() as db:
        identity = await _identity(db)
        prepared = await _prepared_bound_run(db, identity.user, identity.session)
        capability = await _bootstrap(db, prepared.run_id)
        authority = await _authorize(db, capability.token)
        task = _submission_task(user_id=identity.user.id, plan_id=plan_id, task_id=task_id)
        db.add(task)
        await db.flush()
        await bind_qa_canary_submission(
            db,
            authority=authority,
            plan_id=plan_id,
            task_id=task_id,
        )
        expired_at = datetime.now(UTC) - timedelta(seconds=1)
        task.created_at = expired_at - timedelta(hours=1)
        task.expires_at = expired_at

    unrelated_key = IdempotencyKey(uuid.uuid7())
    async with sessions() as db:
        admitted = await PostgresTaskService(db=db).create_or_replay(
            kind=TaskKind.FILE_SMART_CREATE,
            owner_user_id=identity.user.id,
            idempotency_key=unrelated_key,
            payload=FileRename(name="Unrelated admission"),
        )
        assert admitted.created is True

    async with sessions.begin() as db:
        assert await db.get(Task, task_id) is None
        run = await db.get(DocumentTransformQaRun, prepared.run_id)
        assert run is not None
        assert run.submitted_plan_id == plan_id
        assert run.submitted_task_id == task_id
        assert await db.get(Task, admitted.task.id) is not None


async def test_launcher_renewal_requires_the_exact_bound_submission_task(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    plan_id = QaPlanId(uuid.uuid7())
    task_id = TaskId(uuid.uuid7())
    async with sessions.begin() as db:
        identity = await _identity(db)
        prepared = await _prepared_bound_run(db, identity.user, identity.session)
        capability = await _bootstrap(db, prepared.run_id)
    async with sessions.begin() as db:
        with pytest.raises(QaGrantError):
            await _renew(db, capability.token, require_submitted_task=True)
    async with sessions.begin() as db:
        authority = await _authorize(db, capability.token)
        db.add(_submission_task(user_id=identity.user.id, plan_id=plan_id, task_id=task_id))
        await db.flush()
        await bind_qa_canary_submission(
            db,
            authority=authority,
            plan_id=plan_id,
            task_id=task_id,
        )
    async with sessions.begin() as db:
        renewed = await _renew(db, capability.token, require_submitted_task=True)
    assert renewed.token != capability.token


async def test_real_postgres_secretless_bootstrap_state_is_database_atomic(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions.begin() as db:
        identity = await _identity(db)
        run_id = (await _prepared_bound_run(db, identity.user, identity.session)).run_id

    async def violate_atomic_state() -> None:
        async with sessions.begin() as db:
            run = await db.get(DocumentTransformQaRun, run_id)
            assert run is not None
            run.state = "bootstrapped"

    with pytest.raises(IntegrityError):
        await violate_atomic_state()


async def test_real_postgres_distinct_idempotency_keys_cannot_create_two_active_launches(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions.begin() as db:
        identity = await _identity(db)

    async def prepare(key: uuid.UUID) -> bool:
        try:
            async with sessions.begin() as db:
                await _prepare_launch(db, identity.user, identity.session, idempotency_key=key)
        except IntegrityError:
            return False
        return True

    outcomes = await asyncio.gather(prepare(uuid.uuid7()), prepare(uuid.uuid7()))
    assert sorted(outcomes) == [False, True]


async def test_real_postgres_same_idempotency_key_has_one_durable_launch_claimer(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions.begin() as db:
        identity = await _identity(db)
    key = uuid.uuid7()

    async def prepare():
        async with sessions.begin() as db:
            return await _prepare_launch(db, identity.user, identity.session, idempotency_key=key)

    first, second = await asyncio.gather(prepare(), prepare())
    assert first.run_id == second.run_id
    assert first.state == second.state == "launch_claimed"
    assert sorted((first.owns_launch_claim, second.owns_launch_claim)) == [False, True]


async def test_real_postgres_expired_launch_claim_fails_closed_without_relaunch(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions.begin() as db:
        identity = await _identity(db)
        key = uuid.uuid7()
        claimed = await _prepare_launch(db, identity.user, identity.session, idempotency_key=key)
    async with sessions.begin() as db:
        run = await db.get(DocumentTransformQaRun, claimed.run_id)
        assert run is not None
        run.launch_lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    async with sessions.begin() as db:
        replay = await _prepare_launch(db, identity.user, identity.session, idempotency_key=key)
    assert replay.run_id == claimed.run_id
    assert replay.state == "launch_failed"
    assert replay.owns_launch_claim is False
    async with sessions.begin() as db:
        with pytest.raises(IntegrityError):
            await _prepare_launch(
                db,
                identity.user,
                identity.session,
                idempotency_key=uuid.uuid7(),
            )


async def test_real_postgres_failed_orphan_blocks_every_new_launch_key(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions.begin() as db:
        identity = await _identity(db)
        claimed = await _prepare_launch(
            db, identity.user, identity.session, idempotency_key=uuid.uuid7()
        )
    async with sessions.begin() as db:
        await fail_qa_bootstrap_launch(db, run_id=claimed.run_id)
    async with sessions.begin() as db:
        with pytest.raises(IntegrityError):
            await _prepare_launch(
                db,
                identity.user,
                identity.session,
                idempotency_key=uuid.uuid7(),
            )


async def test_real_postgres_unclaimed_legacy_run_cannot_bind_launcher_execution(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions.begin() as db:
        identity = await _identity(db)
        issued = await _issue(db, identity.user, identity.session)
        prepared = await prepare_qa_bootstrap_run(
            db,
            grant_id=issued.grant_id,
            release_sha="a" * 40,
            image_digest="sha256:" + "c" * 64,
            source_folder_id=_SOURCE_FOLDER_ID,
            destination_folder_id=_DESTINATION_FOLDER_ID,
            mode=TransformMode.BOTH,
        )
        with pytest.raises(QaGrantError):
            await _bind_launch(db, prepared.run_id)


async def test_real_postgres_execution_attestation_is_first_binding_wins(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    job = "projects/test-project/locations/me-central2/jobs/qa"
    execution = f"{job}/executions/one"
    uid = uuid.UUID("12345678-1234-4234-8234-123456789abc")
    async with sessions.begin() as db:
        identity = await _identity(db)
        prepared = await _prepare_launch(
            db, identity.user, identity.session, idempotency_key=uuid.uuid7()
        )
    async with sessions.begin() as db:
        await _bind_launch(
            db,
            prepared.run_id,
            execution_name=execution,
            execution_uid=uid,
            execution_job_name=job,
        )
    async with sessions.begin() as db:
        with pytest.raises(QaGrantError):
            await _bind_launch(
                db,
                prepared.run_id,
                execution_name=execution.replace("one", "two"),
                execution_uid=uuid.UUID("22345678-1234-4234-8234-123456789abc"),
                execution_job_name=job,
            )


@pytest.mark.parametrize("spoof", ["uid", "job"])
async def test_real_postgres_bootstrap_rejects_spoofed_server_attestation(
    sessions: async_sessionmaker[AsyncSession], spoof: str
) -> None:
    job = "projects/test-project/locations/me-central2/jobs/qa"
    uid = uuid.UUID("12345678-1234-4234-8234-123456789abc")
    async with sessions.begin() as db:
        identity = await _identity(db)
        prepared = await _prepare_launch(
            db, identity.user, identity.session, idempotency_key=uuid.uuid7()
        )
        await _bind_launch(
            db,
            prepared.run_id,
            execution_name=f"{job}/executions/one",
            execution_uid=uid,
            execution_job_name=job,
        )
    async with sessions.begin() as db:
        with pytest.raises(QaGrantError):
            await bootstrap_attested_qa_execution(
                db,
                execution_uid=(uuid.uuid7() if spoof == "uid" else uid),
                execution_name=f"{job}/executions/one",
                execution_job_name=(f"{job}-hostile" if spoof == "job" else job),
                request_nonce=_CONSUME_NONCE,
                derivation_key=_DERIVATION_KEY,
                release_sha="a" * 40,
                image_digest="sha256:" + "c" * 64,
            )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("execution_name", "execution-b"),
        ("image_digest", "sha256:" + "d" * 64),
        ("release_sha", "b" * 40),
        ("source_folder_id", "other-source"),
        ("destination_folder_id", "other-destination"),
        ("mode", TransformMode.FORMAT),
    ],
)
async def test_real_postgres_secretless_bootstrap_rejects_every_binding_mismatch(
    sessions: async_sessionmaker[AsyncSession], field: str, value: object
) -> None:
    async with sessions.begin() as db:
        identity = await _identity(db)
        run_id = (await _prepared_bound_run(db, identity.user, identity.session)).run_id
    arguments: dict[str, object] = {
        "execution_name": "execution-a",
        "image_digest": "sha256:" + "c" * 64,
        "release_sha": "a" * 40,
        "source_folder_id": _SOURCE_FOLDER_ID,
        "destination_folder_id": _DESTINATION_FOLDER_ID,
        "mode": TransformMode.BOTH,
    }
    arguments[field] = value
    async with sessions.begin() as db:
        with pytest.raises(QaGrantError):
            await _bootstrap(
                db,
                run_id,
                execution_name=str(arguments["execution_name"]),
                release_sha=str(arguments["release_sha"]),
                image_digest=str(arguments["image_digest"]),
                source_folder_id=FileId(str(arguments["source_folder_id"])),
                destination_folder_id=FileId(str(arguments["destination_folder_id"])),
                mode=TransformMode(str(arguments["mode"])),
            )
    async with sessions.begin() as db:
        capability = await _bootstrap(db, run_id)
    assert capability.user_id == identity.user.id


@pytest.mark.parametrize("inactive", ["grant-expired", "session-expired", "revoked", "revision"])
async def test_real_postgres_secretless_bootstrap_rechecks_current_identity_and_expiry(
    sessions: async_sessionmaker[AsyncSession], inactive: str
) -> None:
    async with sessions.begin() as db:
        identity = await _identity(db)
        prepared = await _prepared_bound_run(db, identity.user, identity.session)
        issued, run_id = prepared.issued, prepared.run_id
    async with sessions.begin() as db:
        grant = await db.get(DocumentTransformQaGrant, issued.grant_id)
        session = await db.get(UserSession, identity.session.id)
        user = await db.get(User, identity.user.id)
        assert grant is not None
        assert session is not None
        assert user is not None
        match inactive:
            case "grant-expired":
                now = datetime.now(UTC)
                grant.created_at = now - timedelta(minutes=20)
                grant.expires_at = now - timedelta(minutes=5)
            case "session-expired":
                session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            case "revoked":
                session.revoked_at = datetime.now(UTC)
            case _:
                user.token_version += 1
    async with sessions.begin() as db:
        with pytest.raises(QaGrantError):
            await _bootstrap(db, run_id)


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


async def test_real_postgres_authorization_is_current_scoped_and_does_not_extend_lifetime(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions.begin() as db:
        identity = await _identity(db)
        issued = await _issue(db, identity.user, identity.session)
    async with sessions.begin() as db:
        capability = await _consume(db, issued.token, "execution-a")
    async with sessions.begin() as db:
        authority = await _authorize(db, capability.token)
    assert authority.user_id == identity.user.id
    assert authority.source_folder_id == _SOURCE_FOLDER_ID
    assert authority.destination_folder_id == _DESTINATION_FOLDER_ID
    assert authority.mode is TransformMode.BOTH
    assert authority.expires_at == capability.expires_at
    assert authority.job_expires_at == capability.job_expires_at
    async with sessions.begin() as db:
        with pytest.raises(QaGrantError):
            await _authorize(db, capability.token, execution_name="execution-b")
    async with sessions.begin() as db:
        renewed = await _renew(db, capability.token)
    async with sessions.begin() as db:
        with pytest.raises(QaGrantError):
            await _authorize(db, capability.token)
        renewed_authority = await _authorize(db, renewed.token)
    assert renewed_authority.expires_at == renewed.expires_at


@pytest.mark.parametrize("terminal_state", ["expired", "revoked"])
async def test_real_postgres_authorization_rejects_inactive_capability(
    sessions: async_sessionmaker[AsyncSession], terminal_state: str
) -> None:
    async with sessions.begin() as db:
        identity = await _identity(db)
        issued = await _issue(db, identity.user, identity.session)
    async with sessions.begin() as db:
        capability = await _consume(db, issued.token, "execution-a")
    async with sessions.begin() as db:
        grant = await db.scalar(
            select(DocumentTransformQaGrant).where(
                DocumentTransformQaGrant.user_id == identity.user.id
            )
        )
        assert grant is not None
        if terminal_state == "expired":
            grant.capability_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        else:
            grant.revoked_at = datetime.now(UTC)
    async with sessions.begin() as db:
        with pytest.raises(QaGrantError):
            await _authorize(db, capability.token)


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


async def test_real_postgres_cleanup_is_bounded_and_preserves_live_consumed_grant(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions.begin() as db:
        identity = await _identity(db)
        first = await _issue(db, identity.user, identity.session)
        second = await _issue(db, identity.user, identity.session)
        live = await _issue(db, identity.user, identity.session)
    async with sessions.begin() as db:
        await _consume(db, live.token, "execution-a")
        now = datetime.now(UTC)
        for grant_id in (first.grant_id, second.grant_id):
            grant = await db.get(DocumentTransformQaGrant, grant_id)
            assert grant is not None
            grant.created_at = now - timedelta(minutes=30)
            grant.expires_at = now - timedelta(minutes=15)
        consumed = await db.get(DocumentTransformQaGrant, live.grant_id)
        assert consumed is not None
        consumed.created_at = now - timedelta(minutes=30)
        consumed.expires_at = now - timedelta(minutes=15)
    async with sessions.begin() as db:
        assert await delete_expired_qa_grants(db, limit=1) == 1
    async with sessions.begin() as db:
        assert await delete_expired_qa_grants(db, limit=1) == 1
    async with sessions.begin() as db:
        assert await delete_expired_qa_grants(db, limit=1) == 0
        assert await db.get(DocumentTransformQaGrant, live.grant_id) is not None


async def test_real_postgres_cleanup_deletes_consumed_grant_only_after_job_expiry(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions.begin() as db:
        identity = await _identity(db)
        issued = await _issue(db, identity.user, identity.session)
    async with sessions.begin() as db:
        await _consume(db, issued.token, "execution-a")
        grant = await db.get(DocumentTransformQaGrant, issued.grant_id)
        assert grant is not None
        now = datetime.now(UTC)
        grant.capability_expires_at = now - timedelta(minutes=2)
        grant.job_expires_at = now - timedelta(minutes=1)
    async with sessions.begin() as db:
        assert await delete_expired_qa_grants(db) == 1
        assert await db.get(DocumentTransformQaGrant, issued.grant_id) is None


async def test_real_postgres_cleanup_preserves_retired_run_and_its_revoked_grant(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions.begin() as db:
        identity = await _identity(db)
        prepared = await _prepared_bound_run(db, identity.user, identity.session)
        run = await db.get(DocumentTransformQaRun, prepared.run_id)
        grant = await db.get(DocumentTransformQaGrant, prepared.issued.grant_id)
        assert run is not None
        assert grant is not None
        now = datetime.now(UTC)
        run.state = "retired"
        run.launch_idempotency_key = None
        run.execution_uid = None
        run.execution_job_name = None
        run.launch_claimed_at = None
        grant.created_at = now - timedelta(minutes=30)
        grant.expires_at = now - timedelta(minutes=15)
        grant.revoked_at = now

    async with sessions.begin() as db:
        assert await delete_expired_qa_grants(db) == 0
        assert await db.get(DocumentTransformQaRun, prepared.run_id) is not None
        assert await db.get(DocumentTransformQaGrant, prepared.issued.grant_id) is not None


@pytest.mark.parametrize("limit", [0, 101])
async def test_cleanup_limit_is_validated_before_database_access(limit: int) -> None:
    async with AsyncSession() as db:
        with pytest.raises(QaGrantError):
            await delete_expired_qa_grants(db, limit=limit)
