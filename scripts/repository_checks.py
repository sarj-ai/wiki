"""Executable repository consistency checks."""  # ruff: ignore[implicit-namespace-package] -- standalone repository CLI, not an import package.

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


def check_node() -> None:
    executable = shutil.which("node")
    if executable is None:
        message = "node >= 26.7.0 required. Run mise install."
        raise RuntimeError(message)
    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] -- resolved active Node executable, no shell
        [executable, "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    version = result.stdout.strip()
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
    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] -- fixed git executable, no shell
        [_git_executable(), "ls-files", "--", "*.md"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return sorted(line for line in result.stdout.splitlines() if line and (ROOT / line).is_file())


def _git_executable() -> str:
    executable = shutil.which("git")
    if executable is None:
        message = "git is required"
        raise RuntimeError(message)
    return executable


def _settings_fields() -> set[str]:
    backend = str(ROOT / "backend")
    sys.path.insert(0, backend)
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
    for field in declared_fields:
        os.environ.pop(field, None)  # ruff: ignore[banned-api] -- hermetic import setup
    os.environ.update(  # ruff: ignore[banned-api] -- hermetic import setup
        {
            "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@localhost:5432/wiki",
            "SECRET_KEY": "x" * 32,
            "GOOGLE_CLIENT_ID": "repository-check",
            "GOOGLE_CLIENT_SECRET": "repository-check",
            "FERNET_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
        }
    )
    from app.config import (  # ruff: ignore[import-outside-top-level] -- after env setup
        Settings,
    )

    return set(Settings.model_fields)


if __name__ == "__main__":
    CLI()
