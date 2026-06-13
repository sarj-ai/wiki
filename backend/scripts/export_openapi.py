from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from cryptography.fernet import Fernet


os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())  # noqa: TID251 — pre-import env setup
os.environ.setdefault("SECRET_KEY", "x" * 40)  # noqa: TID251 — pre-import env setup
os.environ.setdefault("GOOGLE_CLIENT_ID", "export-spec")  # noqa: TID251 — pre-import env setup
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "export-spec")  # noqa: TID251 — pre-import env setup
os.environ.setdefault(  # noqa: TID251 — pre-import env setup
    "DATABASE_URL",
    "postgresql+asyncpg://export:export@localhost:5432/export",
)


def main() -> None:
    from app.main import app

    out = Path(sys.argv[1] if len(sys.argv) > 1 else "../frontend/openapi.json")
    spec = app.openapi()
    out.write_text(
        json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
