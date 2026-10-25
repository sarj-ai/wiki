from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
import hmac
import re
from typing import Final, Literal, override
from urllib.parse import urlencode, urlsplit

from fastmcp.server.auth import AccessToken, OAuthProvider
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.routing import Route

from app.mcp.identity import McpClientId
from app.mcp.native_core import NativeOAuthCore, native_redaction
from app.mcp.native_metadata import canonical_issuer, native_metadata_routes
from app.mcp.native_request_context import NativeOAuthRequestContext
from app.mcp.native_schemas import NativeCredential, NativeIntent, NativeOAuthError
from app.mcp.native_state import (
    NativePurpose,
    native_database_time,
    new_native_secret,
    valid_native_secret,
)
from app.mcp.redirect_policy import McpRedirectPolicy
from app.mcp.schemas import McpCapability
from app.models.mcp_grant import McpGrant
from app.models.user import User


_PUBLIC_CLIENT_AUTH = "none"
_MAX_CLIENT_NAME = 128
_MAX_STATE = 1024
_DENIED: Final = "access_denied"
_REGISTRATION_INVALID: Final = "invalid_client_metadata"
_GRANT_INVALID: Final = "invalid_grant"
_SAFE_AUTH_ERROR = "Wiki authorization unavailable"


@asynccontextmanager
async def oauth_boundary(kind: Literal["authorize", "token", "register"]) -> AsyncGenerator[None]:
    try:
        async with native_redaction():
            yield
    except NativeOAuthError:
        if kind == "authorize":
            raise AuthorizeError(_DENIED, _SAFE_AUTH_ERROR) from None
        if kind == "register":
            raise RegistrationError(_REGISTRATION_INVALID, _SAFE_AUTH_ERROR) from None
        raise TokenError(_GRANT_INVALID, _SAFE_AUTH_ERROR) from None


class NativeWikiOAuthProvider(OAuthProvider):
    def __init__(
        self,
        *,
        core: NativeOAuthCore,
        base_url: str,
        consent_url: str,
        redirects: McpRedirectPolicy,
        request_guard: Callable[[str], NativeOAuthRequestContext],
        registration_admission: Callable[[], Awaitable[None]],
        issuer_responses_enabled: bool = False,
    ) -> None:
        if canonical_issuer(base_url) != core.issuer:
            raise NativeOAuthError
        for url in (base_url, consent_url, core.resource):
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or not parsed.netloc
                or parsed.query
                or parsed.fragment
                or parsed.username
            ):
                raise NativeOAuthError
        super().__init__(
            base_url=base_url,
            required_scopes=[McpCapability.READ],
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=list(McpCapability), default_scopes=[McpCapability.READ]
            ),
            revocation_options=RevocationOptions(enabled=True),
        )
        self.core = core
        self.consent_url = consent_url
        self.redirects = redirects
        self.request_guard = request_guard
        self.registration_admission = registration_admission
        self.issuer_responses_enabled = issuer_responses_enabled

    @override
    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        resource_base = str(self.resource_base_url or self.base_url)
        advertised = (
            f"{resource_base.rstrip('/')}/{mcp_path.lstrip('/')}" if mcp_path else resource_base
        )
        if advertised != self.core.resource:
            raise NativeOAuthError
        return native_metadata_routes(
            self,
            super().get_routes(mcp_path),
            issuer_responses_enabled=self.issuer_responses_enabled,
        )

    def request(
        self, endpoint: Literal["authorize", "token", "register", "revoke"]
    ) -> NativeOAuthRequestContext:
        context = self.request_guard(self.core.resource)
        if context.endpoint != endpoint:
            raise NativeOAuthError
        return context

    @override
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        async with native_redaction():
            return await self._get_client(client_id)

    async def _get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        if re.fullmatch(r"[A-Za-z0-9_-]{1,255}", client_id) is None:
            return None
        async with self.core.operations.operation(f"native-client:{client_id}") as db:
            return await self.core.state.get(
                db, NativePurpose.CLIENT, client_id, OAuthClientInformationFull
            )

    @override
    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        async with oauth_boundary("register"):
            self.request("register")
            self.redirects.validate_registration(
                client_info.client_id, tuple(str(uri) for uri in (client_info.redirect_uris or []))
            )
            if (
                client_info.token_endpoint_auth_method != _PUBLIC_CLIENT_AUTH
                or client_info.client_secret is not None
                or client_info.grant_types != ["authorization_code", "refresh_token"]
                or client_info.response_types != ["code"]
            ):
                raise NativeOAuthError
            if (
                client_info.client_name is not None
                and len(client_info.client_name) > _MAX_CLIENT_NAME
            ):
                raise NativeOAuthError
            scopes = client_info.scope.split() if client_info.scope else [McpCapability.READ]
            if (
                not set(scopes).issubset(McpCapability)
                or McpCapability.READ not in scopes
                or len(scopes) != len(set(scopes))
            ):
                raise NativeOAuthError
            # Persist only the supported public-client metadata, not arbitrary logos/JWKs/URLs.
            saved = OAuthClientInformationFull(
                client_id=client_info.client_id,
                redirect_uris=client_info.redirect_uris,
                client_name=client_info.client_name,
                token_endpoint_auth_method=_PUBLIC_CLIENT_AUTH,
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                scope=" ".join(scopes),
            )
        # Admission commits independently before client persistence. Do not put
        # its typed failures through SDK RegistrationError's blanket HTTP 400.
        await self.registration_admission()
        async with (
            oauth_boundary("register"),
            self.core.operations.operation(f"native-client:{saved.client_id}") as db,
        ):
            if (
                await self.core.state.get(
                    db, NativePurpose.CLIENT, client_info.client_id, OAuthClientInformationFull
                )
                is not None
            ):
                raise NativeOAuthError
            await self.core.state.put(
                db,
                NativePurpose.CLIENT,
                client_info.client_id,
                saved,
                await native_database_time(db) + timedelta(minutes=30),
            )

    @override
    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        async with oauth_boundary("authorize"):
            context = self.request("authorize")
            if (
                not context.browser_binding
                or client.client_id is None
                or params.resource != self.core.resource
                or not re.fullmatch(r"[A-Za-z0-9_-]{43}", params.code_challenge)
                or len(params.state or "") > _MAX_STATE
            ):
                raise NativeOAuthError
            scopes = [McpCapability(scope) for scope in (params.scopes or [McpCapability.READ])]
            if McpCapability.READ not in scopes or len(scopes) != len(set(scopes)):
                raise NativeOAuthError
            async with self.core.operations.operation(f"native-client:{client.client_id}") as db:
                registered = await self.core.client(db, client.client_id)
                redirect = str(params.redirect_uri)
                try:
                    self.redirects.validate_authorization(
                        client_id=client.client_id,
                        uri=redirect,
                        registered_callbacks=tuple(
                            str(uri) for uri in (registered.redirect_uris or [])
                        ),
                    )
                except ValueError as error:
                    raise NativeOAuthError from error
                if not set(scopes).issubset((registered.scope or "").split()):
                    raise NativeOAuthError
                key = new_native_secret(NativePurpose.INTENT)
                intent = NativeIntent(
                    issuer=self.core.issuer,
                    expires_at=await native_database_time(db) + timedelta(minutes=10),
                    client_id=McpClientId(client.client_id),
                    redirect_uri=redirect,
                    redirect_explicit=params.redirect_uri_provided_explicitly,
                    resource=self.core.resource,
                    state=params.state,
                    challenge=params.code_challenge,
                    scopes=scopes,
                    browser_binding=context.browser_binding,
                )
                await self.core.state.put(db, NativePurpose.INTENT, key, intent, intent.expires_at)
                return f"{self.consent_url}?{urlencode({'intent': key})}"

    async def credential(self, purpose: NativePurpose, key: str) -> NativeCredential | None:
        if not valid_native_secret(purpose, key):
            return None
        async with self.core.operations.operation(f"native-lookup:{key}") as db:
            return await self.core.state.get(db, purpose, key, NativeCredential)

    @staticmethod
    def code_value(key: str, value: NativeCredential) -> AuthorizationCode:
        if value.challenge is None or value.redirect_uri is None:
            raise NativeOAuthError
        return AuthorizationCode(
            code=key,
            client_id=value.client_id,
            scopes=list(value.scopes),
            expires_at=value.expires_at.timestamp(),
            code_challenge=value.challenge,
            redirect_uri=AnyUrl(value.redirect_uri),
            redirect_uri_provided_explicitly=value.redirect_explicit,
            resource=value.resource,
            subject=str(value.user_id),
        )

    @override
    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        async with native_redaction():
            return await self._load_code(client, authorization_code)

    async def _load_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        value = await self.credential(NativePurpose.CODE, authorization_code)
        if value is None or value.consumed or value.client_id != client.client_id:
            return None
        return self.code_value(authorization_code, value)

    @override
    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        async with oauth_boundary("token"):
            self.request("token")
            if (hint := await self.credential(NativePurpose.CODE, authorization_code.code)) is None:
                raise NativeOAuthError
            async with self.core.operations.operation(f"native-grant:{hint.grant_id}") as db:
                value = await self.core.state.get(
                    db, NativePurpose.CODE, authorization_code.code, NativeCredential
                )
                if (
                    value is None
                    or value.consumed
                    or value.client_id != client.client_id
                    or not hmac.compare_digest(
                        self.code_value(authorization_code.code, value).model_dump_json().encode(),
                        authorization_code.model_dump_json().encode(),
                    )
                ):
                    raise NativeOAuthError
                await self.core.client(db, value.client_id)
                await self.core.live(db, value)
                value.consumed = True
                await self.core.state.put(
                    db, NativePurpose.CODE, authorization_code.code, value, value.expires_at
                )
                return await self.core.issue(db, value, value.scopes)

    @override
    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        async with native_redaction():
            return await self._load_refresh(client, refresh_token)

    async def _load_refresh(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        hint = await self.credential(NativePurpose.REFRESH, refresh_token)
        if hint is None or hint.client_id != client.client_id:
            return None
        async with self.core.operations.operation(f"native-grant:{hint.grant_id}") as db:
            value = await self.core.state.get(
                db, NativePurpose.REFRESH, refresh_token, NativeCredential
            )
            if value is None or value.client_id != client.client_id:
                return None
            if value.consumed:
                await self._revoke_family(db, value)
                return None  # Normal context exit commits replay revocation.
            if not (await self.core.authorization(db, value)).allowed:
                return None
            return RefreshToken(
                token=refresh_token,
                client_id=value.client_id,
                scopes=list(value.scopes),
                expires_at=int(value.expires_at.timestamp()),
                subject=str(value.user_id),
            )

    @staticmethod
    async def _revoke_family(db: AsyncSession, value: NativeCredential) -> None:
        await db.execute(
            update(McpGrant)
            .where(
                McpGrant.id == value.grant_id,
                McpGrant.client_id == value.client_id,
                McpGrant.revoked_at.is_(None),
            )
            .values(revoked_at=await native_database_time(db))
        )

    @override
    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        async with oauth_boundary("token"):
            self.request("token")
            if (hint := await self.credential(NativePurpose.REFRESH, refresh_token.token)) is None:
                raise NativeOAuthError
            async with self.core.operations.operation(f"native-grant:{hint.grant_id}") as db:
                value = await self.core.state.get(
                    db, NativePurpose.REFRESH, refresh_token.token, NativeCredential
                )
                if (
                    value is None
                    or value.client_id != client.client_id
                    or refresh_token.client_id != value.client_id
                ):
                    raise NativeOAuthError
                if value.consumed:
                    await self._revoke_family(db, value)
                else:
                    selected = [McpCapability(scope) for scope in scopes]
                    if (
                        not set(selected).issubset(value.scopes)
                        or McpCapability.READ not in selected
                        or len(selected) != len(set(selected))
                    ):
                        raise NativeOAuthError
                    await self.core.client(db, value.client_id)
                    await self.core.live(db, value)
                    value.consumed = True
                    await self.core.state.put(
                        db, NativePurpose.REFRESH, refresh_token.token, value, value.expires_at
                    )
                    return await self.core.issue(db, value, selected)
            # Replay revocation must commit before the public error is raised.
            raise NativeOAuthError

    @override
    async def load_access_token(self, token: str) -> AccessToken | None:
        async with native_redaction():
            return await self._load_access(token)

    async def _load_access(self, token: str) -> AccessToken | None:
        if (hint := await self.credential(NativePurpose.ACCESS, token)) is None:
            return None
        async with self.core.operations.operation(f"native-grant:{hint.grant_id}") as db:
            if (
                value := await self.core.state.get(
                    db, NativePurpose.ACCESS, token, NativeCredential
                )
            ) is None:
                return None
            await self.core.client(db, value.client_id)
            if not (await self.core.authorization(db, value)).allowed:
                return None
            if (user := await db.get(User, value.user_id)) is None:
                return None
            return AccessToken(
                token=token,
                client_id=value.client_id,
                scopes=list(value.scopes),
                expires_at=int(value.expires_at.timestamp()),
                resource=value.resource,
                subject=str(value.user_id),
                claims={
                    "grant_id": str(value.grant_id),
                    "user_id": str(value.user_id),
                    "user_session_revision": user.token_version,
                },
            )

    @override
    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        async with oauth_boundary("token"):
            self.request("revoke")
            purpose = (
                NativePurpose.REFRESH if isinstance(token, RefreshToken) else NativePurpose.ACCESS
            )
            value = await self.credential(purpose, token.token)
            if value is None or value.client_id != token.client_id:
                return
            async with self.core.operations.operation(f"native-grant:{value.grant_id}") as db:
                await db.execute(
                    update(McpGrant)
                    .where(
                        McpGrant.id == value.grant_id,
                        McpGrant.client_id == value.client_id,
                        McpGrant.revoked_at.is_(None),
                    )
                    .values(revoked_at=await native_database_time(db))
                )
