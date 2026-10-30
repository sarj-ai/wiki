from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Annotated

import typer


_SCHEMA_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://openapi:openapi@localhost:5432/openapi",
    "FERNET_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    "GOOGLE_CLIENT_ID": "openapi-export",
    "GOOGLE_CLIENT_SECRET": "openapi-export",
    "SECRET_KEY": "openapi-export-secret-key-value-0000",
}
_DEFAULT_OUTPUT = Path("../frontend/openapi.json")
CLI = typer.Typer(
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_enable=False,
    help="Export the API's deterministic OpenAPI schema.",
)


@CLI.command()
def main(
    output: Annotated[Path, typer.Argument(help="output path")] = _DEFAULT_OUTPUT,
    *,
    check: Annotated[
        bool,
        typer.Option("--check", help="fail if the output is missing or differs without writing it"),
    ] = False,
) -> None:
    expected = _render_spec()
    if check:
        if output.is_file() and output.read_bytes() == expected:
            return
        print(f"OpenAPI schema is out of date: {output}", file=sys.stderr)
        raise typer.Exit(1)

    _write_atomic(output, expected)


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
    for name, value in _SCHEMA_ENV.items():
        os.putenv(name, value)
    os.reload_environ()


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
    CLI()
