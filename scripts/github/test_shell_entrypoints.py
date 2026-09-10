import json
from pathlib import Path

import pytest
from scripts.github.release_gate import ReleaseBlockedError, command_output
import yaml


ROOT = Path(__file__).resolve().parents[2]


def _migration_job(command: list[str], args: list[str], image: str) -> str:
    return json.dumps(
        {
            "apiVersion": "run.googleapis.com/v1",
            "kind": "Job",
            "spec": {
                "template": {
                    "spec": {
                        "template": {
                            "spec": {
                                "containers": [
                                    {"command": command, "args": args, "image": image}
                                ]
                            }
                        }
                    }
                }
            },
        }
    )


@pytest.mark.parametrize(
    ("fault", "expected_operations"),
    [
        ("none", ["describe", "update", "execute"]),
        ("describe", ["describe"]),
        ("describe-partial", ["describe"]),
        ("command", ["describe"]),
        ("args", ["describe"]),
        ("malformed", ["describe"]),
        ("empty", ["describe"]),
        ("multiple", ["describe"]),
        ("shape", ["describe"]),
        ("update", ["describe", "update"]),
        ("update-partial", ["describe", "update"]),
        ("updated-command", ["describe", "update"]),
        ("updated-args", ["describe", "update"]),
        ("updated-malformed", ["describe", "update"]),
        ("updated-image", ["describe", "update"]),
        ("execute", ["describe", "update", "execute"]),
        ("MIGRATION_JOB", []),
        ("PROJECT_ID", []),
        ("REGION", []),
        ("RELEASE_IMAGE", []),
    ],
)
def test_migrations_only_execute_verified_image_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fault: str,
    expected_operations: list[str],
) -> None:
    initialization = tmp_path / "commands.sh"
    initialization.write_text(
        """gcloud() {
  printf '%s\\n' "$*" >> "$MIGRATION_CALLS"
  test "$3" != "$MIGRATION_FAULT" || return 1
  case "$3" in
    describe) printf '%s' "$DESCRIBED_JOB" ;;
    update) printf '%s' "$UPDATED_JOB" ;;
    execute) ;;
    *) return 1 ;;
  esac
  test "$3-partial" != "$MIGRATION_FAULT"
}
"""
    )
    described = _migration_job(
        ["wrong"] if fault == "command" else [".venv/bin/alembic"],
        ["downgrade", "base"] if fault == "args" else ["upgrade", "head"],
        "old-image",
    )
    match fault:
        case "malformed":
            described = "not-json"
        case "empty":
            described = ""
        case "multiple":
            described += described
        case "shape":
            described = "{}"
    updated = _migration_job(
        ["wrong"] if fault == "updated-command" else [".venv/bin/alembic"],
        ["downgrade", "base"] if fault == "updated-args" else ["upgrade", "head"],
        "old-image" if fault == "updated-image" else "release-image",
    )
    if fault == "updated-malformed":
        updated = "not-json"
    calls = tmp_path / "calls"
    for name, value in {
        "BASH_ENV": str(initialization),
        "MIGRATION_CALLS": str(calls),
        "MIGRATION_FAULT": fault,
        "DESCRIBED_JOB": described,
        "UPDATED_JOB": updated,
        "MIGRATION_JOB": "migration-job",
        "PROJECT_ID": "project",
        "REGION": "region",
        "RELEASE_IMAGE": "release-image",
    }.items():
        monkeypatch.setenv(name, "" if fault == name else value)
    command = ["bash", str(ROOT / "scripts/github/run-backend-migrations.sh")]
    if fault == "none":
        assert not command_output(command)
    else:
        with pytest.raises(ReleaseBlockedError):
            command_output(command)
    expected_calls = {
        "describe": "run jobs describe migration-job --project project --region region --format=json",
        "update": "run jobs update migration-job --project project --region region --image release-image --format=json --quiet",
        "execute": "run jobs execute migration-job --project project --region region --wait --quiet",
    }
    assert (calls.read_text().splitlines() if calls.exists() else []) == [
        expected_calls[operation] for operation in expected_operations
    ]


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("", ["run", "--all-files", "--show-diff-on-failure"]),
        (
            "0000",
            ["run", "--from-ref", "root", "--to-ref", "head", "--show-diff-on-failure"],
        ),
        (
            "base",
            ["run", "--from-ref", "base", "--to-ref", "head", "--show-diff-on-failure"],
        ),
    ],
)
def test_pre_commit_ref_selection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, base: str, expected: list[str]
) -> None:
    initialization = tmp_path / "commands.sh"
    initialization.write_text(
        "uvx() { printf '%s\\n' \"$@\"; }\n"
        "git() { test \"$*\" = 'rev-list --max-parents=0 HEAD' || return 1; printf root; }\n"
    )
    monkeypatch.setenv("BASH_ENV", str(initialization))
    monkeypatch.setenv("BASE_SHA", base)
    monkeypatch.setenv("HEAD_SHA", "head")
    result = command_output(["bash", str(ROOT / "scripts/github/run-pre-commit.sh")])
    assert result.splitlines() == ["--python", "3.14.7", "pre-commit==4.6.2", *expected]


def test_pre_commit_failure_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    initialization = tmp_path / "commands.sh"
    initialization.write_text("uvx() { return 1; }\n")
    monkeypatch.setenv("BASH_ENV", str(initialization))
    monkeypatch.setenv("BASE_SHA", "")
    with pytest.raises(ReleaseBlockedError):
        command_output(["bash", str(ROOT / "scripts/github/run-pre-commit.sh")])


@pytest.mark.parametrize(
    "fault", ["none", "http", "body", "request-id", "cache", "transport"]
)
def test_health_requires_all_release_receipts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str
) -> None:
    initialization = tmp_path / "commands.sh"
    initialization.write_text(
        """node() { printf canary; }
sleep() { test "$1" = 10; }
curl() {
  printf '%s\\n' call >> "$RUNNER_TEMP/calls"
  printf '{"status":"ok","revision":"sha"}' > "$RUNNER_TEMP/frontend-health-body.json"
  printf 'X-Request-ID: %s\\r\\nCache-Control: %s\\r\\n' \\
    "$RESPONSE_ID" "$CACHE_CONTROL" > "$RUNNER_TEMP/frontend-health-headers.txt"
  printf '%s' "$HTTP_STATUS"
  test "$FAULT" != transport
}
jq() { test "$FAULT" != body; }
"""
    )
    monkeypatch.setenv("BASH_ENV", str(initialization))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_SHA", "sha")
    monkeypatch.setenv("FAULT", fault)
    monkeypatch.setenv("HTTP_STATUS", "500" if fault == "http" else "200")
    monkeypatch.setenv("RESPONSE_ID", "foreign" if fault == "request-id" else "canary")
    monkeypatch.setenv(
        "CACHE_CONTROL", "public" if fault == "cache" else "private, no-store"
    )
    command = ["bash", str(ROOT / "scripts/frontend_release/verify-health.sh")]
    if fault == "none":
        assert command_output(command) == "Frontend production revision verified."
    else:
        with pytest.raises(ReleaseBlockedError):
            command_output(command)
    assert (tmp_path / "calls").read_text().splitlines() == ["call"] * (
        1 if fault == "none" else 6
    )
    assert not (tmp_path / "frontend-health-body.json").exists()
    assert not (tmp_path / "frontend-health-headers.txt").exists()


@pytest.mark.parametrize(
    ("workflow", "step_name", "entrypoint"),
    [
        ("ci-quality.yml", "Run pre-commit", "scripts/github/run-pre-commit.sh"),
        (
            "deploy-frontend.yml",
            "Verify exact production revision",
            "scripts/frontend_release/verify-health.sh",
        ),
    ],
)
def test_workflow_calls_tested_entrypoint(
    workflow: str, step_name: str, entrypoint: str
) -> None:
    document = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text())
    steps = [step for job in document["jobs"].values() for step in job.get("steps", [])]
    (step,) = [step for step in steps if step.get("name") == step_name]
    assert step["run"] == f"bash {entrypoint}"
