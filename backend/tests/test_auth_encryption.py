"""Smoke tests for Fernet token encryption round-trip."""

from __future__ import annotations

import os

from cryptography.fernet import Fernet

# Set a test Fernet key before importing app code so Settings validates cleanly.
os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("SECRET_KEY", "x" * 40)
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from app.auth.encryption import decrypt_token, encrypt_token


def test_encrypt_decrypt_roundtrip() -> None:
    plaintext = "ya29.a0AfH6SMBtest-access-token-value"
    encrypted = encrypt_token(plaintext)
    assert encrypted != plaintext
    assert decrypt_token(encrypted) == plaintext


def test_encrypt_produces_different_ciphertexts() -> None:
    """Fernet uses a random IV, so each encryption produces a different ciphertext."""
    plaintext = "identical input"
    a = encrypt_token(plaintext)
    b = encrypt_token(plaintext)
    assert a != b
    assert decrypt_token(a) == plaintext
    assert decrypt_token(b) == plaintext
