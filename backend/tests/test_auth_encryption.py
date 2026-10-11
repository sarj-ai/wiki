from __future__ import annotations

from cryptography.fernet import Fernet, MultiFernet
import pytest

from app.auth.encryption import (
    decrypt_cache,
    decrypt_cache_bytes,
    decrypt_text,
    encrypt_cache_bytes,
    encrypt_text,
)
from app.config import parse_fernet_keys


def test_encrypt_decrypt_roundtrip() -> None:
    plaintext = "ya29.a0AfH6SMBtest-access-token-value"
    encrypted = encrypt_text(plaintext)
    assert encrypted != plaintext
    assert decrypt_text(encrypted) == plaintext


def test_encrypt_produces_different_ciphertexts() -> None:
    plaintext = "identical input"
    a = encrypt_text(plaintext)
    b = encrypt_text(plaintext)
    assert a != b
    assert decrypt_text(a) == plaintext
    assert decrypt_text(b) == plaintext


def test_parse_fernet_keys_splits_and_validates() -> None:
    a, b = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    assert parse_fernet_keys(f"  {a} , {b} ") == [a, b]
    assert parse_fernet_keys(a) == [a]
    assert parse_fernet_keys("not-a-key") == []
    assert parse_fernet_keys("") == []


def test_multifernet_rotation_decrypts_old_ciphertext() -> None:
    old, new = Fernet(Fernet.generate_key()), Fernet(Fernet.generate_key())
    token = old.encrypt(b"refresh-token")
    rotated = MultiFernet([new, old])
    assert rotated.decrypt(token) == b"refresh-token"
    assert MultiFernet([new]).decrypt(rotated.rotate(token)) == b"refresh-token"


@pytest.mark.parametrize("plaintext", [b"", "Résumé 📚 知識".encode(), b"\xff\x00\xfe"])
def test_byte_cache_encryption_preserves_exact_plaintext_and_decoder_snapshot(
    plaintext: bytes,
) -> None:
    encrypted = encrypt_cache_bytes(plaintext)
    assert decrypt_cache_bytes(encrypted) == plaintext
    assert encrypt_cache_bytes(plaintext) != encrypted
    old, new = Fernet(Fernet.generate_key()), Fernet(Fernet.generate_key())
    old_ciphertext = old.encrypt(plaintext).decode()
    assert decrypt_cache_bytes(old_ciphertext, decoder=MultiFernet([new, old])) == plaintext
    if plaintext != b"\xff\x00\xfe":
        assert decrypt_cache(encrypted) == plaintext.decode()
