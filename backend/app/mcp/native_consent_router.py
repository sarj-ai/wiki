import asyncio
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import secrets
from typing import Annotated, Any, override
from urllib.parse import parse_qsl, urlsplit, urlunsplit
import uuid

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import Field
from starlette.responses import JSONResponse, Response

from app.auth.dependencies import auth_service_dep
from app.auth.errors import AuthError
from app.auth.service import AuthService
from app.mcp.native_core import NativeOAuthCore
from app.mcp.native_metadata import canonical_issuer
from app.mcp.native_schemas import (
    ConnectionView,
    ConsentStatus,
    ConsentView,
    NativeOAuthError,
    WikiConsentIdentity,
)
from app.mcp.native_state import NativePurpose, valid_native_secret
from app.mcp.redirect_policy import McpRedirectPolicy
from app.mcp.schemas import McpModel
from app.models.mcp_grant import McpGrantId


_BODY_LIMIT = 4096
_HEADERS_LIMIT = 16_384
_HEADER_COUNT = 64
_PATH_LIMIT = 512
_NONCE = re.compile(r"[A-Za-z0-9_-]{43}")
_STATE_LIMIT = 2048
_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
}


@dataclass(frozen=True, slots=True)
class NativeConsentSettings:
    frontend_origin: str
    redirect_policy: McpRedirectPolicy
    issuer: str
    nonce_cookie: str = "__Host-wiki_mcp_nonce"

    def __post_init__(self) -> None:
        object.__setattr__(self, "issuer", canonical_issuer(self.issuer))
        parsed = urlsplit(self.frontend_origin)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or self.frontend_origin != f"{parsed.scheme}://{parsed.netloc}"
            or parsed.username is not None
        ):
            raise NativeOAuthError
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.nonce_cookie):
            raise NativeOAuthError


@dataclass(frozen=True, slots=True)
class _BrowserIdentity:
    identity: WikiConsentIdentity
    account_email: str
    browser_binding: str
    disconnect_csrf: str


class _DecisionBody(McpModel):
    csrf: str = Field(min_length=1, max_length=128, strict=True)
    approve: bool = Field(strict=True)
    transform_approved: bool = Field(default=False, strict=True)


class ConsentBrowserView(ConsentView):
    account_email: str


class ConsentRedirect(McpModel):
    redirect_uri: str


class ConsentConnections(McpModel):
    connections: list[ConnectionView]
    csrf: str


class ConsentFailure(McpModel):
    code: str = "mcp_consent_unavailable"
    detail: str = "Wiki connection request unavailable"


_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    code: {"model": ConsentFailure} for code in (400, 401, 403, 413, 503)
}


def create_native_consent_router(
    *,
    settings: NativeConsentSettings,
    core_dependency: Callable[..., NativeOAuthCore | Awaitable[NativeOAuthCore]],
    auth_dependency: Callable[..., AuthService | Awaitable[AuthService]] = auth_service_dep,
) -> APIRouter:
    class ConsentRoute(APIRoute):
        @override
        def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
            handler = super().get_route_handler()

            async def guarded(request: Request) -> Response:
                try:
                    await _prepare(request, settings)
                    response = await handler(request)
                except HTTPException as error:
                    response = _error(error.status_code)
                except ValueError, RequestValidationError, TimeoutError, RecursionError:
                    response = _error(400)
                response.headers.update(_SECURITY_HEADERS)
                return response

            return guarded

    router = APIRouter(prefix="/integrations/mcp", route_class=ConsentRoute)

    async def browser(
        request: Request, response: Response, auth: Annotated[AuthService, Depends(auth_dependency)]
    ) -> _BrowserIdentity:
        access = _cookie(request, "access_token")
        nonce = _cookie(request, settings.nonce_cookie)
        if not access:
            raise HTTPException(status_code=401)
        management = request.method == "GET" and request.url.path == "/integrations/mcp/connections"
        if (nonce is not None and not _NONCE.fullmatch(nonce)) or (
            nonce is None and not management
        ):
            raise HTTPException(status_code=403)
        outcomes = await asyncio.gather(auth.resolve_current_user(access), return_exceptions=True)
        user = outcomes[0]
        if isinstance(user, AuthError):
            raise HTTPException(status_code=401)
        if isinstance(user, Exception):
            raise HTTPException(status_code=503)
        if isinstance(user, BaseException):
            raise user
        if nonce is None:
            nonce = secrets.token_urlsafe(32)
            response.set_cookie(
                settings.nonce_cookie,
                nonce,
                max_age=600,
                secure=True,
                httponly=True,
                samesite="lax",
                path="/",
            )
        return _BrowserIdentity(
            WikiConsentIdentity(user_id=user.id, session_revision=user.token_version),
            user.email,
            hashlib.sha256(nonce.encode()).hexdigest(),
            hashlib.sha256(b"wiki-mcp-disconnect-v1:" + nonce.encode()).hexdigest(),
        )

    @router.get(
        "/consent/{intent}",
        status_code=200,
        response_model=ConsentBrowserView,
        responses=_ERROR_RESPONSES,
    )
    async def view(
        intent: Annotated[str, Path(min_length=1, max_length=128)],
        actor: Annotated[_BrowserIdentity, Depends(browser)],
        core: Annotated[NativeOAuthCore, Depends(core_dependency)],
    ) -> ConsentBrowserView:
        _intent(intent)
        result = await core.consent_view(
            intent, identity=actor.identity, browser=actor.browser_binding
        )
        return ConsentBrowserView(**result.model_dump(), account_email=actor.account_email)

    @router.get(
        "/consent/{intent}/status",
        status_code=200,
        response_model=ConsentStatus,
        responses=_ERROR_RESPONSES,
    )
    async def status(
        intent: Annotated[str, Path(min_length=1, max_length=128)],
        actor: Annotated[_BrowserIdentity, Depends(browser)],
        core: Annotated[NativeOAuthCore, Depends(core_dependency)],
    ) -> ConsentStatus:
        _intent(intent)
        return await core.consent_status(
            intent, identity=actor.identity, browser=actor.browser_binding
        )

    @router.post(
        "/consent/{intent}",
        status_code=200,
        response_model=ConsentRedirect,
        responses=_ERROR_RESPONSES,
    )
    async def decide(
        intent: Annotated[str, Path(min_length=1, max_length=128)],
        request: Request,
        actor: Annotated[_BrowserIdentity, Depends(browser)],
        core: Annotated[NativeOAuthCore, Depends(core_dependency)],
    ) -> ConsentRedirect:
        _intent(intent)
        body = _DecisionBody.model_validate(
            json.loads(request.state.native_consent_body, object_pairs_hook=_unique_fields)
        )
        if not valid_native_secret(NativePurpose.INTENT, body.csrf):
            raise NativeOAuthError
        redirect = await core.decide(
            intent,
            identity=actor.identity,
            browser=actor.browser_binding,
            csrf=body.csrf,
            approve=body.approve,
            transform_approved=body.transform_approved,
        )
        _redirect(redirect, settings, approved=body.approve)
        return ConsentRedirect(redirect_uri=redirect)

    @router.get(
        "/connections",
        status_code=200,
        response_model=ConsentConnections,
        responses=_ERROR_RESPONSES,
    )
    async def connections(
        actor: Annotated[_BrowserIdentity, Depends(browser)],
        core: Annotated[NativeOAuthCore, Depends(core_dependency)],
    ) -> ConsentConnections:
        result = await core.list_connections(actor.identity)
        return ConsentConnections(connections=result, csrf=actor.disconnect_csrf)

    @router.delete(
        "/connections/{grant_id}",
        status_code=204,
        response_class=Response,
        response_model=None,
        responses=_ERROR_RESPONSES,
    )
    async def disconnect(
        grant_id: Annotated[str, Path(min_length=36, max_length=36)],
        request: Request,
        actor: Annotated[_BrowserIdentity, Depends(browser)],
        core: Annotated[NativeOAuthCore, Depends(core_dependency)],
    ) -> Response:
        if not hmac.compare_digest(
            request.headers.get("x-mcp-csrf", "").encode(), actor.disconnect_csrf.encode()
        ):
            raise HTTPException(status_code=403)
        try:
            parsed = uuid.UUID(grant_id)
        except ValueError:
            raise NativeOAuthError from None
        if str(parsed) != grant_id:
            raise NativeOAuthError
        await core.disconnect(actor.identity, McpGrantId(parsed))
        return Response(status_code=204)

    return router


def _error(status: int) -> JSONResponse:
    return JSONResponse(
        {"code": "mcp_consent_unavailable", "detail": "Wiki connection request unavailable"},
        status_code=status,
    )


async def _prepare(request: Request, settings: NativeConsentSettings) -> None:
    if len(request.scope.get("path", "")) > _PATH_LIMIT:
        raise HTTPException(status_code=400)
    headers = request.scope.get("headers", [])
    if (
        len(headers) > _HEADER_COUNT
        or sum(len(key) + len(value) for key, value in headers) > _HEADERS_LIMIT
    ):
        raise HTTPException(status_code=400)
    for name in (
        b"cookie",
        b"origin",
        b"authorization",
        b"content-type",
        b"content-length",
        b"x-mcp-csrf",
    ):
        if sum(key.lower() == name for key, _value in headers) > 1:
            raise HTTPException(status_code=400)
    if request.headers.get("origin") != settings.frontend_origin:
        raise HTTPException(status_code=403)
    if request.scope.get("query_string"):
        raise HTTPException(status_code=400)
    if (
        request.method == "POST"
        and request.headers.get("content-type", "").split(";", 1)[0] != "application/json"
    ):
        raise HTTPException(status_code=400)
    body = bytearray()
    async with asyncio.timeout(10):
        async for chunk in request.stream():
            if len(body) + len(chunk) > _BODY_LIMIT:
                raise HTTPException(status_code=413)
            body.extend(chunk)
    if request.method != "POST" and body:
        raise HTTPException(status_code=400)
    request.state.native_consent_body = bytes(body)


def _cookie(request: Request, name: str) -> str | None:
    matches = [
        stripped.split("=", 1)[1]
        for part in request.headers.get("cookie", "").split(";")
        if (stripped := part.strip()).startswith(f"{name}=")
    ]
    if len(matches) > 1:
        raise HTTPException(status_code=400)
    return matches[0] if matches else None


def _intent(value: str) -> None:
    if not valid_native_secret(NativePurpose.INTENT, value):
        raise NativeOAuthError


def _unique_fields(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise NativeOAuthError
        result[key] = value
    return result


def _redirect(value: str, settings: NativeConsentSettings, *, approved: bool) -> None:
    if len(value) > _BODY_LIMIT or not value.isascii():
        raise NativeOAuthError
    parsed = urlsplit(value)
    if parsed.fragment:
        raise NativeOAuthError
    settings.redirect_policy.validate_callback(urlunsplit(parsed._replace(query="")))
    pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=3)
    parameters = _unique_fields(pairs)
    if parameters.get("iss") != settings.issuer:
        raise NativeOAuthError
    if "state" in parameters and (
        not isinstance(parameters["state"], str) or len(parameters["state"]) > _STATE_LIMIT
    ):
        raise NativeOAuthError
    if approved:
        code = parameters.get("code")
        if (
            not set(parameters) <= {"code", "state", "iss"}
            or not isinstance(code, str)
            or not valid_native_secret(NativePurpose.CODE, code)
        ):
            raise NativeOAuthError
    elif (
        not set(parameters) <= {"error", "state", "iss"}
        or parameters.get("error") != "access_denied"
    ):
        raise NativeOAuthError
