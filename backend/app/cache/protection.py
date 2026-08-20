from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass, field
import hashlib
import hmac
import json
from typing import Self

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import JsonValue, TypeAdapter, ValidationError

from app.config import parse_fernet_keys


_JSON_VALUE: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
_LOOKUP_KEY_INFO = b"sarj.cache-entry.h1.lookup-key"
_ENVELOPE_PREFIX = "f1."
_MAX_ROTATION_KEYS = 3


class InvalidCacheEntryError(ValueError):
    pass


@dataclass(frozen=True, slots=True, init=False)
class CacheEntryProtector:
    _fernets: tuple[Fernet, ...] = field(init=False, repr=False)
    _lookup_keys: tuple[bytes, ...] = field(init=False, repr=False)

    def __init__(self, keys: tuple[str, ...]) -> None:
        if not keys:
            msg = "at least one cache protection key is required"
            raise ValueError(msg)
        if len(keys) > _MAX_ROTATION_KEYS:
            msg = "cache protection supports at most three rotation keys"
            raise ValueError(msg)
        if len(set(keys)) != len(keys):
            msg = "cache protection keys must be unique"
            raise ValueError(msg)
        try:
            fernets = tuple(Fernet(key.encode()) for key in keys)
            lookup_keys = tuple(self._derive_lookup_key(key) for key in keys)
        except (ValueError, TypeError) as error:
            msg = "cache protection keys must be valid Fernet keys"
            raise ValueError(msg) from error
        object.__setattr__(self, "_fernets", fernets)
        object.__setattr__(self, "_lookup_keys", lookup_keys)

    @classmethod
    def from_key_string(cls, raw: str) -> Self:
        return cls(tuple(parse_fernet_keys(raw)))

    def physical_keys(self, namespace: str, logical_key: str) -> tuple[str, ...]:
        namespace_bytes = namespace.encode()
        logical_key_bytes = logical_key.encode()
        message = (
            len(namespace_bytes).to_bytes(8, "big")
            + namespace_bytes
            + len(logical_key_bytes).to_bytes(8, "big")
            + logical_key_bytes
        )
        return tuple(
            "h1."
            + urlsafe_b64encode(hmac.new(key, message, hashlib.sha256).digest())
            .rstrip(b"=")
            .decode()
            for key in self._lookup_keys
        )

    def protect(self, namespace: str, logical_key: str, value: JsonValue) -> str:
        plaintext = json.dumps(
            {
                "key": logical_key,
                "namespace": namespace,
                "value": _JSON_VALUE.validate_python(value),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return _ENVELOPE_PREFIX + self._fernets[0].encrypt(plaintext).decode()

    def unprotect(self, namespace: str, logical_key: str, envelope: object) -> JsonValue:
        if not isinstance(envelope, str) or not envelope.startswith(_ENVELOPE_PREFIX):
            raise InvalidCacheEntryError
        token = envelope.removeprefix(_ENVELOPE_PREFIX)
        try:
            plaintext = MultiFernet(list(self._fernets)).decrypt(token.encode())
            payload = json.loads(plaintext)
            if (
                not isinstance(payload, dict)
                or set(payload) != {"key", "namespace", "value"}
                or payload["namespace"] != namespace
                or payload["key"] != logical_key
            ):
                raise InvalidCacheEntryError
            return _JSON_VALUE.validate_python(payload["value"])
        except (InvalidToken, json.JSONDecodeError, ValidationError, UnicodeDecodeError) as error:
            raise InvalidCacheEntryError from error

    @staticmethod
    def _derive_lookup_key(key: str) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=_LOOKUP_KEY_INFO,
        ).derive(urlsafe_b64decode(key.encode()))
