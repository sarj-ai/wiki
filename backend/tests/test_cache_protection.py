from __future__ import annotations

import json
import math

from cryptography.fernet import Fernet
from pydantic import JsonValue
import pytest

from app.cache.protection import CacheEntryProtector, InvalidCacheEntryError


def _key() -> str:
    return Fernet.generate_key().decode()


def test_protected_value_is_versioned_encrypted_json() -> None:
    protector = CacheEntryProtector((_key(),))
    value: JsonValue = {"drive_id": "sensitive-drive-id", "available": True}

    protected = protector.protect("drive.list", "user-id", value)

    assert protected.startswith("f1.")
    assert "sensitive-drive-id" not in str(protected)
    assert protector.unprotect("drive.list", "user-id", protected) == value


def test_encryption_is_nondeterministic_but_lookup_key_is_stable() -> None:
    protector = CacheEntryProtector((_key(),))

    assert protector.protect("drive.list", "user", ["same"]) != protector.protect(
        "drive.list",
        "user",
        ["same"],
    )
    assert protector.physical_keys("drive.list", "user-id") == protector.physical_keys(
        "drive.list",
        "user-id",
    )


def test_hmac_lookup_key_is_domain_separated_by_namespace_and_logical_key() -> None:
    protector = CacheEntryProtector((_key(),))
    base = protector.physical_keys("drive.list", "user-id")[0]

    assert base.startswith("h1.")
    assert len(base) == 46
    assert base != protector.physical_keys("template.catalog", "user-id")[0]
    assert base != protector.physical_keys("drive.list", "other-user-id")[0]
    assert "user-id" not in base


def test_rotation_candidates_are_ordered_newest_first_and_decrypt_old_values() -> None:
    new_key, old_key = _key(), _key()
    old = CacheEntryProtector((old_key,))
    rotated = CacheEntryProtector((new_key, old_key))

    assert rotated.physical_keys("drive.list", "user") == (
        CacheEntryProtector((new_key,)).physical_keys("drive.list", "user")[0],
        old.physical_keys("drive.list", "user")[0],
    )
    protected = old.protect("drive.list", "user", {"from": "old"})
    assert rotated.unprotect("drive.list", "user", protected) == {"from": "old"}


def test_ciphertext_is_bound_to_namespace_and_logical_key() -> None:
    protector = CacheEntryProtector((_key(),))
    protected = protector.protect("drive.list", "user-a", {"secret": True})

    with pytest.raises(InvalidCacheEntryError):
        protector.unprotect("drive.list", "user-b", protected)
    with pytest.raises(InvalidCacheEntryError):
        protector.unprotect("template.catalog", "user-a", protected)


@pytest.mark.parametrize(
    "invalid",
    [
        pytest.param({"drive_id": "plaintext"}, id="plaintext-object"),
        pytest.param("plaintext", id="unversioned-string"),
        pytest.param("f2.token", id="unknown-version"),
        pytest.param("f1.not-a-fernet-token", id="invalid-token"),
    ],
)
def test_plaintext_and_malformed_envelopes_fail_closed(invalid: object) -> None:
    with pytest.raises(InvalidCacheEntryError):
        CacheEntryProtector((_key(),)).unprotect("drive.list", "user", invalid)


def test_protector_is_immutable_and_rejects_missing_or_invalid_keys() -> None:
    protector = CacheEntryProtector((_key(),))

    immutable_field = "_fernets"
    with pytest.raises(AttributeError):
        setattr(protector, immutable_field, ())
    with pytest.raises(ValueError, match="at least one"):
        CacheEntryProtector(())
    with pytest.raises(ValueError, match="valid Fernet"):
        CacheEntryProtector(("invalid",))
    duplicate = _key()
    with pytest.raises(ValueError, match="unique"):
        CacheEntryProtector((duplicate, duplicate))
    with pytest.raises(ValueError, match="at most three"):
        CacheEntryProtector((_key(), _key(), _key(), _key()))


def test_repr_never_exposes_key_material() -> None:
    key = _key()

    assert key not in repr(CacheEntryProtector((key,)))


@pytest.mark.parametrize(
    "plaintext",
    [
        b"[]",
        b'{"key":123,"namespace":"drive.list","value":true}',
        b'{"key":"user","namespace":null,"value":true}',
        b'{"key":"user","namespace":"drive.list"}',
        b'{"key":"user","namespace":"drive.list","value":true,"extra":true}',
        b'{"key":"user","namespace":"drive.list","value":',
        b"\xff",
    ],
)
def test_authenticated_envelope_still_requires_a_strict_complete_json_shape(
    plaintext: bytes,
) -> None:
    key = _key()
    envelope = "f1." + Fernet(key.encode()).encrypt(plaintext).decode()

    with pytest.raises(InvalidCacheEntryError):
        CacheEntryProtector((key,)).unprotect("drive.list", "user", envelope)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), -0.0])
def test_envelope_serialization_preserves_existing_nonfinite_and_negative_zero_values(
    value: float,
) -> None:
    key = _key()
    fernet = Fernet(key.encode())
    protector = CacheEntryProtector((key,))
    legacy_plaintext = json.dumps(
        {"key": "user", "namespace": "drive.list", "value": value},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    legacy = "f1." + fernet.encrypt(legacy_plaintext).decode()
    current = protector.protect("drive.list", "user", value)
    assert fernet.decrypt(current[3:].encode()) == legacy_plaintext
    read_value = protector.unprotect("drive.list", "user", legacy)
    assert isinstance(read_value, float)
    if math.isnan(value):
        assert math.isnan(read_value)
    else:
        assert read_value == value
        assert math.copysign(1, read_value) == math.copysign(1, value)


def test_typed_envelope_and_legacy_json_reader_are_compatible_for_nested_json() -> None:
    key = _key()
    fernet = Fernet(key.encode())
    protector = CacheEntryProtector((key,))
    value: JsonValue = {"z": [None, True, 9223372036854775809], "a": {"title": "Résumé ⚡"}}
    legacy_plaintext = json.dumps(
        {"key": "user", "namespace": "drive.list", "value": value},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    assert (
        protector.unprotect("drive.list", "user", "f1." + fernet.encrypt(legacy_plaintext).decode())
        == value
    )
    current = protector.protect("drive.list", "user", value)
    decoded = json.loads(fernet.decrypt(current[3:].encode()))
    assert decoded == {"key": "user", "namespace": "drive.list", "value": value}


@pytest.mark.parametrize("depth", [199, 200, 210, 253, 254])
def test_deep_json_values_preserve_legacy_round_trip_and_binding(depth: int) -> None:
    key = _key()
    fernet = Fernet(key.encode())
    protector = CacheEntryProtector((key,))
    value: JsonValue = None
    for _ in range(depth):
        value = [value]
    legacy_plaintext = json.dumps(
        {"key": "user", "namespace": "drive.list", "value": value},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    legacy = "f1." + fernet.encrypt(legacy_plaintext).decode()
    assert protector.unprotect("drive.list", "user", legacy) == value
    assert (
        protector.unprotect("drive.list", "user", protector.protect("drive.list", "user", value))
        == value
    )
    with pytest.raises(InvalidCacheEntryError):
        protector.unprotect("drive.list", "different-user", legacy)
    with pytest.raises(InvalidCacheEntryError):
        protector.unprotect("template.catalog", "user", legacy)


@pytest.mark.parametrize("count", [1, 8, 512])
@pytest.mark.parametrize("rotation_count", [1, 3])
def test_bulk_lookup_derivation_preserves_existing_key_bytes_and_rotation_order(
    count: int, rotation_count: int
) -> None:
    protector = CacheEntryProtector(tuple(_key() for _ in range(rotation_count)))
    logical_keys = [f"permission-{index}-Résumé ⚡" for index in range(count)]
    expected = {key: protector.physical_keys("drive.list", key) for key in logical_keys}

    assert (
        protector.physical_keys_many("drive.list", logical_keys, include_rotation=True) == expected
    )
    assert protector.physical_keys_many("drive.list", logical_keys, include_rotation=False) == {
        key: candidates[:1] for key, candidates in expected.items()
    }
    other_namespace = protector.physical_keys_many(
        "template.catalog",
        logical_keys,
        include_rotation=True,
    )
    assert all(other_namespace[key] != expected[key] for key in logical_keys)
