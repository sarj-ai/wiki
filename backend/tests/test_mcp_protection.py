import math

from cryptography.fernet import Fernet
import pytest

from app.mcp.protection import InvalidOAuthStateError, OAuthStateProtector


def test_round_trip_rotation_and_boundaries() -> None:
    old, new, lock = (Fernet.generate_key().decode() for _ in range(3))
    first = OAuthStateProtector((old,), lock_key=lock)
    rotated = OAuthStateProtector((new, old), lock_key=lock)
    value = {"access_token": "private credential", "scopes": ["openid"], "expires": 123}
    encrypted = first.protect("collection", "secret code", value)
    assert "private credential" not in encrypted
    assert "secret code" not in first.physical_keys("collection", "secret code")[0]
    assert rotated.unprotect("collection", "secret code", encrypted) == value
    assert rotated.lock_id("grant:123") == first.lock_id("grant:123")
    assert first.physical_keys("collection", "secret code")[0] in rotated.physical_keys(
        "collection", "secret code"
    )
    for collection, key in [("other", "secret code"), ("collection", "other")]:
        with pytest.raises(InvalidOAuthStateError):
            rotated.unprotect(collection, key, encrypted)
    with pytest.raises(InvalidOAuthStateError):
        first.unprotect("collection", "secret code", encrypted[:-10])


@pytest.mark.parametrize(
    "value",
    [{"x": math.nan}, {"x": math.inf}, {"x": "x" * 262_144}],
    ids=["nan", "infinity", "oversized"],
)
def test_payload_limits(value: dict[str, object]) -> None:
    protector = OAuthStateProtector(
        (Fernet.generate_key().decode(),), lock_key=Fernet.generate_key().decode()
    )
    with pytest.raises(InvalidOAuthStateError, match="Invalid OAuth state"):
        protector.protect("namespace", "key", value)


def test_invalid_key_ring() -> None:
    key = Fernet.generate_key().decode()
    for keys in [(), (key, key), ("invalid",), (key,) * 4]:
        with pytest.raises(ValueError, match=r"OAuth|Fernet"):
            OAuthStateProtector(keys, lock_key=key)
