from __future__ import annotations

import os

from cryptography.fernet import Fernet


os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())  # ruff: ignore[banned-api] — pre-import env setup
os.environ.setdefault("SECRET_KEY", "x" * 40)  # ruff: ignore[banned-api] — pre-import env setup
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")  # ruff: ignore[banned-api] — pre-import env setup
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")  # ruff: ignore[banned-api] — pre-import env setup
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")  # ruff: ignore[banned-api] — pre-import env setup

from app.auth.encryption import decrypt_text, encrypt_text
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
    from cryptography.fernet import MultiFernet

    old, new = Fernet(Fernet.generate_key()), Fernet(Fernet.generate_key())
    token = old.encrypt(b"refresh-token")
    rotated = MultiFernet([new, old])
    assert rotated.decrypt(token) == b"refresh-token"
    assert MultiFernet([new]).decrypt(rotated.rotate(token)) == b"refresh-token"
