import base64
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import hmac
import secrets
import uuid

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.document_transform.schemas import TransformMode
from app.identifiers import AuthSessionId, FileId, IdempotencyKey, QaPlanId, QaRunId, TaskId, UserId
from app.models.auth_session import UserSession
from app.models.document_transform_qa_grant import (
    DocumentTransformQaGrant,
    DocumentTransformQaGrantId,
)
from app.models.document_transform_qa_run import DocumentTransformQaRun
from app.models.task import Task
from app.models.task_replay_key import TaskReplayKey
from app.models.user import User
from app.tasks.schemas import TaskKind


MAX_GRANT_LIFETIME = timedelta(minutes=15)
CAPABILITY_LIFETIME = timedelta(minutes=5)
MAX_JOB_LIFETIME = timedelta(hours=2)
MAX_LAUNCH_LEASE = timedelta(minutes=1)
_TOKEN_BYTES = 32
_MAX_TOKEN_LENGTH = 256
_MAX_EXECUTION_NAME_LENGTH = 255
_RELEASE_SHA_LENGTH = 40
_IMAGE_DIGEST_LENGTH = 71
_MIN_DERIVATION_KEY_BYTES = 32
_MAX_CLEANUP_BATCH = 100
_QA_LAUNCH_ADVISORY_LOCK = 0x51414C41554E4348


class QaGrantError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class IssuedQaGrant:
    grant_id: DocumentTransformQaGrantId
    token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class QaCapability:
    token: str
    expires_at: datetime
    job_expires_at: datetime
    user_id: UserId
    user_email: str


@dataclass(frozen=True, slots=True)
class QaAuthority:
    grant_id: DocumentTransformQaGrantId
    user_id: UserId
    user_email: str
    execution_name: str
    release_sha: str
    image_digest: str
    source_folder_id: FileId
    destination_folder_id: FileId
    mode: TransformMode
    expires_at: datetime
    job_expires_at: datetime


@dataclass(frozen=True, slots=True)
class QaCampaignAuthoritySnapshot:
    run_id: QaRunId
    grant_id: DocumentTransformQaGrantId
    user_id: UserId
    user_email: str
    execution_name: str
    execution_uid: uuid.UUID
    execution_job_name: str
    bootstrap_capability_digest: bytes
    release_sha: str
    image_digest: str
    source_folder_id: FileId
    destination_folder_id: FileId
    mode: TransformMode


@dataclass(frozen=True, slots=True)
class PreparedQaRun:
    run_id: QaRunId


@dataclass(frozen=True, slots=True)
class QaBootstrapResult:
    capability: QaCapability
    execution_name: str
    release_sha: str
    image_digest: str
    source_folder_id: FileId
    destination_folder_id: FileId
    mode: TransformMode


@dataclass(frozen=True, slots=True)
class PreparedQaLaunch:
    run_id: QaRunId
    state: str
    execution_name: str | None
    owns_launch_claim: bool


@dataclass(frozen=True, slots=True)
class QaExecutionIdentity:
    execution_name: str
    execution_uid: uuid.UUID
    execution_job_name: str
    release_sha: str
    image_digest: str


async def prepare_idempotent_qa_launch(
    db: AsyncSession,
    *,
    idempotency_key: IdempotencyKey,
    issuer_user_id: UserId,
    user_id: UserId,
    session_id: AuthSessionId,
    release_sha: str,
    image_digest: str,
    source_folder_id: FileId,
    destination_folder_id: FileId,
    mode: TransformMode,
) -> PreparedQaLaunch:
    # The workload identity is shared by every execution of the configured job.
    # Serialize all launch preparation, in addition to the database uniqueness
    # constraint, so only one execution can ever be bootstrap-eligible at once.
    await db.execute(select(func.pg_advisory_xact_lock(_QA_LAUNCH_ADVISORY_LOCK)))
    lock_key = idempotency_key.int & ((1 << 63) - 1)
    await db.execute(select(func.pg_advisory_xact_lock(lock_key)))
    existing = await db.scalar(
        select(DocumentTransformQaRun)
        .where(DocumentTransformQaRun.launch_idempotency_key == idempotency_key)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        row = await _locked_run(db, existing.id)
        if (
            row is None
            or user_id != issuer_user_id
            or row.run.user_id != user_id
            or not _run_matches(
                row.run,
                row.grant,
                release_sha=release_sha,
                image_digest=image_digest,
                source_folder_id=source_folder_id,
                destination_folder_id=destination_folder_id,
                mode=mode,
            )
        ):
            raise QaGrantError
        if (
            row.run.state == "launch_claimed"
            and row.run.launch_lease_expires_at is not None
            and row.run.launch_lease_expires_at <= row.now
        ):
            row.run.state = "launch_failed"
            row.run.launch_lease_expires_at = None
            row.run.launch_failed_at = row.now
            await db.flush()
        return PreparedQaLaunch(
            row.run.id,
            row.run.state,
            row.run.execution_name,
            owns_launch_claim=False,
        )
    issued = await issue_qa_grant(
        db,
        issuer_user_id=issuer_user_id,
        user_id=user_id,
        session_id=session_id,
        release_sha=release_sha,
        image_digest=image_digest,
        source_folder_id=source_folder_id,
        destination_folder_id=destination_folder_id,
        mode=mode,
    )
    prepared = await prepare_qa_bootstrap_run(
        db,
        grant_id=issued.grant_id,
        release_sha=release_sha,
        image_digest=image_digest,
        source_folder_id=source_folder_id,
        destination_folder_id=destination_folder_id,
        mode=mode,
    )
    if (row := await _locked_run(db, prepared.run_id)) is None:
        raise QaGrantError
    row.run.launch_idempotency_key = idempotency_key
    row.run.launch_claimed_at = row.now
    row.run.launch_lease_expires_at = row.now + MAX_LAUNCH_LEASE
    row.run.state = "launch_claimed"
    await db.flush()
    return PreparedQaLaunch(
        prepared.run_id,
        "launch_claimed",
        None,
        owns_launch_claim=True,
    )


@dataclass(frozen=True, slots=True)
class QaSubmittedCanary:
    plan_id: QaPlanId
    task_id: TaskId


@dataclass(frozen=True, slots=True)
class _LockedGrant:
    grant: DocumentTransformQaGrant
    user: User
    session: UserSession
    now: datetime


@dataclass(frozen=True, slots=True)
class _LockedRun:
    run: DocumentTransformQaRun
    grant: DocumentTransformQaGrant
    user: User
    session: UserSession
    now: datetime


def _digest(token: str) -> bytes:
    if not token or len(token) > _MAX_TOKEN_LENGTH or not token.isascii():
        raise QaGrantError
    return hashlib.sha256(token.encode()).digest()


def _derive_capability(
    key: bytes,
    grant_id: DocumentTransformQaGrantId,
    operation: str,
    credential: str,
    request_nonce: uuid.UUID,
) -> str:
    if len(key) < _MIN_DERIVATION_KEY_BYTES:
        raise QaGrantError
    message = f"qa-capability\0{grant_id}\0{operation}\0{request_nonce}\0{credential}".encode()
    return base64.urlsafe_b64encode(hmac.digest(key, message, "sha256")).rstrip(b"=").decode()


async def issue_qa_grant(
    db: AsyncSession,
    *,
    issuer_user_id: UserId,
    user_id: UserId,
    session_id: AuthSessionId,
    release_sha: str,
    image_digest: str,
    source_folder_id: FileId,
    destination_folder_id: FileId,
    mode: TransformMode,
    lifetime: timedelta = MAX_GRANT_LIFETIME,
) -> IssuedQaGrant:
    _validate_binding(release_sha, image_digest, source_folder_id, destination_folder_id)
    if issuer_user_id != user_id or not timedelta(0) < lifetime <= MAX_GRANT_LIFETIME:
        raise QaGrantError
    await delete_expired_qa_grants(db)
    row = (
        await db.execute(
            select(User, UserSession, func.clock_timestamp())
            .join(UserSession, UserSession.user_id == User.id)
            .where(User.id == user_id, UserSession.id == session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    if row is None:
        raise QaGrantError
    user, session, now = row
    if not _identity_is_live_values(user, session, user.token_version, now):
        raise QaGrantError
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    expires_at = now + lifetime
    grant = DocumentTransformQaGrant(
        id=DocumentTransformQaGrantId(uuid.uuid7()),
        token_digest=_digest(token),
        issuer_user_id=issuer_user_id,
        user_id=user.id,
        user_session_id=session.id,
        user_session_revision=user.token_version,
        release_sha=release_sha,
        image_digest=image_digest,
        source_folder_id=source_folder_id,
        destination_folder_id=destination_folder_id,
        mode=mode.value,
        created_at=now,
        expires_at=expires_at,
    )
    db.add(grant)
    await db.flush()
    return IssuedQaGrant(grant.id, token, expires_at)


async def prepare_qa_bootstrap_run(
    db: AsyncSession,
    *,
    grant_id: DocumentTransformQaGrantId,
    release_sha: str,
    image_digest: str,
    source_folder_id: FileId,
    destination_folder_id: FileId,
    mode: TransformMode,
) -> PreparedQaRun:
    _validate_binding(release_sha, image_digest, source_folder_id, destination_folder_id)
    if (row := await _locked_grant_by_id(db, grant_id)) is None:
        raise QaGrantError
    grant, user, session, now = row.grant, row.user, row.session, row.now
    if not _matches(
        grant,
        release_sha=release_sha,
        image_digest=image_digest,
        source_folder_id=source_folder_id,
        destination_folder_id=destination_folder_id,
        mode=mode,
    ):
        raise QaGrantError
    existing = await db.scalar(
        select(DocumentTransformQaRun)
        .where(DocumentTransformQaRun.grant_id == grant.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        if not _run_matches(
            existing,
            grant,
            release_sha=release_sha,
            image_digest=image_digest,
            source_folder_id=source_folder_id,
            destination_folder_id=destination_folder_id,
            mode=mode,
        ):
            raise QaGrantError
        identity_is_live = _identity_is_live(grant, user, session, now)
        grant_state_is_live = (
            _active_capability(grant, now) and _run_capability_matches_grant(existing, grant)
            if existing.state == "bootstrapped"
            else _grant_is_prepared(grant, user, session, now)
        )
        if not identity_is_live or not grant_state_is_live:
            raise QaGrantError
        return PreparedQaRun(existing.id)
    if not _grant_is_prepared(grant, user, session, now):
        raise QaGrantError
    run = DocumentTransformQaRun(
        id=QaRunId(uuid.uuid7()),
        grant_id=grant.id,
        user_id=grant.user_id,
        user_session_id=grant.user_session_id,
        user_session_revision=grant.user_session_revision,
        release_sha=grant.release_sha,
        image_digest=grant.image_digest,
        source_folder_id=grant.source_folder_id,
        destination_folder_id=grant.destination_folder_id,
        mode=grant.mode,
        state="created",
        created_at=now,
    )
    db.add(run)
    await db.flush()
    return PreparedQaRun(run.id)


async def bind_qa_bootstrap_execution(
    db: AsyncSession,
    *,
    run_id: QaRunId,
    execution_name: str,
    execution_uid: uuid.UUID,
    execution_job_name: str,
    release_sha: str,
    image_digest: str,
    source_folder_id: FileId,
    destination_folder_id: FileId,
    mode: TransformMode,
) -> None:
    _validate_runtime(execution_name, execution_job_name)
    _validate_binding(release_sha, image_digest, source_folder_id, destination_folder_id)
    if (row := await _locked_run(db, run_id)) is None:
        raise QaGrantError
    run, grant, user, session, now = row.run, row.grant, row.user, row.session, row.now
    if not _run_matches(
        run,
        grant,
        release_sha=release_sha,
        image_digest=image_digest,
        source_folder_id=source_folder_id,
        destination_folder_id=destination_folder_id,
        mode=mode,
    ):
        raise QaGrantError
    execution_matches = hmac.compare_digest(run.execution_name or "", execution_name)
    attestation_matches = run.execution_uid == execution_uid and hmac.compare_digest(
        run.execution_job_name or "", execution_job_name
    )
    if run.state == "bootstrapped":
        if not (
            execution_matches
            and attestation_matches
            and _active_capability(grant, now)
            and _run_capability_matches_grant(run, grant)
            and _identity_is_live(grant, user, session, now)
        ):
            raise QaGrantError
        return
    if not _grant_is_prepared(grant, user, session, now):
        raise QaGrantError
    if run.state == "launch_claimed":
        run.state = "execution_bound"
        run.launch_lease_expires_at = None
        run.execution_name = execution_name
        run.execution_uid = execution_uid
        run.execution_job_name = execution_job_name
        await db.flush()
        return
    if run.state != "execution_bound" or not execution_matches or not attestation_matches:
        raise QaGrantError


async def fail_qa_bootstrap_launch(db: AsyncSession, *, run_id: QaRunId) -> None:
    row = await _locked_run(db, run_id)
    if (
        row is None
        or row.run.state != "launch_claimed"
        or not _grant_is_prepared(row.grant, row.user, row.session, row.now)
    ):
        raise QaGrantError
    row.run.state = "launch_failed"
    row.run.launch_lease_expires_at = None
    row.run.launch_failed_at = row.now
    await db.flush()


async def bootstrap_attested_qa_execution(
    db: AsyncSession,
    *,
    execution_uid: uuid.UUID,
    execution_name: str,
    execution_job_name: str,
    request_nonce: uuid.UUID,
    derivation_key: bytes,
    release_sha: str,
    image_digest: str,
) -> QaBootstrapResult:
    _validate_runtime(execution_name, execution_job_name)
    if (row := await _locked_active_launch_run(db)) is None:
        raise QaGrantError
    run = row.run
    attestation_matches = all(
        (
            run.execution_name is not None,
            hmac.compare_digest(run.execution_name or "", execution_name),
            run.execution_uid == execution_uid,
            hmac.compare_digest(run.execution_job_name or "", execution_job_name),
            hmac.compare_digest(run.release_sha, release_sha),
            hmac.compare_digest(run.image_digest, image_digest),
        )
    )
    if not attestation_matches:
        raise QaGrantError
    mode = TransformMode(run.mode)
    capability = await bootstrap_qa_run(
        db,
        run_id=run.id,
        execution_name=execution_name,
        request_nonce=request_nonce,
        derivation_key=derivation_key,
        release_sha=run.release_sha,
        image_digest=run.image_digest,
        source_folder_id=run.source_folder_id,
        destination_folder_id=run.destination_folder_id,
        mode=mode,
    )
    return QaBootstrapResult(
        capability=capability,
        execution_name=execution_name,
        release_sha=run.release_sha,
        image_digest=run.image_digest,
        source_folder_id=run.source_folder_id,
        destination_folder_id=run.destination_folder_id,
        mode=mode,
    )


async def read_active_qa_execution(db: AsyncSession) -> QaExecutionIdentity:
    if (row := await _locked_active_launch_run(db)) is None:
        raise QaGrantError
    run = row.run
    execution_name = run.execution_name
    execution_uid = run.execution_uid
    execution_job_name = run.execution_job_name
    if execution_name is None or execution_uid is None or execution_job_name is None:
        raise QaGrantError
    return QaExecutionIdentity(
        execution_name=execution_name,
        execution_uid=execution_uid,
        execution_job_name=execution_job_name,
        release_sha=run.release_sha,
        image_digest=run.image_digest,
    )


async def bootstrap_qa_run(
    db: AsyncSession,
    *,
    run_id: QaRunId,
    execution_name: str,
    request_nonce: uuid.UUID,
    derivation_key: bytes,
    release_sha: str,
    image_digest: str,
    source_folder_id: FileId,
    destination_folder_id: FileId,
    mode: TransformMode,
) -> QaCapability:
    _validate_runtime(execution_name)
    _validate_binding(release_sha, image_digest, source_folder_id, destination_folder_id)
    if (row := await _locked_run(db, run_id)) is None:
        raise QaGrantError
    run, grant, user, session, now = row.run, row.grant, row.user, row.session, row.now
    if (
        run.state not in {"execution_bound", "bootstrapped"}
        or not hmac.compare_digest(run.execution_name or "", execution_name)
        or not _run_matches(
            run,
            grant,
            release_sha=release_sha,
            image_digest=image_digest,
            source_folder_id=source_folder_id,
            destination_folder_id=destination_folder_id,
            mode=mode,
        )
    ):
        raise QaGrantError
    capability = _derive_bootstrap_capability(
        derivation_key,
        run_id=run.id,
        grant_id=grant.id,
        execution_name=execution_name,
        request_nonce=request_nonce,
    )
    if run.state == "bootstrapped":
        return _replay_bootstrap(
            run,
            grant,
            user,
            session,
            now,
            capability=capability,
            request_nonce=request_nonce,
        )
    if not _grant_is_prepared(grant, user, session, now):
        raise QaGrantError
    capability_digest = _digest(capability)
    job_expires_at = now + MAX_JOB_LIFETIME
    capability_expires_at = min(now + CAPABILITY_LIFETIME, job_expires_at)
    grant.consumed_at = now
    grant.consumed_by_execution = execution_name
    grant.consume_request_nonce = request_nonce
    grant.capability_digest = capability_digest
    grant.capability_expires_at = capability_expires_at
    grant.job_expires_at = job_expires_at
    run.state = "bootstrapped"
    run.bootstrap_request_nonce = request_nonce
    run.bootstrap_capability_digest = capability_digest
    run.bootstrapped_at = now
    await db.flush()
    return QaCapability(capability, capability_expires_at, job_expires_at, user.id, user.email)


async def bind_qa_canary_submission(
    db: AsyncSession,
    *,
    authority: QaAuthority,
    plan_id: QaPlanId,
    task_id: TaskId,
) -> QaSubmittedCanary:
    row = await _locked_run_by_grant(db, authority.grant_id)
    if row is None or not _authority_matches_run(row, authority):
        raise QaGrantError
    run = row.run
    if run.state != "bootstrapped":
        raise QaGrantError
    existing = _submitted_canary(run)
    if existing is not None:
        if not (
            hmac.compare_digest(existing.plan_id.bytes, plan_id.bytes)
            and hmac.compare_digest(existing.task_id.bytes, task_id.bytes)
        ):
            raise QaGrantError
        return existing
    if any(
        value is not None
        for value in (run.submitted_plan_id, run.submitted_task_id, run.submitted_at)
    ):
        raise QaGrantError
    bound_task_id = await db.scalar(
        select(Task.id).where(
            Task.id == task_id,
            Task.owner_user_id == authority.user_id,
            Task.kind == TaskKind.FILE_DOCUMENT_TRANSFORM,
            or_(
                Task.idempotency_key == plan_id,
                Task.id.in_(
                    select(TaskReplayKey.task_id).where(
                        TaskReplayKey.owner_user_id == authority.user_id,
                        TaskReplayKey.kind == TaskKind.FILE_DOCUMENT_TRANSFORM,
                        TaskReplayKey.idempotency_key == plan_id,
                    )
                ),
            ),
        )
    )
    if bound_task_id is None or not hmac.compare_digest(bound_task_id.bytes, task_id.bytes):
        raise QaGrantError
    run.submitted_plan_id = plan_id
    run.submitted_task_id = task_id
    run.submitted_at = row.now
    await db.flush()
    return QaSubmittedCanary(plan_id=plan_id, task_id=task_id)


async def load_qa_canary_submission(
    db: AsyncSession,
    *,
    authority: QaAuthority,
) -> QaSubmittedCanary:
    row = await _run_by_grant(db, authority.grant_id, lock=False)
    if row is None or not _authority_matches_run(row, authority):
        raise QaGrantError
    submitted = _submitted_canary(row.run)
    if submitted is None:
        raise QaGrantError
    return submitted


async def load_qa_campaign_authority_snapshot(
    db: AsyncSession,
    *,
    authority: QaAuthority,
    for_update: bool = False,
) -> QaCampaignAuthoritySnapshot:
    row = (
        await _locked_run_by_grant(db, authority.grant_id)
        if for_update
        else await _run_by_grant(db, authority.grant_id, lock=False)
    )
    if row is None or not _authority_matches_run(row, authority):
        raise QaGrantError
    run = row.run
    execution_name = run.execution_name
    execution_uid = run.execution_uid
    execution_job_name = run.execution_job_name
    bootstrap_capability_digest = run.bootstrap_capability_digest
    if (
        execution_name is None
        or execution_uid is None
        or execution_job_name is None
        or bootstrap_capability_digest is None
    ):
        raise QaGrantError
    if not execution_job_name or len(bootstrap_capability_digest) != hashlib.sha256().digest_size:
        raise QaGrantError
    return QaCampaignAuthoritySnapshot(
        run_id=run.id,
        grant_id=row.grant.id,
        user_id=row.user.id,
        user_email=row.user.email,
        execution_name=execution_name,
        execution_uid=execution_uid,
        execution_job_name=execution_job_name,
        bootstrap_capability_digest=bytes(bootstrap_capability_digest),
        release_sha=run.release_sha,
        image_digest=run.image_digest,
        source_folder_id=run.source_folder_id,
        destination_folder_id=run.destination_folder_id,
        mode=TransformMode(run.mode),
    )


def _derive_bootstrap_capability(
    key: bytes,
    *,
    run_id: QaRunId,
    grant_id: DocumentTransformQaGrantId,
    execution_name: str,
    request_nonce: uuid.UUID,
) -> str:
    if len(key) < _MIN_DERIVATION_KEY_BYTES:
        raise QaGrantError
    message = (
        f"qa-bootstrap-capability\0{run_id}\0{grant_id}\0{execution_name}\0{request_nonce}"
    ).encode()
    return base64.urlsafe_b64encode(hmac.digest(key, message, "sha256")).rstrip(b"=").decode()


async def consume_qa_grant(
    db: AsyncSession,
    *,
    token: str,
    request_nonce: uuid.UUID,
    derivation_key: bytes,
    execution_name: str,
    release_sha: str,
    image_digest: str,
    source_folder_id: FileId,
    destination_folder_id: FileId,
    mode: TransformMode,
) -> QaCapability:
    _validate_runtime(execution_name)
    _validate_binding(release_sha, image_digest, source_folder_id, destination_folder_id)
    if (row := await _locked_grant(db, token_digest=_digest(token))) is None:
        raise QaGrantError
    grant, user, session, now = row.grant, row.user, row.session, row.now
    capability = _derive_capability(derivation_key, grant.id, "consume", token, request_nonce)
    if grant.consumed_at is not None:
        return _replay_consumption(
            grant,
            user,
            session,
            now,
            capability=capability,
            request_nonce=request_nonce,
            execution_name=execution_name,
            release_sha=release_sha,
            image_digest=image_digest,
            source_folder_id=source_folder_id,
            destination_folder_id=destination_folder_id,
            mode=mode,
        )
    if (
        grant.revoked_at is not None
        or not grant.created_at <= now < grant.expires_at
        or not _identity_is_live(grant, user, session, now)
        or not _matches(
            grant,
            release_sha=release_sha,
            image_digest=image_digest,
            source_folder_id=source_folder_id,
            destination_folder_id=destination_folder_id,
            mode=mode,
        )
    ):
        raise QaGrantError
    grant.consumed_at = now
    grant.consumed_by_execution = execution_name
    grant.consume_request_nonce = request_nonce
    grant.capability_digest = _digest(capability)
    job_expires_at = now + MAX_JOB_LIFETIME
    capability_expires_at = min(now + CAPABILITY_LIFETIME, job_expires_at)
    grant.job_expires_at = job_expires_at
    grant.capability_expires_at = capability_expires_at
    await db.flush()
    return QaCapability(capability, capability_expires_at, job_expires_at, user.id, user.email)


async def renew_qa_capability(
    db: AsyncSession,
    *,
    capability_token: str,
    request_nonce: uuid.UUID,
    derivation_key: bytes,
    execution_name: str,
    release_sha: str,
    image_digest: str,
    source_folder_id: FileId,
    destination_folder_id: FileId,
    mode: TransformMode,
    require_submitted_task: bool = False,
) -> QaCapability:
    _validate_runtime(execution_name)
    _validate_binding(release_sha, image_digest, source_folder_id, destination_folder_id)
    supplied_digest = _digest(capability_token)
    if (row := await _locked_grant(db, capability_digest=supplied_digest)) is None:
        raise QaGrantError
    grant, user, session, now = row.grant, row.user, row.session, row.now
    if require_submitted_task:
        run_row = await _locked_run_by_grant(db, grant.id)
        if (
            run_row is None
            or run_row.run.state != "bootstrapped"
            or run_row.run.execution_name != execution_name
            or not _run_capability_matches_grant(run_row.run, grant)
            or not await _submitted_task_is_bound(db, run_row.run, user_id=user.id)
        ):
            raise QaGrantError
    if grant.previous_capability_digest is not None and hmac.compare_digest(
        grant.previous_capability_digest, supplied_digest
    ):
        return _replay_renewal(
            grant,
            user,
            session,
            now,
            capability_token=capability_token,
            request_nonce=request_nonce,
            derivation_key=derivation_key,
            execution_name=execution_name,
            release_sha=release_sha,
            image_digest=image_digest,
            source_folder_id=source_folder_id,
            destination_folder_id=destination_folder_id,
            mode=mode,
        )
    if (
        not _renewal_is_live(grant, execution_name, now)
        or not _identity_is_live(grant, user, session, now)
        or not _matches(
            grant,
            release_sha=release_sha,
            image_digest=image_digest,
            source_folder_id=source_folder_id,
            destination_folder_id=destination_folder_id,
            mode=mode,
        )
    ):
        raise QaGrantError
    capability_expires_at = grant.capability_expires_at
    job_expires_at = grant.job_expires_at
    if capability_expires_at is None or job_expires_at is None:  # pragma: no cover - checked above
        raise QaGrantError
    capability = _derive_capability(
        derivation_key, grant.id, "renew", capability_token, request_nonce
    )
    grant.previous_capability_digest = supplied_digest
    grant.capability_digest = _digest(capability)
    grant.renewal_request_nonce = request_nonce
    renewed_expires_at = min(now + CAPABILITY_LIFETIME, job_expires_at)
    grant.capability_expires_at = renewed_expires_at
    grant.last_renewed_at = now
    await db.flush()
    return QaCapability(capability, renewed_expires_at, job_expires_at, user.id, user.email)


async def _submitted_task_is_bound(
    db: AsyncSession,
    run: DocumentTransformQaRun,
    *,
    user_id: UserId,
) -> bool:
    submitted = _submitted_canary(run)
    if submitted is None:
        return False
    bound_task_id = await db.scalar(
        select(Task.id).where(
            Task.id == submitted.task_id,
            Task.owner_user_id == user_id,
            Task.kind == TaskKind.FILE_DOCUMENT_TRANSFORM,
            or_(
                Task.idempotency_key == submitted.plan_id,
                Task.id.in_(
                    select(TaskReplayKey.task_id).where(
                        TaskReplayKey.owner_user_id == user_id,
                        TaskReplayKey.kind == TaskKind.FILE_DOCUMENT_TRANSFORM,
                        TaskReplayKey.idempotency_key == submitted.plan_id,
                    )
                ),
            ),
        )
    )
    return bound_task_id is not None and hmac.compare_digest(
        bound_task_id.bytes, submitted.task_id.bytes
    )


async def authorize_qa_capability(
    db: AsyncSession,
    *,
    capability_token: str,
    execution_name: str,
    release_sha: str,
    image_digest: str,
    source_folder_id: FileId,
    destination_folder_id: FileId,
    mode: TransformMode,
) -> QaAuthority:
    _validate_runtime(execution_name)
    _validate_binding(release_sha, image_digest, source_folder_id, destination_folder_id)
    if (
        row := await _current_capability_grant(db, capability_digest=_digest(capability_token))
    ) is None:
        raise QaGrantError
    grant, user, session, now = row.grant, row.user, row.session, row.now
    if (
        grant.consumed_by_execution != execution_name
        or not _active_capability(grant, now)
        or not _identity_is_live(grant, user, session, now)
        or not _matches(
            grant,
            release_sha=release_sha,
            image_digest=image_digest,
            source_folder_id=source_folder_id,
            destination_folder_id=destination_folder_id,
            mode=mode,
        )
    ):
        raise QaGrantError
    capability_expires_at = grant.capability_expires_at
    job_expires_at = grant.job_expires_at
    if capability_expires_at is None or job_expires_at is None:  # pragma: no cover
        raise QaGrantError
    return QaAuthority(
        grant_id=grant.id,
        user_id=user.id,
        user_email=user.email,
        execution_name=execution_name,
        release_sha=grant.release_sha,
        image_digest=grant.image_digest,
        source_folder_id=grant.source_folder_id,
        destination_folder_id=grant.destination_folder_id,
        mode=TransformMode(grant.mode),
        expires_at=capability_expires_at,
        job_expires_at=job_expires_at,
    )


async def delete_expired_qa_grants(db: AsyncSession, *, limit: int = _MAX_CLEANUP_BATCH) -> int:
    if not 1 <= limit <= _MAX_CLEANUP_BATCH:
        raise QaGrantError
    now = await db.scalar(select(func.clock_timestamp()))
    if not isinstance(now, datetime):
        raise QaGrantError
    expired = (
        select(DocumentTransformQaGrant.id)
        .where(
            or_(
                and_(
                    DocumentTransformQaGrant.consumed_at.is_(None),
                    DocumentTransformQaGrant.expires_at < now,
                ),
                and_(
                    DocumentTransformQaGrant.consumed_at.is_not(None),
                    DocumentTransformQaGrant.job_expires_at < now,
                ),
            ),
            ~select(DocumentTransformQaRun.id)
            .where(
                DocumentTransformQaRun.grant_id == DocumentTransformQaGrant.id,
                DocumentTransformQaRun.state == "retired",
            )
            .exists(),
        )
        .limit(limit)
        .with_for_update(skip_locked=True)
        .cte("expired_document_transform_qa_grants")
    )
    result = await db.execute(
        delete(DocumentTransformQaGrant).where(
            DocumentTransformQaGrant.id.in_(select(expired.c.id))
        )
    )
    return int(getattr(result, "rowcount", 0) or 0)


def _replay_bootstrap(
    run: DocumentTransformQaRun,
    grant: DocumentTransformQaGrant,
    user: User,
    session: UserSession,
    now: datetime,
    *,
    capability: str,
    request_nonce: uuid.UUID,
) -> QaCapability:
    capability_digest = _digest(capability)
    run_digest_matches = run.bootstrap_capability_digest is not None and hmac.compare_digest(
        run.bootstrap_capability_digest, capability_digest
    )
    grant_digest_matches = grant.capability_digest is not None and hmac.compare_digest(
        grant.capability_digest, capability_digest
    )
    checks = (
        run.bootstrap_request_nonce == request_nonce,
        run_digest_matches,
        grant.consume_request_nonce == request_nonce,
        grant.consumed_by_execution == run.execution_name,
        grant.previous_capability_digest is None,
        grant_digest_matches,
        _active_capability(grant, now),
        _identity_is_live(grant, user, session, now),
    )
    if not all(checks):
        raise QaGrantError
    return _capability_result(grant, user, capability)


def _replay_consumption(
    grant: DocumentTransformQaGrant,
    user: User,
    session: UserSession,
    now: datetime,
    *,
    capability: str,
    request_nonce: uuid.UUID,
    execution_name: str,
    release_sha: str,
    image_digest: str,
    source_folder_id: FileId,
    destination_folder_id: FileId,
    mode: TransformMode,
) -> QaCapability:
    request_matches = (
        grant.consume_request_nonce != request_nonce
        or grant.consumed_by_execution != execution_name
        or grant.previous_capability_digest is not None
    )
    if (
        request_matches
        or not _returned_capability_is_live(grant, capability, now)
        or not _identity_is_live(grant, user, session, now)
        or not _matches(
            grant,
            release_sha=release_sha,
            image_digest=image_digest,
            source_folder_id=source_folder_id,
            destination_folder_id=destination_folder_id,
            mode=mode,
        )
    ):
        raise QaGrantError
    return _capability_result(grant, user, capability)


def _replay_renewal(
    grant: DocumentTransformQaGrant,
    user: User,
    session: UserSession,
    now: datetime,
    *,
    capability_token: str,
    request_nonce: uuid.UUID,
    derivation_key: bytes,
    execution_name: str,
    release_sha: str,
    image_digest: str,
    source_folder_id: FileId,
    destination_folder_id: FileId,
    mode: TransformMode,
) -> QaCapability:
    capability = _derive_capability(
        derivation_key, grant.id, "renew", capability_token, request_nonce
    )
    request_mismatch = grant.renewal_request_nonce != request_nonce or (
        grant.consumed_by_execution != execution_name
    )
    if (
        request_mismatch
        or not _returned_capability_is_live(grant, capability, now)
        or not _identity_is_live(grant, user, session, now)
        or not _matches(
            grant,
            release_sha=release_sha,
            image_digest=image_digest,
            source_folder_id=source_folder_id,
            destination_folder_id=destination_folder_id,
            mode=mode,
        )
    ):
        raise QaGrantError
    return _capability_result(grant, user, capability)


def _returned_capability_is_live(
    grant: DocumentTransformQaGrant, capability: str, now: datetime
) -> bool:
    return (
        grant.capability_digest is not None
        and hmac.compare_digest(grant.capability_digest, _digest(capability))
        and _active_capability(grant, now)
    )


def _active_capability(grant: DocumentTransformQaGrant, now: datetime) -> bool:
    return (
        grant.revoked_at is None
        and grant.capability_expires_at is not None
        and grant.job_expires_at is not None
        and now < grant.capability_expires_at
        and now < grant.job_expires_at
    )


def _capability_result(
    grant: DocumentTransformQaGrant, user: User, capability: str
) -> QaCapability:
    if grant.capability_expires_at is None or grant.job_expires_at is None:
        raise QaGrantError
    return QaCapability(
        capability, grant.capability_expires_at, grant.job_expires_at, user.id, user.email
    )


async def _locked_grant(
    db: AsyncSession, *, token_digest: bytes | None = None, capability_digest: bytes | None = None
) -> _LockedGrant | None:
    predicate = (
        DocumentTransformQaGrant.token_digest.op("=")(token_digest)
        if token_digest is not None
        else or_(
            DocumentTransformQaGrant.capability_digest.op("=")(capability_digest),
            DocumentTransformQaGrant.previous_capability_digest.op("=")(capability_digest),
        )
    )
    row = (
        await db.execute(
            select(DocumentTransformQaGrant, User, UserSession, func.clock_timestamp())
            .join(User, User.id == DocumentTransformQaGrant.user_id)
            .join(UserSession, UserSession.id == DocumentTransformQaGrant.user_session_id)
            .where(predicate)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    return None if row is None else _LockedGrant(row[0], row[1], row[2], row[3])


async def _locked_grant_by_id(
    db: AsyncSession, grant_id: DocumentTransformQaGrantId
) -> _LockedGrant | None:
    row = (
        await db.execute(
            select(DocumentTransformQaGrant, User, UserSession, func.clock_timestamp())
            .join(User, User.id == DocumentTransformQaGrant.user_id)
            .join(UserSession, UserSession.id == DocumentTransformQaGrant.user_session_id)
            .where(DocumentTransformQaGrant.id == grant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    return None if row is None else _LockedGrant(row[0], row[1], row[2], row[3])


async def _locked_run(db: AsyncSession, run_id: QaRunId) -> _LockedRun | None:
    row = (
        await db.execute(
            select(
                DocumentTransformQaRun,
                DocumentTransformQaGrant,
                User,
                UserSession,
                func.clock_timestamp(),
            )
            .join(
                DocumentTransformQaGrant,
                DocumentTransformQaGrant.id == DocumentTransformQaRun.grant_id,
            )
            .join(User, User.id == DocumentTransformQaRun.user_id)
            .join(UserSession, UserSession.id == DocumentTransformQaRun.user_session_id)
            .where(DocumentTransformQaRun.id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    if row is None:
        return None
    return _LockedRun(row[0], row[1], row[2], row[3], row[4])


async def _locked_run_by_grant(
    db: AsyncSession, grant_id: DocumentTransformQaGrantId
) -> _LockedRun | None:
    return await _run_by_grant(db, grant_id, lock=True)


async def _run_by_grant(
    db: AsyncSession,
    grant_id: DocumentTransformQaGrantId,
    *,
    lock: bool,
) -> _LockedRun | None:
    statement = (
        select(
            DocumentTransformQaRun,
            DocumentTransformQaGrant,
            User,
            UserSession,
            func.clock_timestamp(),
        )
        .join(
            DocumentTransformQaGrant,
            DocumentTransformQaGrant.id == DocumentTransformQaRun.grant_id,
        )
        .join(User, User.id == DocumentTransformQaRun.user_id)
        .join(UserSession, UserSession.id == DocumentTransformQaRun.user_session_id)
        .where(DocumentTransformQaRun.grant_id == grant_id)
        .execution_options(populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update()
    row = (await db.execute(statement)).one_or_none()
    if row is None:
        return None
    return _LockedRun(row[0], row[1], row[2], row[3], row[4])


async def _locked_active_launch_run(db: AsyncSession) -> _LockedRun | None:
    row = (
        await db.execute(
            select(
                DocumentTransformQaRun,
                DocumentTransformQaGrant,
                User,
                UserSession,
                func.clock_timestamp(),
            )
            .join(
                DocumentTransformQaGrant,
                DocumentTransformQaGrant.id == DocumentTransformQaRun.grant_id,
            )
            .join(User, User.id == DocumentTransformQaRun.user_id)
            .join(UserSession, UserSession.id == DocumentTransformQaRun.user_session_id)
            .where(
                DocumentTransformQaRun.launch_idempotency_key.is_not(None),
                DocumentTransformQaRun.state.in_(("execution_bound", "bootstrapped")),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    if row is None:
        return None
    return _LockedRun(row[0], row[1], row[2], row[3], row[4])


async def _current_capability_grant(
    db: AsyncSession, *, capability_digest: bytes
) -> _LockedGrant | None:
    row = (
        await db.execute(
            select(DocumentTransformQaGrant, User, UserSession, func.clock_timestamp())
            .join(User, User.id == DocumentTransformQaGrant.user_id)
            .join(UserSession, UserSession.id == DocumentTransformQaGrant.user_session_id)
            .where(DocumentTransformQaGrant.capability_digest.op("=")(capability_digest))
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    return None if row is None else _LockedGrant(row[0], row[1], row[2], row[3])


def _identity_is_live(
    grant: DocumentTransformQaGrant, user: User, session: UserSession, now: datetime
) -> bool:
    return (
        grant.issuer_user_id == grant.user_id == user.id
        and grant.user_session_id == session.id
        and _identity_is_live_values(user, session, grant.user_session_revision, now)
    )


def _identity_is_live_values(
    user: User, session: UserSession, token_version: int, now: datetime
) -> bool:
    return (
        session.user_id == user.id
        and session.revoked_at is None
        and session.expires_at > now
        and hmac.compare_digest(str(user.token_version), str(token_version))
        and bool(user.encrypted_refresh_token)
    )


def _grant_is_prepared(
    grant: DocumentTransformQaGrant,
    user: User,
    session: UserSession,
    now: datetime,
) -> bool:
    return (
        grant.revoked_at is None
        and grant.consumed_at is None
        and grant.consumed_by_execution is None
        and grant.capability_digest is None
        and grant.previous_capability_digest is None
        and grant.consume_request_nonce is None
        and grant.renewal_request_nonce is None
        and grant.capability_expires_at is None
        and grant.job_expires_at is None
        and grant.created_at <= now < grant.expires_at
        and _identity_is_live(grant, user, session, now)
    )


def _run_matches(
    run: DocumentTransformQaRun,
    grant: DocumentTransformQaGrant,
    *,
    release_sha: str,
    image_digest: str,
    source_folder_id: FileId,
    destination_folder_id: FileId,
    mode: TransformMode,
) -> bool:
    return (
        run.grant_id == grant.id
        and run.user_id == grant.user_id
        and run.user_session_id == grant.user_session_id
        and run.user_session_revision == grant.user_session_revision
        and run.release_sha == grant.release_sha == release_sha
        and run.image_digest == grant.image_digest == image_digest
        and run.source_folder_id == grant.source_folder_id == source_folder_id
        and run.destination_folder_id == grant.destination_folder_id == destination_folder_id
        and run.mode == grant.mode == mode.value
    )


def _run_capability_matches_grant(
    run: DocumentTransformQaRun, grant: DocumentTransformQaGrant
) -> bool:
    return (
        run.bootstrap_capability_digest is not None
        and grant.capability_digest is not None
        and hmac.compare_digest(run.bootstrap_capability_digest, grant.capability_digest)
    )


def _authority_matches_run(row: _LockedRun, authority: QaAuthority) -> bool:
    run, grant, user, session, now = row.run, row.grant, row.user, row.session, row.now
    return (
        run.state == "bootstrapped"
        and grant.id == authority.grant_id
        and user.id == authority.user_id
        and user.email.casefold() == authority.user_email.casefold()
        and hmac.compare_digest(run.execution_name or "", authority.execution_name)
        and _run_matches(
            run,
            grant,
            release_sha=authority.release_sha,
            image_digest=authority.image_digest,
            source_folder_id=authority.source_folder_id,
            destination_folder_id=authority.destination_folder_id,
            mode=authority.mode,
        )
        and _active_capability(grant, now)
        and _identity_is_live(grant, user, session, now)
    )


def _submitted_canary(run: DocumentTransformQaRun) -> QaSubmittedCanary | None:
    values = (run.submitted_plan_id, run.submitted_task_id, run.submitted_at)
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise QaGrantError
    plan_id = run.submitted_plan_id
    task_id = run.submitted_task_id
    if plan_id is None or task_id is None:  # pragma: no cover - narrowed above
        raise QaGrantError
    return QaSubmittedCanary(plan_id=plan_id, task_id=task_id)


def _matches(
    grant: DocumentTransformQaGrant,
    *,
    release_sha: str,
    image_digest: str,
    source_folder_id: FileId,
    destination_folder_id: FileId,
    mode: TransformMode,
) -> bool:
    return (
        grant.release_sha == release_sha
        and grant.image_digest == image_digest
        and grant.source_folder_id == source_folder_id
        and grant.destination_folder_id == destination_folder_id
        and grant.mode == mode.value
    )


def _renewal_is_live(grant: DocumentTransformQaGrant, execution_name: str, now: datetime) -> bool:
    return (
        grant.revoked_at is None
        and grant.consumed_by_execution == execution_name
        and grant.capability_expires_at is not None
        and grant.job_expires_at is not None
        and now < grant.capability_expires_at
        and now < grant.job_expires_at
    )


def _validate_runtime(execution_name: str, execution_job_name: str | None = None) -> None:
    if (
        not execution_name
        or len(execution_name) > _MAX_EXECUTION_NAME_LENGTH
        or not execution_name.isprintable()
        or execution_name.strip() != execution_name
    ):
        raise QaGrantError
    if execution_job_name is not None and (
        not execution_job_name
        or len(execution_job_name) > _MAX_EXECUTION_NAME_LENGTH
        or not execution_job_name.isprintable()
        or execution_job_name.strip() != execution_job_name
    ):
        raise QaGrantError


def _validate_binding(
    release_sha: str, image_digest: str, source_folder_id: FileId, destination_folder_id: FileId
) -> None:
    sha_valid = len(release_sha) == _RELEASE_SHA_LENGTH and all(
        c in "0123456789abcdef" for c in release_sha
    )
    digest_valid = (
        image_digest.startswith("sha256:")
        and len(image_digest) == _IMAGE_DIGEST_LENGTH
        and all(c in "0123456789abcdef" for c in image_digest[7:])
    )
    if not sha_valid or not digest_valid:
        raise QaGrantError
    if (
        not source_folder_id
        or not destination_folder_id
        or source_folder_id == destination_folder_id
    ):
        raise QaGrantError
