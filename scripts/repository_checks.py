"""Executable repository consistency checks."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- resolved CLI executables, no shell
import sys
from typing import TYPE_CHECKING

import typer


if TYPE_CHECKING:
    from collections.abc import Callable


ROOT = Path(__file__).resolve().parents[1]
ENV_KEY = re.compile(r"^\s*(?:#\s*)?([A-Z][A-Z0-9_]*)\s*=")
SETTINGS_FIELD = re.compile(r"^    ([A-Z][A-Z0-9_]*):")
_SETTINGS_FIELDS_CODE = """from pydantic import BaseModel
from app.config import Settings

delimiter = Settings.model_config.get("env_nested_delimiter")
expected = set()
for name, field in Settings.model_fields.items():
    annotation = field.annotation
    if delimiter and isinstance(annotation, type) and issubclass(annotation, BaseModel):
        expected.update(f"{name}{delimiter}{child}" for child in annotation.model_fields)
    else:
        expected.add(name)
print("\\n".join(sorted(expected)))
"""


def check_node() -> None:
    executable = shutil.which("node")
    if executable is None:
        message = "node >= 26.7.0 required. Run mise install."
        raise RuntimeError(message)
    version = _command_stdout([executable, "--version"]).strip()
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", version)
    if match is None or tuple(map(int, match.groups())) < (26, 7, 0):
        message = (
            f"node >= 26.7.0 required (found {version}). Run mise install or "
            "mise exec -- uv run scripts/wiki.py check."
        )
        raise ValueError(message)


def check_markdown() -> None:
    actual = _tracked_markdown()
    if actual:
        message = f"tracked Markdown is not permitted; found {actual}"
        raise ValueError(message)


def check_env_example() -> None:
    example = ROOT / "backend" / ".env.example"
    actual = {
        match.group(1)
        for line in example.read_text(encoding="utf-8").splitlines()
        if (match := ENV_KEY.match(line))
    }
    expected = _settings_fields()
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        message = f".env.example drift: missing={missing}, unknown={unknown}"
        raise ValueError(message)


def check_all() -> None:
    check_markdown()
    check_env_example()


CLI = typer.Typer(
    add_completion=False,
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    help=__doc__,
)


def _run(check: Callable[[], None]) -> None:
    try:
        check()
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as error:
        sys.stderr.write(f"repository check failed: {error}\n")
        raise typer.Exit(1) from None


@CLI.command("all", help="Run every repository consistency check")
def all_command() -> None:
    _run(check_all)


@CLI.command("node", help="Verify the active Node version meets the repository minimum")
def node_command() -> None:
    _run(check_node)


@CLI.command("markdown", help="Require no tracked Markdown files")
def markdown_command() -> None:
    _run(check_markdown)


@CLI.command("env", help="Compare .env.example keys with Pydantic Settings")
def env_command() -> None:
    _run(check_env_example)


def _tracked_markdown() -> list[str]:
    output = _command_stdout([_git_executable(), "ls-files", "--", "*.md"], cwd=ROOT)
    return sorted(line for line in output.splitlines() if line and (ROOT / line).is_file())


def _command_stdout(arguments: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] -- resolved native executables and fixed internal argv, no shell
        arguments,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _git_executable() -> str:
    executable = shutil.which("git")
    if executable is None:
        message = "git is required"
        raise RuntimeError(message)
    return executable


def _settings_fields() -> set[str]:
    config_paths = (
        ROOT / "backend/app/config.py",
        ROOT / "backend/app/database_config.py",
    )
    declared_fields = {
        match.group(1)
        for path in config_paths
        for line in path.read_text(encoding="utf-8").splitlines()
        if (match := SETTINGS_FIELD.match(line))
    }
    prefixes = tuple(f"{field}_" for field in declared_fields)
    environment_keys = tuple(os.environ)  # ruff: ignore[banned-api] -- hermetic import excludes nested settings too
    for key in environment_keys:
        if key.upper() in declared_fields or key.upper().startswith(prefixes):
            os.environ.pop(key, None)  # ruff: ignore[banned-api] -- hermetic import setup
    os.environ.update(  # ruff: ignore[banned-api] -- hermetic import setup
        {
            "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@localhost:5432/wiki",
            "SECRET_KEY": "x" * 32,
            "GOOGLE_CLIENT_ID": "repository-check",
            "GOOGLE_CLIENT_SECRET": "repository-check",
            "FERNET_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
        }
    )
    output = _command_stdout([sys.executable, "-c", _SETTINGS_FIELDS_CODE], cwd=ROOT / "backend")
    return set(output.splitlines())


if __name__ == "__main__":
    CLI()
