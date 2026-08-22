# ruff: file-ignore[implicit-namespace-package] -- standalone repository CLI, not a package
"""Executable repository documentation and consistency checks."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- fixed git CLI, no shell
import sys
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Sequence


ROOT = Path(__file__).resolve().parents[1]
MAKE_TARGET = re.compile(r"^([A-Za-z0-9_.-]+):(?:\s|$)")
MAKE_REFERENCE = re.compile(r"\bmake(?:\s+-C\s+(backend))?\s+([A-Za-z0-9_.-]+)")
ENV_KEY = re.compile(r"^\s*(?:#\s*)?([A-Z][A-Z0-9_]*)\s*=")
SETTINGS_FIELD = re.compile(r"^    ([A-Z][A-Z0-9_]*):")


def check_markdown() -> None:
    actual = _tracked_markdown()
    expected = ["README.md"]
    if actual != expected:
        message = f"tracked Markdown must be exactly {expected}; found {actual}"
        raise ValueError(message)


def check_readme() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    root_targets = _make_targets(ROOT / "Makefile")
    backend_targets = _make_targets(ROOT / "backend" / "Makefile")
    missing: list[str] = []
    for match in MAKE_REFERENCE.finditer(text):
        target = match.group(2)
        targets = backend_targets if match.group(1) == "backend" else root_targets
        if target not in targets:
            missing.append(target)
    if missing:
        message = f"README references missing Make targets: {sorted(set(missing))}"
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


def make_help(makefile: Path) -> None:
    entries: list[tuple[str, str]] = []
    for line in makefile.read_text(encoding="utf-8").splitlines():
        if " ## " not in line or not (match := MAKE_TARGET.match(line)):
            continue
        entries.append((match.group(1), line.split(" ## ", maxsplit=1)[1].strip()))
    width = max((len(target) for target, _ in entries), default=0)
    for target, description in entries:
        sys.stdout.write(f"{target:<{width}}  {description}\n")


def check_all() -> None:
    check_markdown()
    check_readme()
    check_env_example()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("all", help="run every repository consistency check")
    subparsers.add_parser(
        "markdown", help="enforce README.md as the only tracked Markdown"
    )
    subparsers.add_parser("readme", help="verify Make targets referenced by README.md")
    subparsers.add_parser(
        "env", help="compare .env.example keys with Pydantic Settings"
    )
    help_parser = subparsers.add_parser(
        "make-help", help="print annotated Make targets"
    )
    help_parser.add_argument(
        "makefile", nargs="?", type=Path, default=ROOT / "Makefile"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        _dispatch(args)
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as error:
        sys.stderr.write(f"repository check failed: {error}\n")
        return 1
    return 0


def _dispatch(args: argparse.Namespace) -> None:
    if args.command == "all":
        check_all()
    elif args.command == "markdown":
        check_markdown()
    elif args.command == "readme":
        check_readme()
    elif args.command == "env":
        check_env_example()
    else:
        make_help(args.makefile)


def _tracked_markdown() -> list[str]:
    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] -- fixed git executable, no shell
        [_git_executable(), "ls-files", "--", "*.md"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return sorted(
        line for line in result.stdout.splitlines() if line and (ROOT / line).is_file()
    )


def _git_executable() -> str:
    executable = shutil.which("git")
    if executable is None:
        message = "git is required"
        raise RuntimeError(message)
    return executable


def _make_targets(path: Path) -> set[str]:
    targets: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if match := MAKE_TARGET.match(line):
            targets.add(match.group(1))
    return targets


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
    raise SystemExit(main())
