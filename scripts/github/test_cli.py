import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from scripts.backend_release import recovery
from scripts.frontend_release import rollback
from scripts.github import branch_protection, release_gate
from typer.testing import CliRunner

from scripts import repository_checks


if TYPE_CHECKING:
    import typer

_USAGE_ERROR = 2


@pytest.mark.parametrize(
    ("version", "exit_code"),
    [
        ("v24.21.0", 1),
        ("v26.6.99", 1),
        ("v26.7.0", 0),
        ("v26.10.0", 0),
        ("invalid", 1),
        (None, 1),
    ],
    ids=[
        "older-major",
        "older-minor",
        "minimum",
        "newer-minor",
        "malformed",
        "missing",
    ],
)
def test_node_guard_checks_the_active_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str | None, exit_code: int
) -> None:
    if version is not None:
        node = tmp_path / "node"
        node.write_text(f"#!/bin/sh\nprintf '%s\\n' '{version}'\n", encoding="utf-8")
        node.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    result = CliRunner().invoke(repository_checks.CLI, ["node"])

    assert result.exit_code == exit_code
    if exit_code:
        assert "repository check failed:" in result.stderr


@pytest.mark.parametrize("github_output", [False, True])
def test_source_cli_preserves_verified_identity_output(
    monkeypatch: pytest.MonkeyPatch, *, github_output: bool
) -> None:
    sha = "a" * 40
    monkeypatch.setattr(  # sarj-noqa: SARJ445 -- the Typer entrypoint resolves verification globally; test output dispatch without cloud reads.
        recovery, "verify_current_release", lambda _args: sha
    )
    arguments = ["--verifier-sha", sha, "source", "--run-id", "1", "--run-attempt", "1"]
    if github_output:
        arguments.append("--github-output")

    result = CliRunner().invoke(recovery.CLI, arguments)

    assert result.exit_code == 0
    prefix = "source_sha=" if github_output else ""
    assert result.stdout == f"{prefix}{sha}\n"


@pytest.mark.parametrize("github_output", [False, True])
def test_active_cli_preserves_verified_version_output(
    monkeypatch: pytest.MonkeyPatch, *, github_output: bool
) -> None:
    version = "12345678-abcd-1234-abcd-123456789abc"
    monkeypatch.setattr(  # sarj-noqa: SARJ445 -- the Typer entrypoint resolves deployment globally; test output dispatch without cloud reads.
        rollback, "current_deployment", lambda: rollback.Deployment(version, version)
    )
    arguments = ["active"]
    if github_output:
        arguments.append("--github-output")

    result = CliRunner().invoke(rollback.CLI, arguments)

    assert result.exit_code == 0
    prefix = "version_id=" if github_output else ""
    assert result.stdout == f"{prefix}{version}\n"


@pytest.mark.parametrize("cli", [recovery.CLI, rollback.CLI])
def test_github_output_is_not_published_when_verification_fails(
    monkeypatch: pytest.MonkeyPatch, cli: typer.Typer
) -> None:
    def blocked(*_args: object) -> None:
        message = "Unverified identity"
        raise release_gate.ReleaseBlockedError(message)

    monkeypatch.setattr(  # sarj-noqa: SARJ445 -- the Typer entrypoint resolves verification globally; test failure output without cloud reads.
        recovery, "verify_current_release", blocked
    )
    monkeypatch.setattr(  # sarj-noqa: SARJ445 -- the Typer entrypoint resolves deployment globally; test failure output without cloud reads.
        rollback, "current_deployment", blocked
    )
    arguments = (
        ["--verifier-sha", "a" * 40, "source", "--run-id", "1", "--run-attempt", "1"]
        if cli is recovery.CLI
        else ["active"]
    )

    result = CliRunner().invoke(cli, [*arguments, "--github-output"])

    assert result.exit_code == 1
    assert not result.stdout


@pytest.fixture
def forbid_operations(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Rejected arguments or help must not run an operation")

    monkeypatch.setattr(release_gate, "execute", forbidden)
    monkeypatch.setattr(recovery, "verify_current_release", forbidden)
    monkeypatch.setattr(rollback, "current_deployment", forbidden)
    monkeypatch.setattr(rollback, "reconcile", forbidden)
    monkeypatch.setattr(branch_protection, "_run", forbidden)
    monkeypatch.setattr(repository_checks, "_run", forbidden)


@pytest.mark.usefixtures("forbid_operations")
@pytest.mark.parametrize(
    "cli",
    [
        release_gate.CLI,
        recovery.CLI,
        rollback.CLI,
        branch_protection.CLI,
        repository_checks.CLI,
    ],
    ids=["release-gate", "recovery", "rollback", "protection", "repository"],
)
@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_never_executes(cli: typer.Typer, flag: str) -> None:
    assert CliRunner().invoke(cli, [flag]).exit_code == 0


@pytest.mark.usefixtures("forbid_operations")
@pytest.mark.parametrize(
    ("cli", "arguments"),
    [
        (release_gate.CLI, []),
        (release_gate.CLI, ["invalid", "backend"]),
        (release_gate.CLI, ["ready", "invalid"]),
        (recovery.CLI, ["source", "--run-id", "1", "--run-attempt", "1"]),
        (recovery.CLI, ["--verifier-sha", "a" * 40, "source"]),
        (
            recovery.CLI,
            [
                "--verifier-sha",
                "a" * 40,
                "source",
                "--run-id",
                "bad",
                "--run-attempt",
                "1",
            ],
        ),
        (recovery.CLI, ["--verifier-sha", "a" * 40, "serving"]),
        (rollback.CLI, ["rollback"]),
        (rollback.CLI, ["rollback", "--previous", "version"]),
        (rollback.CLI, ["active", "extra"]),
        (branch_protection.CLI, ["invalid"]),
        (branch_protection.CLI, ["check", "--unknown"]),
        (repository_checks.CLI, ["invalid"]),
        (repository_checks.CLI, ["all", "extra"]),
    ],
    ids=[
        "missing-release",
        "invalid-phase",
        "invalid-component",
        "missing-verifier",
        "missing-source-run",
        "invalid-run-id",
        "missing-serving-options",
        "missing-rollback",
        "missing-receipt",
        "active-extra",
        "invalid-protection",
        "protection-unknown",
        "invalid-repository",
        "repository-extra",
    ],
)
def test_invalid_arguments_never_execute(cli: typer.Typer, arguments: list[str]) -> None:
    assert CliRunner().invoke(cli, arguments).exit_code == _USAGE_ERROR


def test_recovery_source_passes_typed_values(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[recovery.SourceArguments | recovery.ServingArguments] = []

    def verify(arguments: recovery.SourceArguments | recovery.ServingArguments) -> str:
        calls.append(arguments)
        return "b" * 40

    monkeypatch.setattr(recovery, "verify_current_release", verify)
    result = CliRunner().invoke(
        recovery.CLI,
        ["--verifier-sha", "a" * 40, "source", "--run-id", "123", "--run-attempt", "2"],
    )
    assert result.exit_code == 0
    assert result.stdout == "b" * 40 + "\n"
    assert calls == [
        recovery.SourceArguments(verifier_sha="a" * 40, run_id=recovery.RunId(123), run_attempt=2)
    ]


def test_recovery_serving_passes_typed_values(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[recovery.SourceArguments | recovery.ServingArguments] = []
    monkeypatch.setattr(recovery, "verify_current_release", calls.append)
    result = CliRunner().invoke(
        recovery.CLI,
        [
            "--verifier-sha",
            "a" * 40,
            "serving",
            "--source-sha",
            "b" * 40,
            "--project",
            "project",
            "--region",
            "region",
            "--repository",
            "repository",
            "--service",
            "service",
            "--image",
            "image",
        ],
    )
    assert result.exit_code == 0
    assert result.stdout == "Read-only recovery evidence verified.\n"
    assert calls == [
        recovery.ServingArguments(
            verifier_sha="a" * 40,
            source_sha="b" * 40,
            project="project",
            region="region",
            repository="repository",
            service="service",
            image="image",
        )
    ]


def test_rollback_passes_receipt_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, Path]] = []

    def reconcile(previous: str, receipt: Path) -> None:
        calls.append((previous, receipt))

    monkeypatch.setattr(rollback, "reconcile", reconcile)
    result = CliRunner().invoke(
        rollback.CLI, ["rollback", "--previous", "version", "--receipt", "receipt.json"]
    )
    assert result.exit_code == 0
    assert calls == [("version", Path("receipt.json"))]
    assert result.stdout == "Previous frontend version is verified.\n"


@pytest.mark.parametrize("command", ["apply", "rollback"])
def test_protection_mutation_is_rejected_without_confirmation(
    command: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def api(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Unconfirmed mutation must not access GitHub")

    monkeypatch.setattr(branch_protection, "_api", api)
    result = CliRunner().invoke(branch_protection.CLI, [command])
    assert result.exit_code == 1
    assert "branch protection failed:" in result.stderr


def test_release_error_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    def execute(*_args: object) -> str:
        message = "private receipt details"
        raise ValueError(message)

    monkeypatch.setattr(release_gate, "execute", execute)
    result = CliRunner().invoke(release_gate.CLI, ["ready", "backend"])
    assert result.exit_code == 1
    assert result.stderr == "Release blocked: verification unavailable\n"


@pytest.mark.parametrize("main_advances", [False, True])
def test_components_cli_publishes_selection_only_for_fresh_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, main_advances: bool
) -> None:
    sha = "a" * 40
    output = tmp_path / "github-output"
    main_reads: list[str] = []

    def fetch(endpoint: str) -> release_gate.JsonObject:
        if endpoint != release_gate.MAIN_REF:
            message = "No prior shared release"
            raise release_gate.ReleaseBlockedError(message)
        main_reads.append(endpoint)
        target = "b" * 40 if main_advances and len(main_reads) > 1 else sha
        return {"ref": "refs/heads/main", "object": {"type": "commit", "sha": target}}

    def command(arguments: list[str]) -> str:
        assert arguments == ["git", "rev-parse", "HEAD"]
        return sha

    monkeypatch.setattr(  # sarj-noqa: SARJ445 -- exercise real CLI dispatch and freshness fences with deterministic read-only receipts.
        release_gate, "fetch_github", fetch
    )
    monkeypatch.setattr(  # sarj-noqa: SARJ445 -- the CLI resolves its checkout globally; avoid depending on the test runner's worktree.
        release_gate, "command_output", command
    )
    environment = {
        "GITHUB_SHA": sha,
        "GITHUB_REPOSITORY": release_gate.REPOSITORY,
        "GITHUB_REPOSITORY_ID": str(release_gate.REPOSITORY_ID),
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_WORKFLOW_SHA": sha,
        "GITHUB_WORKFLOW_REF": release_gate.MAIN_RELEASE_REF,
        "GITHUB_OUTPUT": str(output),
    }

    result = CliRunner().invoke(
        release_gate.CLI, ["components", "main"], input=json.dumps(environment)
    )

    assert main_reads == [release_gate.MAIN_REF, release_gate.MAIN_REF]
    if main_advances:
        assert result.exit_code == 1
        assert "main advanced" in result.stderr
        assert not output.exists()
    else:
        assert result.exit_code == 0
        assert output.read_text() == "backend=true\nfrontend=true\n"
