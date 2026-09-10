from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from functools import wraps
import hmac
from typing import Final
from urllib.parse import urlencode

from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp.context import McpAuthorizationError
from app.mcp.grants import SignedGrantBinding, enroll_approved_client, validate_live_grant
from app.mcp.native_metadata import canonical_issuer, issuer_redirect
from app.mcp.native_schemas import (
    ConnectionView,
    ConsentDecision,
    ConsentStatus,
    ConsentView,
    NativeAuthorization,
    NativeCredential,
    NativeIntent,
    NativeOAuthError,
    WikiConsentIdentity,
)
from app.mcp.native_state import (
    NativePurpose,
    native_database_time,
    new_native_secret,
    valid_native_secret,
)
from app.mcp.native_state_port import NativeStatePort
from app.mcp.schemas import McpCapability
from app.mcp.storage import PostgresOAuthStorage
from app.models.mcp_grant import McpGrant, McpGrantId
from app.models.user import User


_MAX_BROWSER_BINDING = 256
_MAX_USER_GRANTS = 100
_BEARER: Final = "Bearer"


@asynccontextmanager
async def native_redaction() -> AsyncGenerator[None]:
    try:
        yield
    except Exception:  # ruff: ignore[blind-except] - Single native OAuth boundary redacts storage/provider errors before SDK logging.
        raise NativeOAuthError from None


def native_errors[**P, T](method: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    @wraps(method)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        async with native_redaction():
            return await method(*args, **kwargs)

    return wrapped


class NativeOAuthCore:
    def __init__(
        self,
        operations: PostgresOAuthStorage,
        state: NativeStatePort,
        resource: str,
        *,
        issuer: str,
    ) -> None:
        self.operations = operations
        self.state = state
        self.resource = resource
        self.issuer = canonical_issuer(issuer)

    @staticmethod
    async def current_user(db: AsyncSession, identity: WikiConsentIdentity) -> User:
        user = await db.scalar(
            select(User)
            .where(User.id == identity.user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        current_revision = user.token_version if user is not None else None
        if (
            user is None
            or current_revision != identity.session_revision
            or not user.encrypted_refresh_token
        ):
            raise NativeOAuthError
        return user

    async def live(self, db: AsyncSession, credential: NativeCredential) -> None:
        if not (await self.authorization(db, credential)).allowed:
            raise NativeOAuthError

    async def authorization(
        self, db: AsyncSession, credential: NativeCredential
    ) -> NativeAuthorization:
        if (
            credential.resource != self.resource
            or credential.issuer != self.issuer
            or credential.expires_at <= await native_database_time(db)
        ):
            return NativeAuthorization(allowed=False)
        try:
            principal = await validate_live_grant(
                db,
                SignedGrantBinding(
                    grant_id=credential.grant_id,
                    client_id=credential.client_id,
                    issued_at=credential.issued_at,
                    google_subject=credential.google_subject,
                ),
            )
        except McpAuthorizationError:
            return NativeAuthorization(allowed=False)
        if principal.user_id != credential.user_id or not set(credential.scopes).issubset(
            principal.capabilities
        ):
            return NativeAuthorization(allowed=False)
        if McpCapability.READ not in credential.scopes:
            return NativeAuthorization(allowed=False)
        return NativeAuthorization(allowed=True)

    async def client(self, db: AsyncSession, client_id: str) -> OAuthClientInformationFull:
        client = await self.state.get(
            db, NativePurpose.CLIENT, client_id, OAuthClientInformationFull
        )
        if client is None:
            raise NativeOAuthError
        return client

    async def intent(
        self, db: AsyncSession, key: str, browser: str, *, allow_decided: bool = False
    ) -> NativeIntent:
        if (
            not valid_native_secret(NativePurpose.INTENT, key)
            or not browser
            or len(browser) > _MAX_BROWSER_BINDING
        ):
            raise NativeOAuthError
        intent = await self.state.get(db, NativePurpose.INTENT, key, NativeIntent)
        if (
            intent is None
            or (intent.decided and not allow_decided)
            or not hmac.compare_digest(intent.browser_binding, browser)
        ):
            raise NativeOAuthError
        client = await self.client(db, intent.client_id)
        if (
            intent.redirect_uri not in [str(uri) for uri in (client.redirect_uris or [])]
            or intent.resource != self.resource
            or intent.issuer != self.issuer
        ):
            raise NativeOAuthError
        return intent

    @native_errors
    async def consent_view(
        self, key: str, *, identity: WikiConsentIdentity, browser: str
    ) -> ConsentView:
        async with self.operations.operation(f"native-intent:{key}") as db:
            await self.current_user(db, identity)
            intent = await self.intent(db, key, browser, allow_decided=True)
            if intent.user_id is not None and (
                intent.user_id != identity.user_id
                or intent.session_revision != identity.session_revision
            ):
                raise NativeOAuthError
            if intent.user_id is None:
                intent.user_id = identity.user_id
                intent.session_revision = identity.session_revision
                intent.csrf = new_native_secret(NativePurpose.INTENT)
                # Consent reads bind identity, but do not create a grant or extend expiry.
                await self.state.put(db, NativePurpose.INTENT, key, intent, intent.expires_at)
            client = await self.client(db, intent.client_id)
            if intent.csrf is None:
                raise NativeOAuthError
            return ConsentView(
                client_id=intent.client_id,
                client_name=client.client_name or "MCP Client",
                scopes=intent.scopes,
                csrf=intent.csrf,
                status=intent.decision or "pending",
            )

    @native_errors
    async def consent_status(
        self, key: str, *, identity: WikiConsentIdentity, browser: str
    ) -> ConsentStatus:
        if not valid_native_secret(NativePurpose.INTENT, key):
            raise NativeOAuthError
        async with self.operations.operation(f"native-intent:{key}") as db:
            await self.current_user(db, identity)
            saved = await self.state.get(db, NativePurpose.INTENT, key, NativeIntent)
            if saved is None:
                return ConsentStatus(status="expired", restart_required=True)
            intent = await self.intent(db, key, browser, allow_decided=True)
            if intent.user_id is not None and (
                intent.user_id != identity.user_id
                or intent.session_revision != identity.session_revision
            ):
                raise NativeOAuthError
            return ConsentStatus(
                status=intent.decision or "pending", restart_required=intent.decided
            )

    @native_errors
    async def decide(
        self,
        key: str,
        *,
        identity: WikiConsentIdentity,
        browser: str,
        csrf: str,
        approve: bool,
        transform_approved: bool = False,
    ) -> str:
        ConsentDecision(approve=approve, transform_approved=transform_approved)
        async with self.operations.operation(f"native-intent:{key}") as db:
            user = await self.current_user(db, identity)
            intent = await self.intent(db, key, browser)
            if (
                intent.user_id != identity.user_id
                or intent.session_revision != identity.session_revision
                or intent.csrf is None
                or not hmac.compare_digest(intent.csrf, csrf)
            ):
                raise NativeOAuthError
            if transform_approved and McpCapability.TRANSFORM not in intent.scopes:
                raise NativeOAuthError
            intent.decided = True
            intent.decision = "approved" if approve else "denied"
            await self.state.put(db, NativePurpose.INTENT, key, intent, intent.expires_at)
            parameters = {"state": intent.state} if intent.state is not None else {}
            if not approve:
                parameters["error"] = "access_denied"
            else:
                count = await db.scalar(
                    select(func.count())
                    .select_from(McpGrant)
                    .where(
                        McpGrant.user_id == user.id,
                        McpGrant.client_id != intent.client_id,
                        McpGrant.user_session_revision == identity.session_revision,
                        McpGrant.revoked_at.is_(None),
                        McpGrant.expires_at > await native_database_time(db),
                    )
                )
                if (count or 0) >= _MAX_USER_GRANTS:
                    raise NativeOAuthError
                grant = await enroll_approved_client(
                    db,
                    approving_user_id=user.id,
                    client_id=intent.client_id,
                    transform_approved=transform_approved,
                )
                await self.state.promote_client(db, intent.client_id, grant.expires_at)
                scopes = [McpCapability.READ] + (
                    [McpCapability.TRANSFORM] if transform_approved else []
                )
                now = await native_database_time(db)
                credential = NativeCredential(
                    issuer=self.issuer,
                    grant_id=grant.id,
                    client_id=intent.client_id,
                    user_id=user.id,
                    google_subject=user.google_id,
                    issued_at=int(grant.created_at.timestamp()),
                    expires_at=now + timedelta(minutes=5),
                    resource=self.resource,
                    scopes=scopes,
                    challenge=intent.challenge,
                    redirect_uri=intent.redirect_uri,
                    redirect_explicit=intent.redirect_explicit,
                )
                code = new_native_secret(NativePurpose.CODE)
                await self.state.put(
                    db, NativePurpose.CODE, code, credential, credential.expires_at
                )
                parameters["code"] = code
            return issuer_redirect(f"{intent.redirect_uri}?{urlencode(parameters)}", self.issuer)

    async def issue(
        self, db: AsyncSession, credential: NativeCredential, scopes: list[McpCapability]
    ) -> OAuthToken:
        await self.live(db, credential)
        if (grant := await db.get(McpGrant, credential.grant_id)) is None:
            raise NativeOAuthError
        now = await native_database_time(db)
        access_expiry = min(now + timedelta(minutes=10), grant.expires_at)
        refresh_expiry = min(now + timedelta(days=30), grant.expires_at)
        access, refresh = (
            new_native_secret(NativePurpose.ACCESS),
            new_native_secret(NativePurpose.REFRESH),
        )
        for purpose, key, expiry in (
            (NativePurpose.ACCESS, access, access_expiry),
            (NativePurpose.REFRESH, refresh, refresh_expiry),
        ):
            saved = credential.model_copy(
                update={
                    "scopes": scopes,
                    "expires_at": expiry,
                    "consumed": False,
                    "challenge": None,
                    "redirect_uri": None,
                }
            )
            await self.state.put(db, purpose, key, saved, expiry)
        return OAuthToken(
            access_token=access,
            token_type=_BEARER,
            expires_in=max(1, int((access_expiry - now).total_seconds())),
            refresh_token=refresh,
            scope=" ".join(scopes),
        )

    @native_errors
    async def list_connections(self, identity: WikiConsentIdentity) -> list[ConnectionView]:
        async with self.operations.operation(f"native-user:{identity.user_id}") as db:
            await self.current_user(db, identity)
            now = await native_database_time(db)
            grants = await db.scalars(
                select(McpGrant)
                .where(
                    McpGrant.user_id == identity.user_id,
                    McpGrant.revoked_at.is_(None),
                    McpGrant.expires_at > now,
                    McpGrant.user_session_revision == identity.session_revision,
                )
                .limit(_MAX_USER_GRANTS)
            )
            return [
                ConnectionView(
                    grant_id=grant.id,
                    client_id=grant.client_id,
                    capabilities=[McpCapability.READ]
                    + ([McpCapability.TRANSFORM] if grant.transform_allowed else []),
                    expires_at=grant.expires_at,
                )
                for grant in grants
            ]

    @native_errors
    async def disconnect(self, identity: WikiConsentIdentity, grant_id: McpGrantId) -> None:
        async with self.operations.operation(f"native-grant:{grant_id}") as db:
            await self.current_user(db, identity)
            await db.execute(
                update(McpGrant)
                .where(
                    McpGrant.id == grant_id,
                    McpGrant.user_id == identity.user_id,
                    McpGrant.revoked_at.is_(None),
                )
                .values(revoked_at=await native_database_time(db))
            )
