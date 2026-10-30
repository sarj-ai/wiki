from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import NewType

from app.identifiers import GoogleUserId


_ASCII_CONTROL_BOUNDARY = 32
_ASCII_DELETE = 127
_MAX_LIFETIME = timedelta(days=1)
_MAX_LIFETIME_DIGITS = 5
McpClientId = NewType("McpClientId", str)
McpTokenId = NewType("McpTokenId", str)


class McpIdentityError(ValueError):
    def __init__(self) -> None:
        super().__init__("MCP identity verification failed")


@dataclass(frozen=True, slots=True)
class VerifiedMcpClaims:
    client_id: McpClientId
    token_id: McpTokenId
    issued_at: int
    expires_at: int


@dataclass(frozen=True, slots=True)
class VerifiedGoogleIdentity:
    subject: GoogleUserId
    email: str
    expires_at: int


def _bounded_text(value: object, maximum: int = 1024) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or value != value.strip()
        or any(
            ord(character) < _ASCII_CONTROL_BOUNDARY or ord(character) == _ASCII_DELETE
            for character in value
        )
    ):
        raise McpIdentityError
    return value


def validate_verified_mcp_claims(
    claims: Mapping[str, object], *, issuer: str, resource: str, now: int
) -> VerifiedMcpClaims:
    # This is not a JWT verifier: the configured issuer must verify the signature first.
    if claims.get("iss") != issuer or claims.get("aud") != resource:
        raise McpIdentityError
    if claims.get("token_use", "access") != "access":
        raise McpIdentityError
    issued_at, expires_at = claims.get("iat"), claims.get("exp")
    if type(issued_at) is not int or type(expires_at) is not int:
        raise McpIdentityError
    if issued_at < 0 or issued_at > now or expires_at <= now or expires_at <= issued_at:
        raise McpIdentityError
    return VerifiedMcpClaims(
        client_id=McpClientId(_bounded_text(claims.get("client_id"))),
        token_id=McpTokenId(_bounded_text(claims.get("jti"), 256)),
        issued_at=issued_at,
        expires_at=expires_at,
    )


def validate_google_identity(
    token_info: Mapping[str, object],
    user_info: Mapping[str, object],
    *,
    client_id: str,
    allowed_domain: str,
    now: int,
) -> VerifiedGoogleIdentity:
    # Only successful tokeninfo and v2 userinfo responses belong at this boundary.
    if token_info.get("aud") != client_id:
        raise McpIdentityError
    subject = _bounded_text(token_info.get("sub"), 256)
    if user_info.get("id") != subject or user_info.get("verified_email") is not True:
        raise McpIdentityError
    email = _bounded_text(user_info.get("email"), 320)
    if email.count("@") != 1:
        raise McpIdentityError
    local, domain = email.rsplit("@", 1)
    if not local or domain.lower() != allowed_domain.lower():
        raise McpIdentityError
    if "email" in token_info and token_info["email"] != email:
        raise McpIdentityError
    if "email_verified" in token_info:
        verified = token_info["email_verified"]
        # Google's tokeninfo contract uses the exact string "true"; v2 userinfo
        # above must independently supply the actual boolean True.
        if verified is not True and verified != "true":
            raise McpIdentityError
    lifetime = token_info.get("expires_in")
    if isinstance(lifetime, str) and lifetime.isascii() and lifetime.isdecimal():
        if len(lifetime) > _MAX_LIFETIME_DIGITS:
            raise McpIdentityError
        lifetime = int(lifetime)
    if type(lifetime) is not int or not 0 < lifetime <= _MAX_LIFETIME.total_seconds():
        raise McpIdentityError
    return VerifiedGoogleIdentity(
        subject=GoogleUserId(subject), email=email, expires_at=now + lifetime
    )
