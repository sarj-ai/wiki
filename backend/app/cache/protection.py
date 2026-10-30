from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache, partial
import hashlib
import hmac
import json
from typing import Self

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError
from pydantic_core import PydanticSerializationError, to_json

from app.config import parse_fernet_keys


class _CacheEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, ser_json_inf_nan="constants")

    key: str
    namespace: str
    value: JsonValue


_LOOKUP_KEY_INFO = b"sarj.cache-entry.h1.lookup-key"
_ENVELOPE_PREFIX = "f1."
_MAX_ROTATION_KEYS = 3
_BULK_LOOKUP_PREFIX_THRESHOLD = 8
_NAMESPACE_PREFIX_CACHE_MAX = 8


def _namespace_hmac_states(lookup_keys: tuple[bytes, ...], namespace: str) -> tuple[hmac.HMAC, ...]:
    namespace_bytes = namespace.encode()
    prefix = len(namespace_bytes).to_bytes(8, "big") + namespace_bytes
    return tuple(hmac.new(key, prefix, hashlib.sha256) for key in lookup_keys)


class InvalidCacheEntryError(ValueError):
    pass


@dataclass(frozen=True, slots=True, init=False)
class CacheEntryProtector:
    _fernets: tuple[Fernet, ...] = field(init=False, repr=False)
    _lookup_keys: tuple[bytes, ...] = field(init=False, repr=False)
    _decryptor: MultiFernet = field(init=False, repr=False)
    _initial_states: Callable[[str], tuple[hmac.HMAC, ...]] = field(init=False, repr=False)

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
        object.__setattr__(self, "_decryptor", MultiFernet(list(fernets)))
        # Only namespace seeds are retained, bounded per immutable protector.
        # Logical keys and values never enter this cache; copied states remain
        # request-local and a new rotation configuration builds new seeds.
        initial_states = lru_cache(maxsize=_NAMESPACE_PREFIX_CACHE_MAX)(
            partial(_namespace_hmac_states, lookup_keys)
        )
        object.__setattr__(self, "_initial_states", initial_states)

    @classmethod
    def from_key_string(cls, raw: str) -> Self:
        return cls(tuple(parse_fernet_keys(raw)))

    def physical_keys(self, namespace: str, logical_key: str) -> tuple[str, ...]:
        logical_key_bytes = logical_key.encode()
        suffix = len(logical_key_bytes).to_bytes(8, "big") + logical_key_bytes
        candidates: list[str] = []
        for initial in self._initial_states(namespace):
            state = initial.copy()
            state.update(suffix)
            candidates.append("h1." + urlsafe_b64encode(state.digest()).rstrip(b"=").decode())
        return tuple(candidates)

    def physical_keys_many(
        self,
        namespace: str,
        logical_keys: Sequence[str],
        *,
        include_rotation: bool,
    ) -> dict[str, tuple[str, ...]]:
        limit = len(self._lookup_keys) if include_rotation else 1
        if len(logical_keys) < _BULK_LOOKUP_PREFIX_THRESHOLD:
            return {
                logical_key: self.physical_keys(namespace, logical_key)[:limit]
                for logical_key in logical_keys
            }
        namespace_bytes = namespace.encode()
        prefix = len(namespace_bytes).to_bytes(8, "big") + namespace_bytes
        initial_states = tuple(
            hmac.new(key, prefix, hashlib.sha256) for key in self._lookup_keys[:limit]
        )
        physical: dict[str, tuple[str, ...]] = {}
        for logical_key in logical_keys:
            logical_bytes = logical_key.encode()
            suffix = len(logical_bytes).to_bytes(8, "big") + logical_bytes
            candidates: list[str] = []
            for initial in initial_states:
                state = initial.copy()
                state.update(suffix)
                candidates.append("h1." + urlsafe_b64encode(state.digest()).rstrip(b"=").decode())
            physical[logical_key] = tuple(candidates)
        return physical

    def protect(self, namespace: str, logical_key: str, value: JsonValue) -> str:
        payload = _CacheEnvelope(key=logical_key, namespace=namespace, value=value)
        try:
            plaintext = to_json(payload)
        except PydanticSerializationError:
            # The typed serializer's nesting limit is slightly lower than
            # JsonValue validation. Preserve previously supported deep values.
            plaintext = json.dumps(
                {"key": payload.key, "namespace": payload.namespace, "value": payload.value},
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
            plaintext = self._decryptor.decrypt(token.encode())
            try:
                payload = _CacheEnvelope.model_validate_json(plaintext)
            except ValidationError:
                # jiter's parser stops before the legacy JsonValue nesting
                # limit. Strict envelope validation still binds every field.
                payload = _CacheEnvelope.model_validate(json.loads(plaintext))
        except (InvalidToken, ValidationError, json.JSONDecodeError, UnicodeDecodeError) as error:
            raise InvalidCacheEntryError from error
        if payload.namespace != namespace or payload.key != logical_key:
            raise InvalidCacheEntryError
        return payload.value

    @staticmethod
    def _derive_lookup_key(key: str) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=_LOOKUP_KEY_INFO,
        ).derive(urlsafe_b64decode(key.encode()))
