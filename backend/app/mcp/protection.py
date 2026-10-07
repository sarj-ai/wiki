# MCP-only authenticated encryption; never return corrupt state as a cache miss.

from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Mapping
import hashlib
import hmac
import json

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import ConfigDict, JsonValue, RootModel


class _OAuthValue(RootModel[dict[str, JsonValue]]):
    """Validate authenticated state without exposing input in validation errors."""

    model_config = ConfigDict(hide_input_in_errors=True)


MAX_VALUE_BYTES = 262_144
_MAX_KEYS = 3


class InvalidOAuthStateError(ValueError):
    def __init__(self) -> None:
        super().__init__("Invalid OAuth state")


def _derive(key: str, purpose: bytes) -> bytes:
    Fernet(key.encode())
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"sarj.wiki.mcp.oauth.v1." + purpose,
    ).derive(urlsafe_b64decode(key.encode()))


class OAuthStateProtector:  # sarj-noqa: SARJ071 -- Consumers require this encryption implementation.
    def __init__(self, keys: tuple[str, ...], *, lock_key: str) -> None:
        if not keys or len(keys) > _MAX_KEYS or len(set(keys)) != len(keys):
            msg = "OAuth encryption requires one to three unique keys"
            raise ValueError(msg)
        self._fernets = tuple(
            Fernet(urlsafe_b64encode(_derive(key, b"encryption"))) for key in keys
        )
        self._lookup = tuple(_derive(key, b"lookup") for key in keys)
        self._lock = _derive(lock_key, b"operation-lock")

    def physical_keys(self, collection: str, key: str) -> tuple[str, ...]:
        message = json.dumps([collection, key], ensure_ascii=True).encode()
        return tuple(
            hmac.new(secret, message, hashlib.sha256).hexdigest() for secret in self._lookup
        )

    def lock_id(self, identity: str) -> int:
        digest = hmac.new(self._lock, identity.encode(), hashlib.sha256).digest()
        return int.from_bytes(digest[:8], "big", signed=True)

    def protect(self, collection: str, key: str, value: Mapping[str, object]) -> str:
        try:
            return self._seal(collection, key, value)
        except ValueError, TypeError, UnicodeError, RecursionError:
            raise InvalidOAuthStateError from None

    def _seal(self, collection: str, key: str, value: Mapping[str, object]) -> str:
        payload = _OAuthValue.model_validate(dict(value), strict=True).root
        plaintext = json.dumps(
            {"version": 1, "collection": collection, "key": key, "value": payload},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
        if len(plaintext) > MAX_VALUE_BYTES:
            msg = "OAuth value exceeds storage limit"
            raise ValueError(msg)
        return self._fernets[0].encrypt(plaintext).decode()

    def unprotect(self, collection: str, key: str, ciphertext: str) -> dict[str, JsonValue]:
        try:
            result = self._open(collection, key, ciphertext)
            json.dumps(result, allow_nan=False)
        except InvalidToken, ValueError, TypeError, UnicodeError, RecursionError:
            raise InvalidOAuthStateError from None
        else:
            return result

    def _open(self, collection: str, key: str, ciphertext: str) -> dict[str, JsonValue]:
        if len(ciphertext) > 2 * MAX_VALUE_BYTES:
            raise InvalidOAuthStateError
        plaintext = MultiFernet(list(self._fernets)).decrypt(ciphertext.encode())
        if len(plaintext) > MAX_VALUE_BYTES:
            raise InvalidOAuthStateError
        envelope = json.loads(plaintext)
        if not isinstance(envelope, dict) or set(envelope) != {
            "version",
            "collection",
            "key",
            "value",
        }:
            raise InvalidOAuthStateError
        if (
            type(envelope["version"]) is not int
            or envelope["version"] != 1
            or envelope["collection"] != collection
            or envelope["key"] != key
        ):
            raise InvalidOAuthStateError
        return _OAuthValue.model_validate(envelope["value"], strict=True).root
