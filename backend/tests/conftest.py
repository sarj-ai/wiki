"""Shared test setup — environment variables required by app.config.

Tests in this directory must never hit a real database or a real
Drive API. Fixtures below provide in-memory stand-ins.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet

# Must be set BEFORE any `app.*` import so Settings validates.
os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("SECRET_KEY", "x" * 40)
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("DRIVE_ROOT_FOLDER_ID", "test-drive-id")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost:5432/test",
)
