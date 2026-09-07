import base64
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import hmac
import secrets
import uuid

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.document_transform.schemas import TransformMode
from app.identifiers import AuthSessionId, FileId, UserId
from app.models.auth_session import UserSession
from app.models.document_transform_qa_grant import (
    DocumentTransformQaGrant,
    DocumentTransformQaGrantId,
)
from app.models.user import User


MAX_GRANT_LIFETIME = timedelta(minutes=15)
CAPABILITY_LIFETIME = timedelta(minutes=5)
MAX_JOB_LIFETIME = timedelta(hours=2)
_TOKEN_BYTES = 32
_MAX_TOKEN_LENGTH = 256
_MAX_EXECUTION_NAME_LENGTH = 255
_RELEASE_SHA_LENGTH = 40
_IMAGE_DIGEST_LENGTH = 71
_MIN_DERIVATION_KEY_BYTES = 32


class QaGrantError(Exception):
    """A QA grant or capability is invalid, expired, revoked, or mismatched."""


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
class _LockedGrant:
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
    row = await _locked_grant(db, token_digest=_digest(token))
    if row is None:
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
) -> QaCapability:
    _validate_runtime(execution_name)
    _validate_binding(release_sha, image_digest, source_folder_id, destination_folder_id)
    supplied_digest = _digest(capability_token)
    row = await _locked_grant(db, capability_digest=supplied_digest)
    if row is None:
        raise QaGrantError
    grant, user, session, now = row.grant, row.user, row.session, row.now
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


async def delete_expired_qa_grants(db: AsyncSession) -> int:
    now = await db.scalar(select(func.clock_timestamp()))
    if not isinstance(now, datetime):
        raise QaGrantError
    result = await db.execute(
        delete(DocumentTransformQaGrant).where(
            or_(
                DocumentTransformQaGrant.expires_at < now,
                DocumentTransformQaGrant.job_expires_at < now,
            )
        )
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


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


def _validate_runtime(execution_name: str) -> None:
    if (
        not execution_name
        or len(execution_name) > _MAX_EXECUTION_NAME_LENGTH
        or not execution_name.isprintable()
        or execution_name.strip() != execution_name
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
