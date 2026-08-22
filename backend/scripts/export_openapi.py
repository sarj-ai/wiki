from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile


_SCHEMA_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://openapi:openapi@localhost:5432/openapi",
    "FERNET_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    "GOOGLE_CLIENT_ID": "openapi-export",
    "GOOGLE_CLIENT_SECRET": "openapi-export",
    "SECRET_KEY": "openapi-export-secret-key-value-0000",
}


def main() -> int:
    args = _parse_args()
    expected = _render_spec()
    if args.check:
        if args.output.is_file() and args.output.read_bytes() == expected:
            return 0
        print(f"OpenAPI schema is out of date: {args.output}", file=sys.stderr)
        return 1

    _write_atomic(args.output, expected)
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export the API's deterministic OpenAPI schema.")
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        default=Path("../frontend/openapi.json"),
        help="output path (default: ../frontend/openapi.json)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the output is missing or differs without writing it",
    )
    return parser.parse_args()


def _render_spec() -> bytes:
    _configure_import_environment()

    from app.config import Settings
    from app.main import create_app

    schema_settings = Settings.model_validate(_SCHEMA_ENV)
    rendered = json.dumps(
        create_app(schema_settings).openapi(),
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )
    return f"{rendered}\n".encode()


def _configure_import_environment() -> None:
    os.environ.update(  # ruff: ignore[banned-api] — isolate application imports from ambient config
        _SCHEMA_ENV
    )


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.chmod(0o644)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
