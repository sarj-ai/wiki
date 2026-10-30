import json
from pathlib import Path
import shlex

import pytest
from scripts.frontend_release import rollback
from scripts.github import release_gate
import yaml


PREVIOUS = "11111111-1111-4111-8111-111111111111"
CANDIDATE = "22222222-2222-4222-8222-222222222222"
FOREIGN = "33333333-3333-4333-8333-333333333333"
DEPLOYMENT = "44444444-4444-4444-8444-444444444444"


def deployment(version: str = CANDIDATE) -> list[dict]:
    return [
        {
            "id": DEPLOYMENT,
            "created_on": "2026-09-06T12:00:00Z",
            "versions": [{"version_id": version, "percentage": 100}],
        }
    ]


@pytest.fixture
def receipt(tmp_path: Path) -> Path:
    path = tmp_path / "receipt.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "deploy",
                "version": 1,
                "worker_name": "sarj-wiki",
                "worker_name_overridden": False,
                "version_id": CANDIDATE,
            }
        )
        + "\n"
    )
    return path


@pytest.mark.parametrize(
    ("versions", "expected_writes"),
    [
        ([PREVIOUS], []),
        ([CANDIDATE, CANDIDATE, PREVIOUS], [PREVIOUS]),
        ([CANDIDATE, CANDIDATE, CANDIDATE, PREVIOUS], [PREVIOUS]),
    ],
    ids=["already-restored", "owned-candidate", "eventual-restoration"],
)
def test_reconciles_only_owned_candidate(
    monkeypatch: pytest.MonkeyPatch,
    receipt: Path,
    versions: list[str],
    expected_writes: list[str],
) -> None:
    reads = iter(versions)
    writes = []
    monkeypatch.setattr(
        rollback,
        "current_deployment",
        lambda: rollback.Deployment(DEPLOYMENT, next(reads)),
    )
    monkeypatch.setattr(rollback, "command_output", writes.append)
    rollback.reconcile(PREVIOUS, receipt, pause=lambda _seconds: None)
    assert [args[args.index("rollback") + 1] for args in writes] == expected_writes


@pytest.mark.parametrize("current", [FOREIGN, CANDIDATE])
def test_foreign_or_changed_deployment_never_mutates(
    monkeypatch: pytest.MonkeyPatch, receipt: Path, current: str
) -> None:
    reads = iter(
        [
            rollback.Deployment(DEPLOYMENT, current),
            rollback.Deployment(FOREIGN, CANDIDATE),
        ]
    )
    writes = []
    monkeypatch.setattr(rollback, "current_deployment", lambda: next(reads))
    monkeypatch.setattr(rollback, "command_output", writes.append)
    with pytest.raises(rollback.ReleaseBlockedError):
        rollback.reconcile(PREVIOUS, receipt)
    assert not writes


def test_already_restored_does_not_require_candidate_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        rollback,
        "current_deployment",
        lambda: rollback.Deployment(DEPLOYMENT, PREVIOUS),
    )
    rollback.reconcile(PREVIOUS, tmp_path / "missing")


def test_missing_candidate_receipt_never_mutates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    writes = []
    monkeypatch.setattr(
        rollback,
        "current_deployment",
        lambda: rollback.Deployment(DEPLOYMENT, CANDIDATE),
    )
    monkeypatch.setattr(rollback, "command_output", writes.append)
    with pytest.raises(FileNotFoundError):
        rollback.reconcile(PREVIOUS, tmp_path / "missing")
    assert not writes


def test_read_failure_never_attempts_rollback(
    monkeypatch: pytest.MonkeyPatch, receipt: Path
) -> None:
    commands = []

    def unavailable(args: list[str]) -> str:
        commands.append(args)
        msg = "API unavailable"
        raise rollback.ReleaseBlockedError(msg)

    monkeypatch.setattr(rollback, "command_output", unavailable)
    with pytest.raises(rollback.ReleaseBlockedError):
        rollback.reconcile(PREVIOUS, receipt)
    (command,) = commands
    assert command == [
        *rollback.WRANGLER,
        "deployments",
        "list",
        "--name",
        "sarj-wiki",
        "--json",
    ]


def test_oversized_receipt_is_rejected(receipt: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rollback, "MAX_RESPONSE_BYTES", 1)
    with pytest.raises(rollback.ReleaseBlockedError):
        rollback.candidate_version(receipt)


def test_command_timeout_is_normalized_without_leaking_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*_args: object, **_kwargs: object) -> None:
        command = "wrangler"
        raise release_gate.subprocess.TimeoutExpired(command, 30, output="private-output")

    monkeypatch.setattr(release_gate.subprocess, "run", timeout)
    with pytest.raises(rollback.ReleaseBlockedError, match="completion is unconfirmed") as error:
        release_gate.command_output(["unused"])
    assert "private-output" not in str(error.value)


@pytest.mark.parametrize("after", [PREVIOUS, FOREIGN, CANDIDATE])
def test_timeout_reconciles_without_repeating_write(
    monkeypatch: pytest.MonkeyPatch, receipt: Path, after: str
) -> None:
    reads = iter([CANDIDATE, CANDIDATE] + [after] * rollback.READBACK_ATTEMPTS)
    writes = []
    monkeypatch.setattr(
        rollback,
        "current_deployment",
        lambda: rollback.Deployment(DEPLOYMENT, next(reads)),
    )

    def ambiguous_write(args: list[str]) -> str:
        writes.append(args)
        msg = "Release command timed out; completion is unconfirmed"
        raise rollback.ReleaseBlockedError(msg)

    monkeypatch.setattr(rollback, "command_output", ambiguous_write)
    if after == PREVIOUS:
        rollback.reconcile(PREVIOUS, receipt, pause=lambda _seconds: None)
    else:
        with pytest.raises(rollback.ReleaseBlockedError):
            rollback.reconcile(PREVIOUS, receipt, pause=lambda _seconds: None)
    (write,) = writes
    assert write == [
        *rollback.WRANGLER,
        "rollback",
        PREVIOUS,
        "--name",
        "sarj-wiki",
        "--message",
        "automatic ownership-verified rollback",
        "--yes",
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("worker_name", "foreign"),
        ("worker_name_overridden", True),
        ("wrangler_environment", "preview"),
        ("version", 2),
        ("version", True),
        ("version_id", "--help"),
        ("type", "version-upload"),
    ],
)
def test_untrusted_receipt_rejected(receipt: Path, field: str, value: object) -> None:
    entry = json.loads(receipt.read_text(encoding="utf-8"))
    entry[field] = value
    receipt.write_text(json.dumps(entry), encoding="utf-8")
    with pytest.raises((rollback.ReleaseBlockedError, ValueError)):
        rollback.candidate_version(receipt)


@pytest.mark.parametrize("contents", ["", "not json", "{}\n{}", "null"])
def test_missing_or_malformed_receipt_rejected(receipt: Path, contents: str) -> None:
    receipt.write_text(contents, encoding="utf-8")
    with pytest.raises((rollback.ReleaseBlockedError, ValueError)):
        rollback.candidate_version(receipt)


def test_duplicate_candidate_receipts_rejected(receipt: Path) -> None:
    receipt.write_text(receipt.read_text(encoding="utf-8") * 2, encoding="utf-8")
    with pytest.raises(rollback.ReleaseBlockedError):
        rollback.candidate_version(receipt)


@pytest.mark.parametrize(
    "bad",
    [[], {}, [None], deployment() * 2],
    ids=["empty", "object", "null", "ambiguous"],
)
def test_invalid_deployment_listing_rejected(bad: object) -> None:
    with pytest.raises(rollback.ReleaseBlockedError):
        rollback.active_deployment(json.dumps(bad))


@pytest.mark.parametrize("percentage", [50, "100", True, 101])
def test_non_full_traffic_rejected(percentage: object) -> None:
    data = deployment()
    data[0]["versions"][0]["percentage"] = percentage
    with pytest.raises(rollback.ReleaseBlockedError):
        rollback.active_deployment(json.dumps(data))


def test_split_traffic_rejected_even_with_one_full_version() -> None:
    data = deployment()
    data[0]["versions"].append({"version_id": FOREIGN, "percentage": 0})
    with pytest.raises(rollback.ReleaseBlockedError):
        rollback.active_deployment(json.dumps(data))


def test_latest_deployment_selected_by_timestamp() -> None:
    older = deployment(PREVIOUS)
    older[0]["created_on"] = "2026-09-05T12:00:00Z"
    assert rollback.active_deployment(json.dumps(deployment() + older)) == (
        DEPLOYMENT,
        CANDIDATE,
    )


def test_receipt_validates_deploy_among_other_wrangler_output(receipt: Path) -> None:
    receipt.write_text(
        '{"type":"version-upload","version":1}\n' + receipt.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert rollback.candidate_version(receipt) == CANDIDATE


def test_workflow_uses_same_fresh_receipt_for_deploy_and_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/deploy-frontend.yml").read_text())
    steps = workflow["jobs"]["deploy"]["steps"]
    (deploy,) = [step for step in steps if step.get("id") == "deploy"]
    (recover,) = [
        step for step in steps if step.get("name") == "Roll back an unverified frontend release"
    ]
    receipt_path = deploy["env"]["WRANGLER_OUTPUT_FILE_PATH"]
    assert (
        receipt_path
        == "${{ runner.temp }}/frontend-deploy-${{ github.run_id }}-${{ github.run_attempt }}.jsonl"
    )
    assert recover["env"]["CANDIDATE_RECEIPT"] == receipt_path
    guard = steps[steps.index(deploy) - 1]
    assert guard["name"] == "Require an unused deployment receipt path"
    assert guard["env"] == {"WRANGLER_OUTPUT_FILE_PATH": receipt_path}
    assert guard.get("if") is None
    assert guard.get("continue-on-error") is None
    assert shlex.split(deploy["run"]) == ["npm", "run", "deploy"]
    output = tmp_path / "deployment.jsonl"
    monkeypatch.setenv("WRANGLER_OUTPUT_FILE_PATH", str(output))
    release_gate.command_output(["bash", "-e", "-c", guard["run"]])
    output.write_text("stale receipt", encoding="utf-8")
    with pytest.raises(release_gate.ReleaseBlockedError, match="Release command failed"):
        release_gate.command_output(["bash", "-e", "-c", guard["run"]])
    invocation = shlex.split(recover["run"].replace("\\\n", ""))
    assert invocation == [
        "uv",
        "run",
        "--no-project",
        "--with",
        "typer==0.27.3",
        "--python",
        "3.14",
        "python",
        "-m",
        "scripts.frontend_release.rollback",
        "rollback",
        "--previous",
        "$PREVIOUS_VERSION",
        "--receipt",
        "$CANDIDATE_RECEIPT",
    ]
