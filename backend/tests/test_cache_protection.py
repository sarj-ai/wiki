from __future__ import annotations

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
