from __future__ import annotations

import os

from cryptography.fernet import Fernet


os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())  # noqa: TID251 — pre-import env setup
os.environ.setdefault("SECRET_KEY", "x" * 40)  # noqa: TID251 — pre-import env setup
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")  # noqa: TID251 — pre-import env setup
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")  # noqa: TID251 — pre-import env setup
os.environ.setdefault(  # noqa: TID251 — pre-import env setup
    "DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost:5432/test",
)
