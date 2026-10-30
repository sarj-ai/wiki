from copy import deepcopy
from pathlib import Path
import runpy
import shlex
from typing import TYPE_CHECKING

import pytest
import yaml


if TYPE_CHECKING:
    from scripts.github.release_gate import JsonObject

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
    workflow = yaml.safe_load((root / f".github/workflows/deploy-{component}.yml").read_text())
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
        if step.get("run") == f"bash scripts/github/verify-release-gate.sh ready {component}"
    ]
    expected_fences = 1 if component == "backend" else 2
    assert len(verification) == expected_fences
    assert all(
        step["env"]["RELEASE_NEEDS"] == "${{ inputs.release_needs || toJSON(needs) }}"
        for step in verification
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
def test_gate_bootstraps_python_before_every_invocation(component: str, job_name: str) -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / f".github/workflows/deploy-{component}.yml").read_text())
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
        "--with",
        "typer==0.27.3",
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
    data[f"{GATE['API_PREFIX']}/actions/runs/101"].update(status="in_progress", conclusion=None)
    assert not GATE["standards_receipt"](data.__getitem__, SHA)


def context() -> dict[str, str]:
    return {
        "GITHUB_SHA": SHA,
        "GITHUB_REPOSITORY": "sarj-ai/wiki-internal",
        "GITHUB_REPOSITORY_ID": "1183627122",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_WORKFLOW_SHA": SHA,
        "GITHUB_WORKFLOW_REF": "sarj-ai/wiki-internal/.github/workflows/deploy-backend.yml@refs/heads/main",
    }


def test_exact_release_context_passes() -> None:
    assert GATE["validate_context"](context(), "backend", SHA) == SHA


@pytest.mark.parametrize(
    ("repository", "repository_id"),
    [
        ("sarj-ai/wiki-internal", 1434944257),
        ("sarj-ai/wiki", 1183627122),
        ("sarj-ai/wiki", 1434944257),
    ],
    ids=["new-public-id", "reclaimed-public-name", "public-identity"],
)
def test_public_identity_cannot_authorize_deployment(repository: str, repository_id: int) -> None:
    environment = context()
    environment.update(
        GITHUB_REPOSITORY=repository,
        GITHUB_REPOSITORY_ID=str(repository_id),
        GITHUB_WORKFLOW_REF=f"{repository}/.github/workflows/deploy-backend.yml@refs/heads/main",
    )
    with pytest.raises(GATE["ReleaseBlockedError"], match="Untrusted"):
        GATE["validate_context"](environment, "backend", SHA)
    with pytest.raises(GATE["ReleaseBlockedError"], match="Untrusted receipt repository"):
        GATE["validate_repository"]({"id": repository_id, "full_name": repository})


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
    needs: dict[str, JsonObject] = {name: {"result": "success"} for name in GATE["REQUIRED_JOBS"]}
    needs["ci-backend"]["result"] = result
    with pytest.raises(GATE["ReleaseBlockedError"], match="did not succeed"):
        GATE["validate_needs"](needs, SHA)


def test_missing_gate_blocks_release() -> None:
    with pytest.raises(GATE["ReleaseBlockedError"], match="Missing"):
        GATE["validate_needs"]({}, SHA)


@pytest.mark.parametrize("receipt_sha", [SHA, "b" * 40])
def test_all_gates_require_matching_standards_sha(receipt_sha: str) -> None:
    needs: dict[str, JsonObject] = {name: {"result": "success"} for name in GATE["REQUIRED_JOBS"]}
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
        GATE["wait_for_standards"](fetch, SHA, now=lambda: next(times), pause=lambda _seconds: None)


@pytest.mark.parametrize("component", ["main", "backend", "frontend"])
def test_shared_push_context_matches_exact_main_workflow(component: str) -> None:
    environment = {**context(), "GITHUB_WORKFLOW_REF": GATE["MAIN_RELEASE_REF"]}
    assert GATE["validate_context"](environment, component, SHA) == SHA


@pytest.mark.parametrize("event", ["pull_request", "workflow_dispatch", "workflow_run"])
def test_shared_receipt_cannot_authorize_other_events(event: str) -> None:
    environment = {
        **context(),
        "GITHUB_WORKFLOW_REF": GATE["MAIN_RELEASE_REF"],
        "GITHUB_EVENT_NAME": event,
    }
    with pytest.raises(GATE["ReleaseBlockedError"], match="event"):
        GATE["validate_context"](environment, "backend", SHA)


@pytest.mark.parametrize("selected", ["true", "false", "", None])
def test_shared_dag_requires_explicit_component_selection(selected: str | None) -> None:
    needs: dict[str, JsonObject] = {name: {"result": "success"} for name in GATE["REQUIRED_JOBS"]}
    needs["standards-receipt"]["outputs"] = {
        "release_sha": SHA,
        "backend": selected,
    }
    if selected == "true":
        GATE["validate_needs"](needs, SHA, "backend")
    else:
        with pytest.raises(GATE["ReleaseBlockedError"], match="not selected"):
            GATE["validate_needs"](needs, SHA, "backend")


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["backend/app/main.py"], {"backend": True, "frontend": False}),
        (["backend/alembic/versions/new.py"], {"backend": True, "frontend": False}),
        (["frontend/src/app/page.tsx"], {"backend": False, "frontend": True}),
        (
            ["backend/tests/test_main.py", "README.md"],
            {"backend": False, "frontend": False},
        ),
        (
            ["backend/app/deleted.py", "frontend/src/new.ts"],
            {"backend": True, "frontend": True},
        ),
        (["scripts/github/release_gate.py"], {"backend": True, "frontend": True}),
        ([".github/workflows/release-main.yml"], {"backend": True, "frontend": True}),
        (
            ["scripts/backend_release/recovery.py"],
            {"backend": True, "frontend": False},
        ),
        (
            ["scripts/frontend_release/rollback.py"],
            {"backend": False, "frontend": True},
        ),
    ],
    ids=[
        "backend-source",
        "backend-migration",
        "frontend-source",
        "non-production",
        "both-components",
        "shared-release-gate",
        "shared-workflow",
        "backend-verification-entrypoint",
        "frontend-deployment-entrypoint",
    ],
)
def test_release_selection_preserves_production_input_boundaries(
    paths: list[str], expected: dict[str, bool]
) -> None:
    assert GATE["selected_components"](paths) == expected


def baseline_receipts(sha: str) -> dict:
    repository = {"id": GATE["REPOSITORY_ID"], "full_name": GATE["REPOSITORY"]}
    return {
        GATE["MAIN_RELEASE_WORKFLOW"]: {
            "id": 303,
            "path": GATE["MAIN_RELEASE_PATH"],
            "state": "active",
        },
        f"{GATE['MAIN_RELEASE_WORKFLOW']}/runs?event=push&branch=main&status=success&per_page=1&page=1": {
            "workflow_runs": [
                {
                    "workflow_id": 303,
                    "path": GATE["MAIN_RELEASE_PATH"],
                    "repository": repository,
                    "head_repository": repository,
                    "head_sha": sha,
                    "head_branch": "main",
                    "event": "push",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        },
    }


@pytest.mark.parametrize("backend_push", ["superseded", "reverted", "merged"])
def test_cumulative_selection_includes_intervening_backend_pushes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend_push: str
) -> None:
    run = GATE["command_output"]
    # Hooks export repository-local Git variables; this test owns a disposable repo.
    for name in run(["git", "rev-parse", "--local-env-vars"]).splitlines():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    run(["git", "init", "--quiet"])
    run(["git", "config", "user.name", "CI routing test"])
    run(["git", "config", "user.email", "ci-routing@example.invalid"])
    primary = run(["git", "symbolic-ref", "--short", "HEAD"])
    (tmp_path / "README.md").write_text("baseline\n")
    (tmp_path / "backend/app").mkdir(parents=True)
    backend = tmp_path / "backend/app/main.py"
    backend.write_text("changed = False\n")
    run(["git", "add", "."])
    run(["git", "commit", "--quiet", "-m", "baseline"])
    baseline = run(["git", "rev-parse", "HEAD"])
    if backend_push == "merged":
        run(["git", "checkout", "--quiet", "-b", "backend-push"])
    backend.write_text("changed = True\n")
    run(["git", "add", "."])
    run(["git", "commit", "--quiet", "-m", "backend push superseded before deployment"])
    if backend_push == "merged":
        run(["git", "checkout", "--quiet", primary])
    elif backend_push == "reverted":
        backend.write_text("changed = False\n")
    (tmp_path / "frontend/src").mkdir(parents=True)
    (tmp_path / "frontend/src/page.tsx").write_text("frontend change\n")
    run(["git", "add", "."])
    run(["git", "commit", "--quiet", "-m", "latest frontend push"])
    if backend_push == "merged":
        run(["git", "merge", "--quiet", "--no-ff", "--no-edit", "backend-push"])
    sha = run(["git", "rev-parse", "HEAD"])
    assert GATE["release_components"](baseline_receipts(baseline).__getitem__, sha) == {
        "backend": True,
        "frontend": True,
    }


@pytest.mark.parametrize("fault", ["missing", "failed", "fork", "bad-sha", "wrong-workflow"])
def test_unusable_baseline_selects_full_release(fault: str) -> None:
    data = baseline_receipts(SHA)
    page = data[
        f"{GATE['MAIN_RELEASE_WORKFLOW']}/runs?event=push&branch=main&status=success&per_page=1&page=1"
    ]
    run = page["workflow_runs"][0]
    if fault == "missing":
        page["workflow_runs"] = []
    else:
        field, value = {
            "failed": ("conclusion", "failure"),
            "fork": ("head_repository", {}),
            "bad-sha": ("head_sha", "bad"),
            "wrong-workflow": ("workflow_id", 999),
        }[fault]
        run[field] = value
    assert GATE["release_components"](data.__getitem__, SHA) == {
        "backend": True,
        "frontend": True,
    }


def test_shared_main_dag_qualifies_once_without_release_secrets() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / GATE["MAIN_RELEASE_PATH"]).read_text())
    assert set(workflow["on"]) == {"push"}
    assert workflow["on"]["push"]["branches"] == ["main"]
    assert workflow["concurrency"] == {
        "group": "release-main",
        "cancel-in-progress": False,
    }
    jobs = workflow["jobs"]
    assert set(workflow["on"]["push"]["paths"]) == set(
        GATE["BACKEND_RELEASE_PATHS"]
        + GATE["FRONTEND_RELEASE_PATHS"]
        + GATE["SHARED_RELEASE_PATHS"]
    )
    for name in GATE["REQUIRED_JOBS"] - {"standards-receipt"}:
        assert jobs[name]["uses"] == f"./.github/workflows/{name}.yml"
        assert jobs[name]["permissions"] == {"contents": "read"}
        assert "secrets" not in jobs[name]
    for component in ("backend", "frontend"):
        job = jobs[f"{component}-deploy"]
        assert set(job["needs"]) == GATE["REQUIRED_JOBS"]
        assert job["with"] == {"release_needs": "${{ toJSON(needs) }}"}
        assert job["if"] == f"needs.standards-receipt.outputs.{component} == 'true'"
        deploy = yaml.safe_load((root / f".github/workflows/deploy-{component}.yml").read_text())
        for name in GATE["REQUIRED_JOBS"]:
            assert "inputs.release_needs == ''" in deploy["jobs"][name]["if"]
