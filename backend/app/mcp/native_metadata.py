from typing import override
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastmcp.server.auth import OAuthProvider
from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.routes import build_metadata, cors_middleware
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthMetadata
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from app.mcp.native_request_context import NativeOAuthRequestError


_METADATA_PATH = "/.well-known/oauth-authorization-server"
_MAX_REDIRECT_LENGTH = 8192


class NativeOAuthMetadata(OAuthMetadata):
    authorization_response_iss_parameter_supported: bool | None = False
    client_id_metadata_document_supported: bool | None = False


class _ComposedMetadataHandler(MetadataHandler):
    def __init__(self, metadata: OAuthMetadata) -> None:
        super().__init__(metadata)
        self._body = metadata.model_dump_json(exclude_none=True).encode("utf-8")

    @override
    async def handle(self, request: Request) -> Response:
        return Response(
            self._body,
            media_type="application/json",
            headers={"Cache-Control": "public, max-age=3600"},
        )


def canonical_issuer(value: str) -> str:
    issuer = AnyHttpUrl(value)
    if (
        issuer.username is not None
        or issuer.password is not None
        or issuer.query is not None
        or issuer.fragment is not None
    ):
        raise NativeOAuthRequestError
    if issuer.scheme != "https" or not issuer.host:
        raise NativeOAuthRequestError
    return str(issuer)


def issuer_redirect(uri: str, issuer: str) -> str:
    # The caller must already have validated the registered callback. This helper
    # only binds a final authorization result, never an intermediate consent URL.
    if len(uri) > _MAX_REDIRECT_LENGTH:
        raise NativeOAuthRequestError
    expected = canonical_issuer(issuer)
    parsed = urlsplit(uri)
    pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=16)
    fields = dict(pairs)
    if (
        len(fields) != len(pairs)
        or parsed.fragment
        or ("code" in fields) == ("error" in fields)
        or not fields.get("code", fields.get("error"))
    ):
        raise NativeOAuthRequestError
    if "iss" in fields and fields["iss"] != expected:
        raise NativeOAuthRequestError
    fields["iss"] = expected
    result = urlunsplit(parsed._replace(query=urlencode(fields)))
    if len(result) > _MAX_REDIRECT_LENGTH:
        raise NativeOAuthRequestError
    return result


def native_metadata_routes(
    provider: OAuthProvider,
    routes: list[Route],
    *,
    issuer_responses_enabled: bool = False,
) -> list[Route]:
    # Advertise issuer support only after both consent results and SDK-generated
    # authorization errors are covered by the composed response boundary.
    issuer = canonical_issuer(str(provider.issuer_url))
    if canonical_issuer(str(provider.base_url)) != issuer:
        raise NativeOAuthRequestError
    if sum(route.path == _METADATA_PATH for route in routes) != 1:
        raise NativeOAuthRequestError
    base = build_metadata(
        AnyHttpUrl(issuer),
        provider.service_documentation_url,
        provider.client_registration_options or ClientRegistrationOptions(),
        provider.revocation_options or RevocationOptions(),
    )
    metadata = NativeOAuthMetadata.model_validate(
        {
            **base.model_dump(),
            "authorization_response_iss_parameter_supported": issuer_responses_enabled,
            "client_id_metadata_document_supported": False,
            "token_endpoint_auth_methods_supported": ["none"],
            "revocation_endpoint_auth_methods_supported": (
                ["none"] if base.revocation_endpoint is not None else None
            ),
        }
    )
    return [
        Route(
            _METADATA_PATH,
            endpoint=cors_middleware(_ComposedMetadataHandler(metadata).handle, ["GET", "OPTIONS"]),
            methods=["GET", "OPTIONS"],
        )
        if route.path == _METADATA_PATH
        else route
        for route in routes
    ]
