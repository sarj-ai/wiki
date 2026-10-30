from dataclasses import dataclass
from datetime import datetime, timedelta
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.identifiers import GoogleUserId, UserId
from app.mcp.context import McpAuthorizationError, ValidatedMcpPrincipal
from app.mcp.identity import McpClientId
from app.mcp.schemas import McpCapability
from app.models.mcp_grant import McpGrant, McpGrantId
from app.models.user import User


_MAX_GRANT_LIFETIME = timedelta(days=30)
_CLIENT_ID_LIMIT = 1024


@dataclass(frozen=True, slots=True)
class SignedGrantBinding:
    # Construct only from verified signatures or authenticated encrypted native
    # credential state, never from tool arguments or unsigned client claims.
    grant_id: McpGrantId
    client_id: McpClientId
    issued_at: int
    google_subject: GoogleUserId


def _check_client(client_id: McpClientId) -> None:
    if not client_id or len(client_id) > _CLIENT_ID_LIMIT or not client_id.isprintable():
        raise McpAuthorizationError
    if client_id.strip() != client_id:
        raise McpAuthorizationError


async def _database_time(db: AsyncSession) -> datetime:
    now = await db.scalar(select(func.clock_timestamp()))
    if not isinstance(now, datetime):
        raise McpAuthorizationError
    return now


async def enroll_approved_client(
    db: AsyncSession,
    *,
    approving_user_id: UserId,
    client_id: McpClientId,
    lifetime: timedelta = _MAX_GRANT_LIFETIME,
    transform_approved: bool = False,
) -> McpGrant:
    # The caller must have verified explicit, CSRF-protected server-side consent.
    # This function does not commit: grant and authorization issuance share a transaction.
    _check_client(client_id)
    if type(transform_approved) is not bool or not timedelta(0) < lifetime <= _MAX_GRANT_LIFETIME:
        raise McpAuthorizationError
    user = await db.scalar(
        select(User)
        .where(User.id == approving_user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None or not user.encrypted_refresh_token:
        raise McpAuthorizationError
    now = await _database_time(db)
    await _revoke_client(db, approving_user_id, client_id, now)
    grant = McpGrant(
        id=McpGrantId(uuid.uuid7()),
        user_id=approving_user_id,
        client_id=client_id,
        user_session_revision=user.token_version,
        transform_allowed=transform_approved,
        created_at=now,
        expires_at=now + lifetime,
    )
    db.add(grant)
    await db.flush()
    return grant


async def _revoke_client(
    db: AsyncSession, user_id: UserId, client_id: McpClientId, now: datetime
) -> None:
    await db.execute(
        update(McpGrant)
        .where(
            McpGrant.user_id == user_id,
            McpGrant.client_id == client_id,
            McpGrant.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )


async def revoke_client(db: AsyncSession, *, user_id: UserId, client_id: McpClientId) -> None:
    # Revocation stops future calls, not already admitted jobs. Never logs out Wiki.
    _check_client(client_id)
    user = await db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is not None:
        await _revoke_client(db, user_id, client_id, await _database_time(db))


async def validate_live_grant(
    db: AsyncSession,
    binding: SignedGrantBinding,
    *,
    required_capability: McpCapability = McpCapability.READ,
) -> ValidatedMcpPrincipal:
    _check_client(binding.client_id)
    if type(binding.issued_at) is not int or binding.issued_at < 0:
        raise McpAuthorizationError
    # populate_existing prevents a long-lived identity map from hiding revocation/logout.
    row = (
        await db.execute(
            select(McpGrant, User, func.clock_timestamp())
            .join(User, User.id == McpGrant.user_id)
            .where(McpGrant.id == binding.grant_id)
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    if row is None:
        raise McpAuthorizationError
    grant, user, now = row
    current_revision = user.token_version
    if (
        grant.client_id != binding.client_id
        or user.google_id != binding.google_subject
        or grant.revoked_at is not None
        or grant.user_session_revision != current_revision
        or not user.encrypted_refresh_token
    ):
        raise McpAuthorizationError
    if (
        grant.expires_at <= now
        or grant.created_at > now
        or binding.issued_at < int(grant.created_at.timestamp())
        or binding.issued_at > int(now.timestamp())
    ):
        raise McpAuthorizationError
    capabilities = {McpCapability.READ}
    if grant.transform_allowed:
        capabilities.add(McpCapability.TRANSFORM)
    principal = ValidatedMcpPrincipal(
        user_id=user.id,
        user_session_revision=user.token_version,
        capabilities=frozenset(capabilities),
    )
    principal.require(required_capability)
    return principal
