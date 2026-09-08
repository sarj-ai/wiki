from copy import deepcopy
from pathlib import Path
import runpy
import shlex

import pytest
import yaml


GATE = runpy.run_path(str(Path(__file__).with_name("release_gate.py")))
SHA = "a" * 40


def receipts() -> dict:
    repository = {"id": GATE["REPOSITORY_ID"], "full_name": GATE["REPOSITORY"]}
    run = {
        "id": 101,
        "workflow_id": GATE["STANDARDS_ID"],
        "path": GATE["STANDARDS_PATH"],
        "event": "push",
        "head_branch": "main",
        "head_sha": SHA,
        "repository": repository,
        "head_repository": repository,
        "status": "completed",
        "conclusion": "success",
        "check_suite_id": 202,
    }
    return {
        GATE["WORKFLOW"]: {
            "id": GATE["STANDARDS_ID"],
            "path": GATE["STANDARDS_PATH"],
            "state": "active",
        },
        f"{GATE['WORKFLOW']}/runs?event=push&branch=main&head_sha={SHA}&per_page=2&page=1": {
            "total_count": 1,
            "workflow_runs": [deepcopy(run)],
        },
        f"{GATE['API_PREFIX']}/actions/runs/101": run,
        f"{GATE['API_PREFIX']}/check-suites/202": {
            "id": 202,
            "app": {"id": 15368, "slug": "github-actions"},
            "repository": repository,
            "head_sha": SHA,
            "head_branch": "main",
            "status": "completed",
            "conclusion": "success",
        },
    }


def test_exact_trusted_standards_receipt_passes() -> None:
    assert GATE["standards_receipt"](receipts().__getitem__, SHA)


@pytest.mark.parametrize("component", ["backend", "frontend"])
def test_workflow_requires_all_local_gates_without_cloud_secrets(
    component: str,
) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load(
        (root / f".github/workflows/deploy-{component}.yml").read_text()
    )
    jobs = workflow["jobs"]
    assert set(jobs["deploy"]["needs"]) == GATE["REQUIRED_JOBS"]
    for name in GATE["REQUIRED_JOBS"] - {"standards-receipt"}:
        job = jobs[name]
        assert job["uses"] == f"./.github/workflows/{name}.yml"
        assert job["permissions"] == {"contents": "read"}
        assert "secrets" not in job
        assert "refs/heads/main" in job["if"]
        called = yaml.safe_load((root / f".github/workflows/{name}.yml").read_text())
        assert "workflow_call" in called["on"]
        assert "github.workflow" in called["concurrency"]["group"]
    assert jobs["standards-receipt"]["permissions"] == {
        "contents": "read",
        "actions": "read",
        "checks": "read",
    }
    assert "refs/heads/main" in jobs["deploy"]["if"]
    verification = [
        step
        for step in jobs["deploy"]["steps"]
        if step.get("run")
        == f"bash scripts/github/verify-release-gate.sh ready {component}"
    ]
    expected_fences = 1 if component == "backend" else 2
    assert len(verification) == expected_fences
    assert all(
        step["env"]["RELEASE_NEEDS"] == "${{ toJSON(needs) }}" for step in verification
    )


@pytest.mark.parametrize(
    ("component", "job_name"),
    [
        ("backend", "standards-receipt"),
        ("frontend", "standards-receipt"),
        ("backend", "deploy"),
        ("frontend", "deploy"),
    ],
)
def test_gate_bootstraps_python_before_every_invocation(
    component: str, job_name: str
) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load(
        (root / f".github/workflows/deploy-{component}.yml").read_text()
    )
    steps = workflow["jobs"][job_name]["steps"]
    (installer,) = [
        index
        for index, step in enumerate(steps)
        if step.get("uses", "").startswith("astral-sh/setup-uv@")
    ]
    invocations = [
        index
        for index, step in enumerate(steps)
        if "bash scripts/github/verify-release-gate.sh" in step.get("run", "")
    ]
    assert invocations
    assert all(installer < invocation for invocation in invocations)
    assert steps[installer]["with"]["version-file"] == "backend/uv.toml"
    if job_name == "deploy":
        assert "enable-cache" not in steps[installer]["with"]
    wrapper = (root / "scripts/github/verify-release-gate.sh").read_text()
    commands = shlex.split(wrapper, comments=True)
    invocation = commands[commands.index("|") + 1 :]
    assert invocation == [
        "uv",
        "run",
        "--no-project",
        "--python",
        "3.14",
        "python",
        "scripts/github/release_gate.py",
        "$@",
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workflow_id", 999),
        ("path", "other.yml"),
        ("event", "pull_request"),
        ("head_branch", "feature"),
        ("head_sha", "b" * 40),
        ("conclusion", "failure"),
        ("status", "unknown"),
        ("repository", {}),
        ("head_repository", {}),
        ("check_suite_id", True),
    ],
)
def test_invalid_run_receipt_is_rejected(field: str, value: object) -> None:
    data = receipts()
    data[f"{GATE['API_PREFIX']}/actions/runs/101"][field] = value
    with pytest.raises(GATE["ReleaseBlockedError"]):
        GATE["standards_receipt"](data.__getitem__, SHA)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("app", {"id": 42, "slug": "github-actions"}),
        ("repository", {}),
        ("head_sha", "b" * 40),
        ("head_branch", "feature"),
        ("status", "in_progress"),
        ("conclusion", "failure"),
    ],
)
def test_invalid_check_suite_is_rejected(field: str, value: object) -> None:
    data = receipts()
    data[f"{GATE['API_PREFIX']}/check-suites/202"][field] = value
    with pytest.raises(GATE["ReleaseBlockedError"]):
        GATE["standards_receipt"](data.__getitem__, SHA)


def test_ambiguous_paginated_runs_fail_closed() -> None:
    data = receipts()
    listing = next(value for value in data.values() if "total_count" in value)
    listing["total_count"] = 2
    with pytest.raises(GATE["ReleaseBlockedError"], match="Ambiguous"):
        GATE["standards_receipt"](data.__getitem__, SHA)


def test_pending_run_waits_without_accepting_success() -> None:
    data = receipts()
    data[f"{GATE['API_PREFIX']}/actions/runs/101"].update(
        status="in_progress", conclusion=None
    )
    assert not GATE["standards_receipt"](data.__getitem__, SHA)


def context() -> dict[str, str]:
    return {
        "GITHUB_SHA": SHA,
        "GITHUB_REPOSITORY": "sarj-ai/wiki",
        "GITHUB_REPOSITORY_ID": "1183627122",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_WORKFLOW_SHA": SHA,
        "GITHUB_WORKFLOW_REF": "sarj-ai/wiki/.github/workflows/deploy-backend.yml@refs/heads/main",
    }


def test_exact_release_context_passes() -> None:
    assert GATE["validate_context"](context(), "backend", SHA) == SHA


@pytest.mark.parametrize("key", list(context()))
def test_wrong_release_identity_fails(key: str) -> None:
    environment = context()
    environment[key] = "untrusted"
    with pytest.raises(GATE["ReleaseBlockedError"]):
        GATE["validate_context"](environment, "backend", SHA)


def test_wrong_checkout_fails() -> None:
    with pytest.raises(GATE["ReleaseBlockedError"], match="Checkout"):
        GATE["validate_context"](context(), "backend", "b" * 40)


def test_main_advance_blocks_release() -> None:
    receipt = {"ref": "refs/heads/main", "object": {"type": "commit", "sha": "b" * 40}}
    with pytest.raises(GATE["ReleaseBlockedError"], match="stale"):
        GATE["require_current_main"](lambda _endpoint: receipt, SHA)


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", None])
def test_any_unsuccessful_gate_blocks_release(result: str | None) -> None:
    needs = {name: {"result": "success"} for name in GATE["REQUIRED_JOBS"]}
    needs["ci-backend"]["result"] = result
    with pytest.raises(GATE["ReleaseBlockedError"], match="did not succeed"):
        GATE["validate_needs"](needs, SHA)


def test_missing_gate_blocks_release() -> None:
    with pytest.raises(GATE["ReleaseBlockedError"], match="Missing"):
        GATE["validate_needs"]({}, SHA)


@pytest.mark.parametrize("receipt_sha", [SHA, "b" * 40])
def test_all_gates_require_matching_standards_sha(receipt_sha: str) -> None:
    needs = {name: {"result": "success"} for name in GATE["REQUIRED_JOBS"]}
    needs["standards-receipt"]["outputs"] = {"release_sha": receipt_sha}
    if receipt_sha == SHA:
        GATE["validate_needs"](needs, SHA)
    else:
        with pytest.raises(GATE["ReleaseBlockedError"], match="SHA"):
            GATE["validate_needs"](needs, SHA)


def test_missing_standards_times_out() -> None:
    times = iter([0, 0, 1201])

    def fetch(endpoint: str) -> dict:
        if endpoint == GATE["MAIN_REF"]:
            return {"ref": "refs/heads/main", "object": {"type": "commit", "sha": SHA}}
        if endpoint == GATE["WORKFLOW"]:
            return {
                "id": GATE["STANDARDS_ID"],
                "path": GATE["STANDARDS_PATH"],
                "state": "active",
            }
        return {"total_count": 0, "workflow_runs": []}

    with pytest.raises(GATE["ReleaseBlockedError"], match="deadline"):
        GATE["wait_for_standards"](
            fetch, SHA, now=lambda: next(times), pause=lambda _seconds: None
        )
