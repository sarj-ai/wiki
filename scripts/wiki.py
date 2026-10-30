#!/usr/bin/env -S uv run --script
# /// script
# requires-python = "==3.14.8"
# dependencies = ["typer==0.27.3"]
# ///
"""Set up, verify and maintain Sarj Wiki using its native tools."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os
from pathlib import Path
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- Resolved native tools execute without a shell.
import sys
from typing import Annotated

import typer


ROOT = Path(__file__).resolve().parents[1]
# Audit the latest repository engine independently of the immutable Standards bundle.
REPOSITORY_STANDARDS_VERSION = "6.14.0"
REPOSITORY_TESTS = (
    "test_release_gate.py",
    "test_wiki_cli.py",
    "test_frontend_rollback.py",
    "test_backend_recovery.py",
    "test_iac_ci.py",
    "test_deploy_cache_contract.py",
    "test_cli.py",
    "test_shell_entrypoints.py",
)


class Check(StrEnum):
    ALL = "all"
    NODE = "node"
    QUALITY = "quality"
    BACKEND = "backend"
    FRONTEND = "frontend"
    IAC = "iac"
    REPOSITORY = "repository"


class Standards(StrEnum):
    SYNC = "sync"
    CHECK = "check"
    UPGRADE = "upgrade"
    REPOSITORY = "repository"


class Protection(StrEnum):
    CHECK = "check"
    APPLY = "apply"
    ROLLBACK = "rollback"


@dataclass(frozen=True, kw_only=True)
class Command:
    arguments: tuple[str, ...]
    environment: tuple[tuple[str, str], ...] = ()
    allow_failure: bool = False


def _backend(*arguments: str) -> Command:
    return Command(arguments=("uv", "--directory", "backend", "run", "--locked", *arguments))


def _project(*arguments: str) -> Command:
    return Command(arguments=("uv", "run", "--project", "backend", "--locked", *arguments))


def _frontend(script: str) -> Command:
    return Command(arguments=("npm", "--prefix", "frontend", "run", script))


def _isolated(distribution: str, executable: str, *arguments: str) -> tuple[str, ...]:
    return (
        "uvx",
        "--no-config",
        "--isolated",
        "--python",
        "3.14",
        "--from",
        distribution,
        executable,
        *arguments,
    )


def _standards(*arguments: str) -> Command:
    return Command(arguments=_isolated("sarj-standards-bootstrap", "code-standards", *arguments))


def _repository_standards() -> Command:
    return Command(
        arguments=_isolated(
            f"repo-standards=={REPOSITORY_STANDARDS_VERSION}",
            "repo-standards",
            "check",
            ".",
            "--mode",
            "ratchet",
            "--ratchet-warnings",
        )
    )


def _repository_checks() -> tuple[Command, ...]:
    return (
        _repository_standards(),
        _project("python", "backend/scripts/generate_sarj_loader.py", "--check"),
        _project("python", "scripts/repository_checks.py", "all"),
        _project(
            "python", "-m", "pytest", "-q", *(f"scripts/github/{name}" for name in REPOSITORY_TESTS)
        ),
    )


def checks(scope: Check) -> tuple[Command, ...]:
    if scope is Check.ALL:
        return tuple(
            command
            for part in (Check.NODE, Check.QUALITY, Check.BACKEND, Check.FRONTEND, Check.IAC)
            for command in checks(part)
        )
    if scope is Check.NODE:
        return (Command(arguments=(sys.executable, "scripts/repository_checks.py", "node")),)
    if scope is Check.QUALITY:
        return _quality_checks()
    if scope is Check.REPOSITORY:
        return _repository_checks()
    if scope is Check.IAC:
        return (Command(arguments=("bash", "scripts/check-terraform.sh")),)
    if scope is Check.BACKEND:
        return (
            _backend("deptry", "."),
            _backend("ruff", "check", "."),
            _backend("ruff", "format", "--check", "."),
            _backend("mypy", "app"),
            _backend("basedpyright", "-p", "pyrightconfig.json"),
            _backend("ty", "check", "app"),
            _backend("vulture"),
            _backend("lint-imports"),
            _backend("pytest", "--cov=app", "--cov-report=term-missing", "--cov-fail-under=70"),
        )
    return (
        *(
            _frontend(script)
            for script in (
                "generate:api",
                "format:check",
                "typecheck",
                "lint:baseline-check",
                "knip",
                "type-coverage",
            )
        ),
        Command(arguments=("npm", "--prefix", "frontend", "test")),
        Command(
            arguments=_frontend("build").arguments,
            environment=(("NEXT_PUBLIC_API_URL", "http://localhost:8000"),),
        ),
    )


def _quality_checks() -> tuple[Command, ...]:
    return (
        *_repository_checks(),
        _standards("doctor"),
        _standards("check", "--trust-repository-code"),
        _project(
            "python",
            "scripts/standards_checks/check_standards.py",
            "--verify-python-baseline",
            ".",
        ),
        _project("sarj-ratchet", "."),
        # Full subprocess fan-out can deadlock on macOS; native hooks and CI retain these checks.
        Command(
            arguments=(
                "uvx",
                "--python",
                "3.14",
                "pre-commit",
                "run",
                "--all-files",
                "--show-diff-on-failure",
            ),
            environment=(("SKIP", "actionlint,check-added-large-files"),),
        ),
        _backend("python", "scripts/export_openapi.py", "--check", "../frontend/openapi.json"),
    )


def run(commands: tuple[Command, ...]) -> None:
    for command in commands:
        executable = shutil.which(command.arguments[0])
        if executable is None:
            typer.echo(f"Required executable is unavailable: {command.arguments[0]}", err=True)
            raise typer.Exit(1)
        environment = None
        if command.environment:
            # A process boundary copies the native environment only for command-specific settings.
            environment = dict(os.environ)  # ruff: ignore[banned-api] -- Preserve native tool configuration while overriding one command's settings.
            environment.update(command.environment)
        result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] -- Fixed typed recipes and a resolved executable, with shell execution disabled.
            (executable, *command.arguments[1:]),
            cwd=ROOT,
            env=environment,
            check=False,
            shell=False,
        )
        if result.returncode and not command.allow_failure:
            raise typer.Exit(
                result.returncode if result.returncode > 0 else 128 - result.returncode
            )


CLI = typer.Typer(
    add_completion=False,
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    help=__doc__,
)


@CLI.command("check", help="Run all qualification gates or selected subsystems in order.")
def check_command(scopes: Annotated[list[Check] | None, typer.Argument()] = None) -> None:
    commands = (command for scope in scopes or [Check.ALL] for command in checks(scope))
    run(tuple(dict.fromkeys(commands)))


@CLI.command("standards", help="Maintain the pinned source and repository standards.")
def standards_command(action: Annotated[Standards, typer.Argument()] = Standards.CHECK) -> None:
    if action is Standards.SYNC:
        run((_standards("update", "--offline", "--no-install"),))
        return
    if action is Standards.REPOSITORY:
        run((_repository_standards(),))
        return
    commands = (
        _repository_standards(),
        _standards("doctor"),
        _standards("check", "--trust-repository-code"),
    )
    run((_standards("update"), *commands) if action is Standards.UPGRADE else commands)


@CLI.command("hooks", help="Install the repository's native Git hooks.")
def hooks_command() -> None:
    run((Command(arguments=("sh", "scripts/github/install-hooks.sh")),))


@CLI.command("setup", help="Install the locked backend, frontend and native Git hooks.")
def setup_command() -> None:
    run(
        (
            Command(arguments=("sh", "scripts/github/install-hooks.sh")),
            _standards("update", "--offline", "--no-install"),
            Command(arguments=("uv", "sync", "--project", "backend", "--locked")),
            Command(arguments=("npm", "ci", "--prefix", "frontend")),
            Command(
                arguments=(
                    "terraform",
                    "-chdir=terraform",
                    "init",
                    "-backend=false",
                    "-input=false",
                    "-lockfile=readonly",
                )
            ),
        )
    )


@CLI.command("audit", help="Audit production Python and JavaScript dependencies.")
def audit_command() -> None:
    run(
        (
            _backend("pip-audit"),
            Command(
                arguments=(
                    "npm",
                    "--prefix",
                    "frontend",
                    "audit",
                    "--omit=dev",
                    "--audit-level=moderate",
                )
            ),
        )
    )


@CLI.command("fix", help="Apply native safe formatter and lint fixes.")
def fix_command() -> None:
    run(
        (
            _backend("ruff", "check", "--fix", "."),
            _backend("ruff", "format", "."),
            _frontend("format"),
            Command(
                arguments=(
                    "uv",
                    "--directory",
                    "frontend",
                    "run",
                    "--no-project",
                    "node_modules/.bin/eslint",
                    "--fix",
                    "--suppressions-location",
                    "eslint-suppressions.json",
                    ".",
                ),
                allow_failure=True,
            ),
        )
    )


@CLI.command(
    "branch-protection",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    help="Inspect or apply a reviewed policy with the native confirmation guards.",
)
def protection_command(
    context: typer.Context, action: Annotated[Protection, typer.Argument()] = Protection.CHECK
) -> None:
    run(
        (
            Command(
                arguments=(
                    "uv",
                    "run",
                    "--no-project",
                    "--with",
                    "typer==0.27.3",
                    "--python",
                    "3.14",
                    "python",
                    "scripts/github/branch_protection.py",
                    action.value,
                    *context.args,
                )
            ),
        )
    )


if __name__ == "__main__":
    CLI()
