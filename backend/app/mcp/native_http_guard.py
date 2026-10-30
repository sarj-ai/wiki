from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import logging  # Redact the MCP SDK stdlib logger before OAuth secrets reach handlers.
import re
import secrets
from typing import override
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse, Response
from starlette.status import HTTP_400_BAD_REQUEST
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.mcp.native_admission import (
    RegistrationAdmissionDeniedError,
    RegistrationAdmissionUnavailableError,
)
from app.mcp.native_metadata import canonical_issuer, issuer_redirect
from app.mcp.native_request_context import (
    NativeOAuthEndpoint,
    NativeOAuthRequestContext,
    NativeOAuthRequestError,
    native_oauth_request,
)
from app.mcp.redirect_policy import McpRedirectPolicy, validate_registered_client_id


_BODY_LIMIT = 16_384
_HEADER_LIMIT = 64
_UNIQUE_HEADERS = frozenset({b"content-type", b"content-length", b"authorization", b"cookie"})
_NAME_LIMIT = 64
_PRINTABLE_START = 32
_DELETE_CHARACTER = 127
_JSON_DEPTH = 2
_CALLBACK_LIMIT = 4
_CLIENT_NAME_LIMIT = 256
_DEFAULT_EXCHANGE_PATH = "/token"
_PUBLIC_AUTH_METHOD = "none"
_RESPONSE_LIMIT = 65_536
_OAUTH_ERRORS = frozenset(
    {
        "invalid_request",
        "invalid_client",
        "invalid_grant",
        "unauthorized_client",
        "unsupported_grant_type",
        "invalid_scope",
        "invalid_client_metadata",
        "invalid_redirect_uri",
        "access_denied",
        "unsupported_response_type",
        "server_error",
        "temporarily_unavailable",
    }
)
_FIELD_LIMIT = 2048
_NONCE = re.compile(r"[A-Za-z0-9_-]{43}")
_VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")
_SCOPES = frozenset({"wiki:read", "wiki:transform"})
_AUTHORIZE = frozenset(
    {
        "client_id",
        "redirect_uri",
        "response_type",
        "code_challenge",
        "code_challenge_method",
        "state",
        "scope",
        "resource",
    }
)
_CODE = frozenset({"client_id", "grant_type", "code", "redirect_uri", "code_verifier", "resource"})
_REFRESH = frozenset({"client_id", "grant_type", "refresh_token", "scope", "resource"})
_REGISTER = frozenset(
    {
        "redirect_uris",
        "token_endpoint_auth_method",
        "grant_types",
        "response_types",
        "client_name",
        "scope",
    }
)


@dataclass(frozen=True, slots=True)
class NativeOAuthGuardSettings:
    resource: str
    redirect_policy: McpRedirectPolicy
    issuer: str
    authorize_path: str = "/authorize"
    token_path: str = _DEFAULT_EXCHANGE_PATH
    register_path: str = "/register"
    revoke_path: str = "/revoke"
    cookie_name: str = "__Host-wiki_mcp_nonce"
    cookie_secure: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "issuer", canonical_issuer(self.issuer))
        paths = (self.authorize_path, self.token_path, self.register_path, self.revoke_path)
        if len(set(paths)) != len(paths) or any(
            not path.startswith("/") or "?" in path for path in paths
        ):
            raise NativeOAuthRequestError
        if not self.resource or len(self.resource) > _FIELD_LIMIT:
            raise NativeOAuthRequestError
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.cookie_name):
            raise NativeOAuthRequestError
        if self.cookie_name.startswith("__Host-") and not self.cookie_secure:
            raise NativeOAuthRequestError


class _OAuthLogFilter(logging.Filter):
    @override
    def filter(self, record: logging.LogRecord) -> bool:
        if native_oauth_request.get() is not None:
            record.msg = "OAuth handler failure"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


_LOG_FILTER = _OAuthLogFilter()


class _OAuthErrorBody(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)
    error: str = "server_error"


class _RegistrationBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    redirect_uris: list[str] = Field(min_length=1, max_length=_CALLBACK_LIMIT)
    token_endpoint_auth_method: str
    grant_types: list[str] = Field(default_factory=lambda: ["authorization_code", "refresh_token"])
    response_types: list[str] = Field(default_factory=lambda: ["code"])
    client_name: str = ""
    scope: str | None = None
    application_type: str | None = None


def _clean_response(
    start: Message | None, body: bytes, settings: NativeOAuthGuardSettings
) -> Response:
    if start is None:
        raise NativeOAuthRequestError
    status = start.get("status", 500)
    if status >= HTTP_400_BAD_REQUEST:
        error = _OAuthErrorBody.model_validate_json(body).error
        body = json.dumps(
            {
                "error": error if error in _OAUTH_ERRORS else "server_error",
                "error_description": "OAuth request failed",
            }
        ).encode()
    response = Response(body, status_code=status)
    response.raw_headers.extend(
        (key, _clean_location(value, settings) if lowered == b"location" else value)
        for key, value in start.get("headers", [])
        if (lowered := key.lower()) != b"content-length"
    )
    return response


def _clean_location(value: bytes, settings: NativeOAuthGuardSettings) -> bytes:
    location = urlsplit(value.decode("ascii"))
    params = dict(parse_qsl(location.query, keep_blank_values=True))
    if {"code", "error"}.isdisjoint(params):
        return value
    settings.redirect_policy.validate_callback(urlunsplit(location._replace(query="")))
    # Validate the original fields before privacy cleanup; never overwrite a
    # conflicting issuer or collapse duplicate fields into a seemingly valid one.
    location = urlsplit(issuer_redirect(value.decode("ascii"), settings.issuer))
    params = dict(parse_qsl(location.query, keep_blank_values=True))
    if "error" not in params:
        return location.geturl().encode("ascii")
    error = params["error"]
    safe = {
        "error": error if error in _OAUTH_ERRORS else "server_error",
        "error_description": "OAuth request failed",
        "iss": params["iss"],
        **({"state": params["state"]} if "state" in params else {}),
    }
    return urlunsplit(location._replace(query=urlencode(safe))).encode("ascii")


def _fields(raw: bytes, *, public_revocation: bool = False) -> dict[str, str]:
    if len(raw) > _BODY_LIMIT or re.search(rb"%(?![0-9a-fA-F]{2})", raw):
        raise NativeOAuthRequestError
    pairs = parse_qsl(
        raw.decode("utf-8", errors="strict"),
        keep_blank_values=True,
        strict_parsing=True,
        errors="strict",
        max_num_fields=16,
    )
    result: dict[str, str] = {}
    for key, value in pairs:
        if key in result or not key or len(key) > _NAME_LIMIT or len(value.encode()) > _FIELD_LIMIT:
            raise NativeOAuthRequestError
        if not value and not (public_revocation and key == "client_secret"):
            raise NativeOAuthRequestError
        if any(
            ord(character) < _PRINTABLE_START or ord(character) == _DELETE_CHARACTER
            for character in key + value
        ):
            raise NativeOAuthRequestError
        result[key] = value
    return result


def _scope(value: str | None) -> None:
    if value is not None and (not value.split() or not set(value.split()) <= _SCOPES):
        raise NativeOAuthRequestError


def _registration(raw: bytes, policy: McpRedirectPolicy) -> None:
    # Bound nesting before the JSON parser allocates nested objects.
    depth = 0
    quoted = escaped = False
    for character in raw.decode("utf-8", errors="strict"):
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            if depth > _JSON_DEPTH:
                raise NativeOAuthRequestError
        elif character in "]}":
            depth -= 1
    value = _RegistrationBody.model_validate(json.loads(raw, object_pairs_hook=_pairs))
    if value.token_endpoint_auth_method != _PUBLIC_AUTH_METHOD:
        raise NativeOAuthRequestError
    redirects = value.redirect_uris
    for uri in redirects:
        policy.validate_callback(uri)
    if value.grant_types != [
        "authorization_code",
        "refresh_token",
    ] or value.response_types != ["code"]:
        raise NativeOAuthRequestError
    name = value.client_name
    scope = value.scope
    if value.application_type not in {None, "native"}:
        raise NativeOAuthRequestError
    if len(name.encode()) > _CLIENT_NAME_LIMIT:
        raise NativeOAuthRequestError
    _scope(scope)


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise NativeOAuthRequestError
        result[key] = value
    return result


def _validate_headers(headers: list[tuple[bytes, bytes]]) -> None:
    if len(headers) > _HEADER_LIMIT:
        raise NativeOAuthRequestError
    header_bytes = 0
    seen: set[bytes] = set()
    for key, value in headers:
        header_bytes += len(key) + len(value)
        if header_bytes > _BODY_LIMIT:
            raise NativeOAuthRequestError
        lowered = key.lower()
        if lowered == b"authorization":
            raise NativeOAuthRequestError
        if lowered in _UNIQUE_HEADERS:
            if lowered in seen:
                raise NativeOAuthRequestError
            seen.add(lowered)


class NativeOAuthHTTPGuard:
    # Unmounted finite OAuth boundary; consent and traffic quotas are separate.

    def __init__(self, app: ASGIApp, settings: NativeOAuthGuardSettings) -> None:
        self._app = app
        self._settings = settings
        self._endpoints = {
            settings.authorize_path: NativeOAuthEndpoint.AUTHORIZE,
            settings.token_path: NativeOAuthEndpoint.TOKEN,
            settings.register_path: NativeOAuthEndpoint.REGISTER,
            settings.revoke_path: NativeOAuthEndpoint.REVOKE,
        }
        # Authorization-provider exceptions can include private details. Filter
        # that logger only during guarded requests, before handler sinks.
        logging.getLogger("mcp.server.auth.handlers.authorize").addFilter(_LOG_FILTER)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        settings = self._settings
        endpoint = self._endpoints.get(scope.get("path", ""))
        if scope["type"] != "http" or endpoint is None:
            await self._app(scope, receive, send)
            return
        try:
            async with asyncio.timeout(10):
                body = await self._read(scope, receive, endpoint)
            nonce = self._validate(scope, body, endpoint)
        except ValueError, UnicodeError, RecursionError, TimeoutError:
            error = (
                "invalid_client_metadata"
                if endpoint is NativeOAuthEndpoint.REGISTER
                else "invalid_request"
            )
            await JSONResponse(
                {"error": error, "error_description": "Invalid OAuth request"},
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)
            return
        if endpoint is NativeOAuthEndpoint.REVOKE:
            # Normalize validated public revocations with an empty client_secret field.
            body = urlencode(
                {**_fields(body, public_revocation=True), "client_secret": ""}
            ).encode()
            scope = {
                **scope,
                "headers": [
                    (key, value)
                    for key, value in scope.get("headers", [])
                    if key.lower() != b"content-length"
                ]
                + [(b"content-length", str(len(body)).encode())],
            }
        context = NativeOAuthRequestContext(
            settings.resource,
            hashlib.sha256(nonce.encode()).hexdigest() if nonce else None,
            endpoint,
        )
        token = native_oauth_request.set(context)
        consumed = False
        response_start: Message | None = None
        response_body = bytearray()

        async def replay() -> Message:
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        async def guarded_send(message: Message) -> None:  # ruff: ignore[unused-async] - The ASGI send interface requires an async callback.
            nonlocal response_start
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [(b"cache-control", b"no-store"), (b"referrer-policy", b"no-referrer")]
                )
                if nonce:
                    cookie = Response()
                    cookie.set_cookie(
                        settings.cookie_name,
                        nonce,
                        max_age=600,
                        secure=settings.cookie_secure,
                        httponly=True,
                        samesite="lax",
                        path="/",
                    )
                    headers.extend(
                        (key, value) for key, value in cookie.raw_headers if key == b"set-cookie"
                    )
                response_start = {**message, "headers": headers}
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                if len(response_body) + len(chunk) > _RESPONSE_LIMIT:
                    raise NativeOAuthRequestError
                response_body.extend(chunk)

        try:
            try:
                async with asyncio.timeout(30):
                    await self._app(scope, replay, guarded_send)
                response = _clean_response(response_start, bytes(response_body), settings)
            except RegistrationAdmissionDeniedError as error:
                response = JSONResponse(
                    {
                        "error": "temporarily_unavailable",
                        "error_description": "Registration temporarily unavailable",
                    },
                    status_code=429,
                    headers={
                        "Cache-Control": "no-store",
                        "Referrer-Policy": "no-referrer",
                        "Retry-After": str(max(1, min(1800, error.retry_after_seconds))),
                    },
                )
            except RegistrationAdmissionUnavailableError:
                response = JSONResponse(
                    {
                        "error": "temporarily_unavailable",
                        "error_description": "Registration temporarily unavailable",
                    },
                    status_code=503,
                    headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
                )
            except Exception:  # ruff: ignore[blind-except] - OAuth privacy boundary before any response is emitted.
                response = JSONResponse(
                    {"error": "server_error", "error_description": "OAuth request failed"},
                    status_code=500,
                    headers={"Cache-Control": "no-store"},
                )
            await response(scope, receive, send)
        finally:
            native_oauth_request.reset(token)

    @staticmethod
    async def _read(scope: Scope, receive: Receive, endpoint: NativeOAuthEndpoint) -> bytes:
        headers = scope.get("headers", [])
        _validate_headers(headers)
        method = scope.get("method")
        if method != ("GET" if endpoint is NativeOAuthEndpoint.AUTHORIZE else "POST"):
            raise NativeOAuthRequestError
        if len(scope.get("query_string", b"")) > _BODY_LIMIT:
            raise NativeOAuthRequestError
        if endpoint != "authorize" and scope.get("query_string"):
            raise NativeOAuthRequestError
        chunks = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                raise NativeOAuthRequestError
            chunk = message.get("body", b"")
            if len(chunks) + len(chunk) > _BODY_LIMIT:
                raise NativeOAuthRequestError
            chunks.extend(chunk)
            if not message.get("more_body", False):
                break
        if endpoint is NativeOAuthEndpoint.AUTHORIZE and chunks:
            raise NativeOAuthRequestError
        if endpoint != "authorize":
            content_type = dict(headers).get(b"content-type", b"").split(b";", 1)[0].strip()
            expected = (
                b"application/json"
                if endpoint is NativeOAuthEndpoint.REGISTER
                else b"application/x-www-form-urlencoded"
            )
            if content_type != expected:
                raise NativeOAuthRequestError
        return bytes(chunks)

    def _validate(self, scope: Scope, body: bytes, endpoint: NativeOAuthEndpoint) -> str | None:
        settings = self._settings
        if endpoint is NativeOAuthEndpoint.REGISTER:
            _registration(body, settings.redirect_policy)
            return None
        values = _fields(
            scope.get("query_string", b"") if endpoint is NativeOAuthEndpoint.AUTHORIZE else body,
            public_revocation=endpoint is NativeOAuthEndpoint.REVOKE,
        )
        validate_registered_client_id(values.get("client_id", ""))
        _scope(values.get("scope"))
        if (
            endpoint in {NativeOAuthEndpoint.AUTHORIZE, NativeOAuthEndpoint.TOKEN}
            and values.get("resource") != settings.resource
        ):
            raise NativeOAuthRequestError
        if endpoint is NativeOAuthEndpoint.AUTHORIZE:
            return self._validate_authorize(scope, values)
        if endpoint is NativeOAuthEndpoint.TOKEN:
            self._validate_token(values)
        elif not {"client_id", "token"} <= set(values) <= {
            "client_id",
            "token",
            "token_type_hint",
            "client_secret",
        } or values.get("client_secret", ""):
            raise NativeOAuthRequestError
        return None

    def _validate_authorize(self, scope: Scope, values: dict[str, str]) -> str:
        settings = self._settings
        if (
            not set(values) <= _AUTHORIZE
            or values.get("response_type") != "code"
            or values.get("code_challenge_method") != "S256"
            or not _NONCE.fullmatch(values.get("code_challenge", ""))
        ):
            raise NativeOAuthRequestError
        settings.redirect_policy.validate_callback(values.get("redirect_uri", ""))
        cookie = dict(scope.get("headers", [])).get(b"cookie", b"").decode("ascii")
        matches = [
            stripped.split("=", 1)[1]
            for part in cookie.split(";")
            if (stripped := part.strip()).startswith(settings.cookie_name + "=")
        ]
        if len(matches) > 1 or (matches and not _NONCE.fullmatch(matches[0])):
            raise NativeOAuthRequestError
        return matches[0] if matches else secrets.token_urlsafe(32)

    def _validate_token(self, values: dict[str, str]) -> None:
        if values.get("grant_type") == "authorization_code":
            if set(values) != _CODE or not _VERIFIER.fullmatch(values.get("code_verifier", "")):
                raise NativeOAuthRequestError
            self._settings.redirect_policy.validate_callback(values["redirect_uri"])
        elif values.get("grant_type") == "refresh_token":
            if (
                not {"client_id", "grant_type", "refresh_token", "resource"}
                <= set(values)
                <= _REFRESH
            ):
                raise NativeOAuthRequestError
        else:
            raise NativeOAuthRequestError
