from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import hmac
import secrets
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.document_transform.schemas import TransformMode
from app.identifiers import AuthSessionId, FileId, UserId
from app.models.auth_session import UserSession
from app.models.document_transform_qa_grant import (
    DocumentTransformQaGrant,
    DocumentTransformQaGrantId,
)
from app.models.user import User


_MAX_GRANT_LIFETIME = timedelta(minutes=15)
_MAX_EXECUTION_NAME_LENGTH = 255
_MAX_TOKEN_LENGTH = 256
_RELEASE_SHA_LENGTH = 40
_TOKEN_BYTES = 32


class QaGrantError(Exception):
    """A QA grant was invalid, expired, consumed, revoked, or mismatched."""


@dataclass(frozen=True, slots=True)
class IssuedQaGrant:
    grant_id: DocumentTransformQaGrantId
    token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ConsumedQaGrant:
    user_id: UserId
    session_id: AuthSessionId
    user_session_revision: int
    user_email: str


def _digest(token: str) -> bytes:
    if not token or len(token) > _MAX_TOKEN_LENGTH or not token.isascii():
        raise QaGrantError
    return hashlib.sha256(token.encode()).digest()


async def _database_time(db: AsyncSession) -> datetime:
    now = await db.scalar(select(func.clock_timestamp()))
    if not isinstance(now, datetime):
        raise QaGrantError
    return now


async def issue_qa_grant(
    db: AsyncSession,
    *,
    user_id: UserId,
    session_id: AuthSessionId,
    release_sha: str,
    source_folder_id: FileId,
    destination_folder_id: FileId,
    mode: TransformMode,
    lifetime: timedelta = _MAX_GRANT_LIFETIME,
) -> IssuedQaGrant:
    _validate_binding(
        release_sha=release_sha,
        source_folder_id=source_folder_id,
        destination_folder_id=destination_folder_id,
    )
    if not timedelta(0) < lifetime <= _MAX_GRANT_LIFETIME:
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
    if (
        session.revoked_at is not None
        or session.expires_at <= now
        or not user.encrypted_refresh_token
    ):
        raise QaGrantError
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    expires_at = now + lifetime
    grant = DocumentTransformQaGrant(
        id=DocumentTransformQaGrantId(uuid.uuid7()),
        token_digest=_digest(token),
        user_id=user.id,
        user_session_id=session.id,
        user_session_revision=user.token_version,
        release_sha=release_sha,
        source_folder_id=source_folder_id,
        destination_folder_id=destination_folder_id,
        mode=mode.value,
        created_at=now,
        expires_at=expires_at,
    )
    db.add(grant)
    await db.flush()
    return IssuedQaGrant(grant_id=grant.id, token=token, expires_at=expires_at)


async def consume_qa_grant(
    db: AsyncSession,
    *,
    token: str,
    execution_name: str,
    user_email: str,
    release_sha: str,
    source_folder_id: FileId,
    destination_folder_id: FileId,
    mode: TransformMode,
) -> ConsumedQaGrant:
    _validate_binding(
        release_sha=release_sha,
        source_folder_id=source_folder_id,
        destination_folder_id=destination_folder_id,
    )
    if (
        not execution_name
        or len(execution_name) > _MAX_EXECUTION_NAME_LENGTH
        or not execution_name.isprintable()
        or execution_name.strip() != execution_name
    ):
        raise QaGrantError
    row = (
        await db.execute(
            select(DocumentTransformQaGrant, User, UserSession, func.clock_timestamp())
            .join(User, User.id == DocumentTransformQaGrant.user_id)
            .join(UserSession, UserSession.id == DocumentTransformQaGrant.user_session_id)
            .where(DocumentTransformQaGrant.token_digest.op("=")(_digest(token)))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    if row is None:
        raise QaGrantError
    grant, user, session, now = row
    if not _grant_is_live(grant, now) or not _identity_is_live(grant, user, session, now):
        raise QaGrantError
    if not _grant_matches_binding(
        grant,
        user,
        user_email=user_email,
        release_sha=release_sha,
        source_folder_id=source_folder_id,
        destination_folder_id=destination_folder_id,
        mode=mode,
    ):
        raise QaGrantError
    grant.consumed_at = now
    grant.consumed_by_execution = execution_name
    await db.flush()
    return ConsumedQaGrant(
        user_id=user.id,
        session_id=session.id,
        user_session_revision=user.token_version,
        user_email=user.email,
    )


def _validate_binding(
    *,
    release_sha: str,
    source_folder_id: FileId,
    destination_folder_id: FileId,
) -> None:
    if len(release_sha) != _RELEASE_SHA_LENGTH or any(
        character not in "0123456789abcdef" for character in release_sha
    ):
        raise QaGrantError
    if (
        not source_folder_id
        or not destination_folder_id
        or source_folder_id == destination_folder_id
    ):
        raise QaGrantError


def _grant_is_live(grant: DocumentTransformQaGrant, now: datetime) -> bool:
    return grant.consumed_at is None and grant.created_at <= now < grant.expires_at


def _identity_is_live(
    grant: DocumentTransformQaGrant,
    user: User,
    session: UserSession,
    now: datetime,
) -> bool:
    grant_identity_matches = (
        grant.user_id == user.id
        and grant.user_session_id == session.id
        and hmac.compare_digest(
            str(grant.user_session_revision),
            str(user.token_version),
        )
        and session.user_id == user.id
    )
    return (
        grant_identity_matches
        and session.revoked_at is None
        and session.expires_at > now
        and bool(user.encrypted_refresh_token)
    )


def _grant_matches_binding(
    grant: DocumentTransformQaGrant,
    user: User,
    *,
    user_email: str,
    release_sha: str,
    source_folder_id: str,
    destination_folder_id: str,
    mode: TransformMode,
) -> bool:
    return (
        hmac.compare_digest(user.email.casefold(), user_email.casefold())
        and grant.release_sha == release_sha
        and grant.source_folder_id == source_folder_id
        and grant.destination_folder_id == destination_folder_id
        and grant.mode == mode.value
    )
