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
                                "containers": [{"command": command, "args": args, "image": image}]
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
        case _:
            pass
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
    github_path = tmp_path / "github-path"
    github_path.write_text("")
    monkeypatch.setenv("GITHUB_PATH", str(github_path))
    initialization = tmp_path / "commands.sh"
    initialization.write_text(
        "uv() { printf 'native-bootstrap\\n'; }\n"
        "uvx() { printf '%s\\n' \"$@\"; }\n"
        "git() { test \"$*\" = 'rev-list --max-parents=0 HEAD' || return 1; printf root; }\n"
    )
    monkeypatch.setenv("BASH_ENV", str(initialization))
    monkeypatch.setenv("BASE_SHA", base)
    monkeypatch.setenv("HEAD_SHA", "head")
    result = command_output(["bash", str(ROOT / "scripts/github/run-pre-commit.sh")])
    assert result.splitlines() == [
        "native-bootstrap",
        "--no-config",
        "--isolated",
        "--python",
        "3.14",
        "--from",
        "sarj-standards-bootstrap",
        "code-standards",
        "setup",
        "--tools-only",
        "--python",
        "3.14.8",
        "pre-commit==4.7.0",
        *expected,
    ]


def test_pre_commit_failure_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    github_path = tmp_path / "github-path"
    github_path.write_text("")
    monkeypatch.setenv("GITHUB_PATH", str(github_path))
    initialization = tmp_path / "commands.sh"
    initialization.write_text("uv() { return 0; }\nuvx() { return 1; }\n")
    monkeypatch.setenv("BASH_ENV", str(initialization))
    monkeypatch.setenv("BASE_SHA", "")
    with pytest.raises(ReleaseBlockedError):
        command_output(["bash", str(ROOT / "scripts/github/run-pre-commit.sh")])


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "http",
        "body",
        "request-id",
        "cache",
        "transport",
        "proxy-http",
        "proxy-body",
        "proxy-transport",
        "drives-http",
    ],
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
  case "$*" in
    *'/api/wiki/'*)
      printf '{"detail":"Your session has expired"}' > "$RUNNER_TEMP/frontend-proxy-body.json"
      case "$*" in
        *'/sidebar/drives')
          if test "$FAULT" = drives-http; then printf 502; return 0; fi ;;
      esac
      if test "$FAULT" = proxy-http; then printf 502; else printf 401; fi
      test "$FAULT" != proxy-transport
      return ;;
  esac
  printf '{"status":"ok","revision":"sha"}' > "$RUNNER_TEMP/frontend-health-body.json"
  printf 'X-Request-ID: %s\\r\\nCache-Control: %s\\r\\n' \\
    "$RESPONSE_ID" "$CACHE_CONTROL" > "$RUNNER_TEMP/frontend-health-headers.txt"
  printf '%s' "$HTTP_STATUS"
  test "$FAULT" != transport
}
jq() {
  case "$*" in
    *'frontend-proxy-body.json') test "$FAULT" != proxy-body ;;
    *) test "$FAULT" != body ;;
  esac
}
"""
    )
    monkeypatch.setenv("BASH_ENV", str(initialization))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_SHA", "sha")
    monkeypatch.setenv("FAULT", fault)
    monkeypatch.setenv("HTTP_STATUS", "500" if fault == "http" else "200")
    monkeypatch.setenv("RESPONSE_ID", "foreign" if fault == "request-id" else "canary")
    monkeypatch.setenv("CACHE_CONTROL", "public" if fault == "cache" else "private, no-store")
    command = ["bash", str(ROOT / "scripts/frontend_release/verify-health.sh")]
    if fault == "none":
        assert command_output(command) == "Frontend production revision verified."
    else:
        with pytest.raises(ReleaseBlockedError):
            command_output(command)
    expected_calls = 3 if fault == "none" else 18 if fault == "drives-http" else 6
    if fault.startswith("proxy-"):
        expected_calls = 12
    assert (tmp_path / "calls").read_text().splitlines() == ["call"] * expected_calls
    assert not (tmp_path / "frontend-health-body.json").exists()
    assert not (tmp_path / "frontend-health-headers.txt").exists()
    assert not (tmp_path / "frontend-proxy-body.json").exists()


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
def test_workflow_calls_tested_entrypoint(workflow: str, step_name: str, entrypoint: str) -> None:
    document = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text())
    steps = [step for job in document["jobs"].values() for step in job.get("steps", [])]
    (step,) = [step for step in steps if step.get("name") == step_name]
    assert step["run"] == f"bash {entrypoint}"


def test_pre_commit_native_bootstrap_failure_stops_before_hook_execution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    initialization = tmp_path / "commands.sh"
    marker = tmp_path / "hook-ran"
    initialization.write_text('uv() { return 71; }\nuvx() { touch "$HOOK_MARKER"; }\n')
    monkeypatch.setenv("BASH_ENV", str(initialization))
    monkeypatch.setenv("HOOK_MARKER", str(marker))
    monkeypatch.setenv("BASE_SHA", "")
    with pytest.raises(ReleaseBlockedError):
        command_output(["bash", str(ROOT / "scripts/github/run-pre-commit.sh")])
    assert not marker.exists()


@pytest.mark.parametrize("force_full", ["true", "false"])
def test_cumulative_release_checks_every_file_without_weakening_normal_diff_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, force_full: str
) -> None:
    initialization = tmp_path / "commands.sh"
    trace = tmp_path / "trace"
    paths = tmp_path / "paths"
    paths.write_text("")
    initialization.write_text(
        'uv() { return 0; }\nuvx() { printf "%s\\n" "$*" >> "$HOOK_TRACE"; }\n'
    )
    for name, value in {
        "BASH_ENV": str(initialization),
        "HOOK_TRACE": str(trace),
        "GITHUB_PATH": str(paths),
        "FORCE_FULL": force_full,
        "BASE_SHA": "a" * 40,
        "HEAD_SHA": "b" * 40,
    }.items():
        monkeypatch.setenv(name, value)
    command_output(["bash", str(ROOT / "scripts/github/run-pre-commit.sh")])
    invocation = trace.read_text().splitlines()[-1]
    if force_full == "true":
        assert "run --all-files --show-diff-on-failure" in invocation
        assert "--from-ref" not in invocation
    else:
        assert f"run --from-ref {'a' * 40} --to-ref {'b' * 40}" in invocation
        assert "--all-files" not in invocation
