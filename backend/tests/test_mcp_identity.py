from __future__ import annotations

import pytest

from app.mcp.identity import (
    McpIdentityError,
    validate_google_identity,
    validate_verified_mcp_claims,
)


def claims() -> dict[str, object]:
    return {
        "iss": "https://wiki.example",
        "aud": "https://wiki.example/mcp",
        "client_id": "codex-client",
        "jti": "token-id",
        "iat": 100,
        "exp": 300,
    }


def validate(claim: dict[str, object]):
    return validate_verified_mcp_claims(
        claim, issuer="https://wiki.example", resource="https://wiki.example/mcp", now=200
    )


def test_mcp_identity_retains_client_not_google_subject():
    identity = validate({**claims(), "sub": "google-person"})
    assert identity.client_id == "codex-client"
    assert identity.token_id == "token-id"


@pytest.mark.parametrize("key", ["iss", "aud", "client_id", "jti", "iat", "exp"])
def test_missing_required_mcp_claim_fails(key: str):
    payload = claims()
    del payload[key]
    with pytest.raises(McpIdentityError):
        validate(payload)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("aud", "sarj-wiki-api"),
        ("aud", ["https://wiki.example/mcp"]),
        ("iss", "https://evil.example"),
        ("token_use", "refresh"),
        ("iat", True),
        ("iat", 201),
        ("iat", -1),
        ("exp", "300"),
        ("exp", 200),
        ("exp", float("inf")),
        ("jti", ""),
        ("jti", "secret\nvalue"),
        ("client_id", " client"),
    ],
)
def test_bad_mcp_claim_fails_without_echoing_input(key: str, value: object):
    with pytest.raises(McpIdentityError, match=r"^MCP identity verification failed$"):
        validate({**claims(), key: value})


def google_token() -> dict[str, object]:
    return {"aud": "google-client", "sub": "person", "expires_in": "3600"}


def google_user() -> dict[str, object]:
    return {"id": "person", "email": "person@sarj.ai", "verified_email": True}


def google_validate(token: dict[str, object], user: dict[str, object]):
    return validate_google_identity(
        token, user, client_id="google-client", allowed_domain="sarj.ai", now=200
    )


@pytest.mark.parametrize("verified", [True, "true"])
def test_google_identity_requires_matching_subject_and_preserves_expiry(verified: object):
    identity = google_validate({**google_token(), "email_verified": verified}, google_user())
    assert identity.subject == "person"
    assert identity.email == "person@sarj.ai"
    assert identity.expires_at == 3800


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("aud", "other-client"),
        ("sub", "other-person"),
        ("expires_in", None),
        ("expires_in", True),
        ("expires_in", 0),
        ("expires_in", 86401),
        ("expires_in", "nan"),
        ("expires_in", "999999999999999999999"),
        ("expires_in", 1.5),
        ("email_verified", "false"),
        ("email_verified", "TRUE"),
        ("email_verified", 1),
        ("email", "other@sarj.ai"),
    ],
)
def test_invalid_google_token_claim_fails(key: str, value: object):
    with pytest.raises(McpIdentityError):
        google_validate({**google_token(), key: value}, google_user())


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("id", "other-person"),
        ("verified_email", "true"),
        ("verified_email", 1),
        ("verified_email", None),
        ("email", "person@sarj.ai.evil.example"),
        ("email", "person@@sarj.ai"),
        ("email", "person@sarj.ai\n"),
    ],
)
def test_invalid_google_user_claim_fails(key: str, value: object):
    with pytest.raises(McpIdentityError):
        google_validate(google_token(), {**google_user(), key: value})
