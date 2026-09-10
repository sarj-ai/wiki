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
def test_invalid_arguments_never_execute(
    cli: typer.Typer, arguments: list[str]
) -> None:
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
        recovery.SourceArguments(verifier_sha="a" * 40, run_id=123, run_attempt=2)
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
