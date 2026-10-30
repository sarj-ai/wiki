from __future__ import annotations

from typing import override

import pytest

from app.mcp.redirect_policy import McpRedirectPolicy, validate_registered_client_id


@pytest.fixture
def policy() -> McpRedirectPolicy:
    return McpRedirectPolicy(
        loopback_paths=("/verified-client-callback",),
        https_callbacks=("https://client.example.com/oauth/callback",),
    )


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
@pytest.mark.parametrize("port", [1, 80, 3000, 65535])
def test_explicit_loopback_path_accepts_dynamic_port(
    policy: McpRedirectPolicy, host: str, port: int
):
    policy.validate_authorization(
        client_id="registered-client",
        uri=f"http://{host}:{port}/verified-client-callback",
        registered_callbacks=(f"http://{host}:9999/verified-client-callback",),
    )


def test_remote_https_requires_exact_registration(policy: McpRedirectPolicy):
    uri = "https://client.example.com/oauth/callback"
    policy.validate_authorization(client_id="client", uri=uri, registered_callbacks=(uri,))
    with pytest.raises(ValueError, match="not registered"):
        policy.validate_authorization(
            client_id="client",
            uri=uri,
            registered_callbacks=("http://localhost:3000/verified-client-callback",),
        )


@pytest.mark.parametrize(
    "uri",
    [
        "http://localhost:3000/other",
        "http://localhost/verified-client-callback",
        "http://127.0.0.2:3000/verified-client-callback",
        "http://2130706433:3000/verified-client-callback",
        "http://localhost.evil.com:3000/verified-client-callback",
        "http://localhost.:3000/verified-client-callback",
        "http://localhost@evil.com:3000/verified-client-callback",
        "http://user:password@localhost:3000/verified-client-callback",
        "http://LOCALHOST:3000/verified-client-callback",
        "http://localhost:03000/verified-client-callback",
        "http://localhost:0/verified-client-callback",
        "http://localhost:65536/verified-client-callback",
        "http://[::ffff:127.0.0.1]:3000/verified-client-callback",
        "http://[::1%25zone]:3000/verified-client-callback",
        "http://[::1/verified-client-callback",
        "http://localhost:3000/%76erified-client-callback",
        "http://localhost:3000/a/../verified-client-callback",
        "http://localhost:3000/verified-client-callback?next=evil",
        "http://localhost:3000/verified-client-callback#fragment",
        "http://localhost:3000/verified-client-callback?",
        "http://localhost:3000/verified-client-callback#",
        "http://localhost:3000/verified-client-callback/",
        "http://localhost:3000//verified-client-callback",
        "http://localhost:3000\\@evil.com/verified-client-callback",
        "\nhttp://localhost:3000/verified-client-callback",
        "http://local\thost:3000/verified-client-callback",
        "http://localhost:3000/verified-client-callback\x7f",
        "https://client.example.com:443/oauth/callback",
        "https://evil.example.com/oauth/callback",
        "https://client.example.com/oauth/callback/extra",
        "https://clіent.example.com/oauth/callback",
        "file:///verified-client-callback",
        "javascript:alert(1)",
        "x" * 2049,
        "",
    ],
)
def test_rejects_callback_bypasses(policy: McpRedirectPolicy, uri: str):
    with pytest.raises(ValueError, match="MCP callback URI"):
        policy.validate_callback(uri)


def test_default_policy_denies_all_callbacks():
    with pytest.raises(ValueError, match="not allowed"):
        McpRedirectPolicy().validate_callback("http://localhost:3000/callback")


@pytest.mark.parametrize("path", ["callback", "/*", "/foo/../callback", "/%63allback", "/a?b"])
def test_configuration_rejects_non_exact_paths(path: str):
    with pytest.raises(ValueError, match="exact canonical paths"):
        McpRedirectPolicy(loopback_paths=(path,))


@pytest.mark.parametrize(
    "uri",
    [
        "https://*.example.com/callback",
        "https://client.example.com/*",
        "http://client.example.com/callback",
        "https://127.0.0.1/callback",
        "https://[::1]/callback",
        "https://host.local/callback",
        "https://host.internal/callback",
        "https://host.localhost/callback",
    ],
)
def test_configuration_rejects_wildcards_and_local_https(uri: str):
    with pytest.raises(ValueError, match="callback"):
        McpRedirectPolicy(https_callbacks=(uri,))


@pytest.mark.parametrize(
    "client_id", ["", "https://client.example.com/client.json", "../id", "a" * 256]
)
def test_cimd_and_unbounded_client_ids_are_rejected(client_id: str):
    with pytest.raises(ValueError, match="opaque"):
        validate_registered_client_id(client_id)


def test_loopback_host_cannot_change_between_registration_and_authorization(
    policy: McpRedirectPolicy,
):
    with pytest.raises(ValueError, match="not registered"):
        policy.validate_authorization(
            client_id="client",
            uri="http://127.0.0.1:3000/verified-client-callback",
            registered_callbacks=("http://localhost:3000/verified-client-callback",),
        )


def test_registration_has_no_implicit_localhost_fallback(policy: McpRedirectPolicy):
    with pytest.raises(ValueError, match="explicit callbacks"):
        policy.validate_registration("client", ())


def test_registration_and_configuration_are_bounded(policy: McpRedirectPolicy):
    with pytest.raises(ValueError, match="Too many"):
        McpRedirectPolicy(loopback_paths=("/callback",) * 17)
    with pytest.raises(ValueError, match="bounded"):
        policy.validate_registration("client", ("http://localhost:3000/callback",) * 17)


@pytest.mark.parametrize(
    "requested",
    ["http://localhost:3000/verified-client-callback", "not a callback"],
)
def test_authorization_validates_every_registered_callback_before_requested_uri(
    policy: McpRedirectPolicy, requested: str
):
    with pytest.raises(ValueError, match="not allowed"):
        policy.validate_authorization(
            client_id="client",
            uri=requested,
            registered_callbacks=(
                "http://localhost:3000/verified-client-callback",
                "https://evil.example.com/oauth/callback",
            ),
        )


def test_authorization_matches_last_of_sixteen_valid_callbacks():
    paths = tuple(f"/callback{index}" for index in range(16))
    policy = McpRedirectPolicy(loopback_paths=paths)
    policy.validate_authorization(
        client_id="client",
        uri="http://127.0.0.1:9876/callback15",
        registered_callbacks=tuple(f"http://127.0.0.1:3000{path}" for path in paths),
    )


class _RecordingPolicy(McpRedirectPolicy):
    def __init__(self) -> None:
        super().__init__(loopback_paths=("/callback",))
        self.calls: list[str] = []

    @override
    def validate_registration(self, client_id: str, callbacks: tuple[str, ...]) -> None:
        self.calls.append("registration")
        super().validate_registration(client_id, callbacks)

    @override
    def validate_callback(self, uri: str) -> None:
        self.calls.append(uri)
        super().validate_callback(uri)


def test_subclass_authorization_preserves_public_validator_call_order():
    policy = _RecordingPolicy()
    registered = "http://127.0.0.1:3000/callback"
    requested = "http://127.0.0.1:9876/callback"
    policy.validate_authorization(
        client_id="client", uri=requested, registered_callbacks=(registered,)
    )
    assert policy.calls == ["registration", registered, requested]


def test_subclass_authorization_keeps_registration_override_rejection():
    class RejectingPolicy(McpRedirectPolicy):
        @override
        def validate_registration(self, client_id: str, callbacks: tuple[str, ...]) -> None:
            message = "Extra registration policy rejected"
            raise ValueError(message)

    policy = RejectingPolicy(loopback_paths=("/callback",))
    with pytest.raises(ValueError, match="Extra registration policy rejected"):
        policy.validate_authorization(
            client_id="client",
            uri="http://127.0.0.1:9876/callback",
            registered_callbacks=("http://127.0.0.1:3000/callback",),
        )
