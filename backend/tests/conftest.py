from __future__ import annotations

import os

from cryptography.fernet import Fernet


os.environb.setdefault(b"FERNET_KEY", Fernet.generate_key())
os.environb.setdefault(b"SECRET_KEY", b"x" * 40)
os.environb.setdefault(b"GOOGLE_CLIENT_ID", b"test-client-id")
os.environb.setdefault(b"GOOGLE_CLIENT_SECRET", b"test-client-secret")
os.environb.setdefault(
    b"DATABASE_URL",
    b"postgresql+asyncpg://test:test@localhost:5432/test",
)
