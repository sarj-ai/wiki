from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import SplitResult, urlsplit


_MAX_URI_LENGTH = 2048
_MAX_CALLBACKS = 16
_FIRST_PRINTABLE = 32
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_PATH = re.compile(r"/(?:[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*)?")
_CLIENT_ID = re.compile(r"[A-Za-z0-9_-]{1,255}")
_DNS_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}")
_INVALID_CLIENT = "An opaque registered MCP client ID is required"
_INVALID_CALLBACK = "Invalid MCP callback URI"
_NON_CANONICAL = "MCP callback URI must use canonical spelling"
_HTTPS_HOST = "HTTPS callbacks require an exact non-local DNS hostname"
_TOO_MANY = "Too many configured MCP callbacks"
_INVALID_PATH = "MCP loopback callbacks require exact canonical paths"
_NOT_ALLOWED = "MCP callback URI is not allowed"
_INVALID_REGISTRATION = "MCP client registration requires bounded explicit callbacks"
_NOT_REGISTERED = "MCP callback URI is not registered for this client"


def validate_registered_client_id(client_id: str) -> None:
    if not _CLIENT_ID.fullmatch(client_id):
        raise ValueError(_INVALID_CLIENT)


def _validate_registration_bounds(client_id: str, callbacks: tuple[str, ...]) -> None:
    validate_registered_client_id(client_id)
    if not callbacks or len(callbacks) > _MAX_CALLBACKS:
        raise ValueError(_INVALID_REGISTRATION)


def _parse_callback(uri: str) -> SplitResult:
    if (
        not uri
        or len(uri) > _MAX_URI_LENGTH
        or not uri.isascii()
        or any(character.isspace() or ord(character) < _FIRST_PRINTABLE for character in uri)
        or any(character in uri for character in "\\%?#*@")
    ):
        raise ValueError(_INVALID_CALLBACK)
    try:
        parsed = urlsplit(uri)
        port = parsed.port
    except ValueError as error:
        raise ValueError(_INVALID_CALLBACK) from error
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or not _PATH.fullmatch(parsed.path)
        or port == 0
    ):
        raise ValueError(_INVALID_CALLBACK)
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    authority = f"{host}:{port}" if port is not None else host
    if uri != f"{parsed.scheme}://{authority}{parsed.path}":
        raise ValueError(_NON_CANONICAL)
    return parsed


def _validate_https_allowlist_entry(uri: str) -> None:
    parsed = _parse_callback(uri)
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or not _DNS_HOST.fullmatch(host)
        or host.endswith((".localhost", ".local", ".internal"))
    ):
        raise ValueError(_HTTPS_HOST)


@dataclass(frozen=True, slots=True)
class McpRedirectPolicy:
    # Configure paths from a verified client contract, not a guessed Codex default.
    # HTTPS entries are administrator-owned; never fetch them. Keep CIMD disabled.
    loopback_paths: tuple[str, ...] = ()
    https_callbacks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "loopback_paths", tuple(self.loopback_paths))
        object.__setattr__(self, "https_callbacks", tuple(self.https_callbacks))
        if len(self.loopback_paths) + len(self.https_callbacks) > _MAX_CALLBACKS:
            raise ValueError(_TOO_MANY)
        if any(not _PATH.fullmatch(path) for path in self.loopback_paths):
            raise ValueError(_INVALID_PATH)
        for uri in self.https_callbacks:
            _validate_https_allowlist_entry(uri)

    def validate_callback(self, uri: str) -> None:
        self._validated_callback(uri)

    def _validated_callback(self, uri: str) -> SplitResult:
        parsed = _parse_callback(uri)
        if parsed.scheme == "https" and uri in self.https_callbacks:
            return parsed
        if (
            parsed.scheme == "http"
            and parsed.hostname in _LOOPBACK_HOSTS
            and parsed.port is not None
            and parsed.path in self.loopback_paths
        ):
            return parsed
        raise ValueError(_NOT_ALLOWED)

    def validate_registration(self, client_id: str, callbacks: tuple[str, ...]) -> None:
        _validate_registration_bounds(client_id, callbacks)
        for uri in callbacks:
            self.validate_callback(uri)

    def validate_authorization(
        self, *, client_id: str, uri: str, registered_callbacks: tuple[str, ...]
    ) -> None:
        if type(self) is not McpRedirectPolicy:
            self._validate_extended_authorization(
                client_id=client_id, uri=uri, registered_callbacks=registered_callbacks
            )
            return
        _validate_registration_bounds(client_id, registered_callbacks)
        registered = [self._validated_callback(value) for value in registered_callbacks]
        requested = self._validated_callback(uri)
        for registered_uri, callback in zip(registered_callbacks, registered, strict=True):
            if uri == registered_uri or (
                requested.scheme == callback.scheme == "http"
                and requested.hostname == callback.hostname
                and requested.path == callback.path
            ):
                return
        raise ValueError(_NOT_REGISTERED)

    def _validate_extended_authorization(
        self, *, client_id: str, uri: str, registered_callbacks: tuple[str, ...]
    ) -> None:
        self.validate_registration(client_id, registered_callbacks)
        self.validate_callback(uri)
        requested = _parse_callback(uri)
        for registered_uri in registered_callbacks:
            registered = _parse_callback(registered_uri)
            if uri == registered_uri or (
                requested.scheme == registered.scheme == "http"
                and requested.hostname == registered.hostname
                and requested.path == registered.path
            ):
                return
        raise ValueError(_NOT_REGISTERED)
