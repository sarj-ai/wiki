import argparse
from copy import deepcopy
import json
from pathlib import Path

import pytest
from scripts.backend_release import recovery
import yaml


SHA = "a" * 40
RUN_ID = 123
REVISION = "sarj-docs-backend-dammam-00001-test"
DIGEST = "sha256:" + "b" * 64


def receipts() -> dict:
    repository = {"id": 1183627122, "full_name": "sarj-ai/wiki"}
    run = {
        "id": RUN_ID,
        "workflow_id": recovery.WORKFLOW_ID,
        "path": recovery.WORKFLOW_PATH,
        "head_sha": SHA,
        "head_branch": "main",
        "event": "push",
        "status": "completed",
        "run_attempt": 1,
        "repository": repository,
        "head_repository": repository,
        "conclusion": "failure",
    }
    job = {
        "name": "deploy",
        "run_id": RUN_ID,
        "run_attempt": 1,
        "head_sha": SHA,
        "status": "completed",
        "steps": [
            {"name": name, "number": index, "status": "completed", "conclusion": "success"}
            for index, name in enumerate(recovery.REQUIRED_STEPS, start=1)
        ],
    }
    return {
        f"{recovery.API_PREFIX}/actions/runs/{RUN_ID}": run,
        f"{recovery.API_PREFIX}/actions/runs/{RUN_ID}/attempts/1/jobs?per_page=100&page=1": {
            "total_count": 1,
            "jobs": [job],
        },
    }


def service() -> dict:
    return {
        "status": {
            "latestReadyRevisionName": REVISION,
            "traffic": [{"revisionName": REVISION, "percent": 100}],
        }
    }


def test_completed_mutations_authorize_read_only_verification() -> None:
    recovery.verify_source(receipts().__getitem__, SHA, RUN_ID)


@pytest.mark.parametrize("advance", [False, True])
def test_current_main_is_rechecked_after_receipt_reads(
    monkeypatch: pytest.MonkeyPatch, *, advance: bool
) -> None:
    data = receipts()
    reads = []

    def fetch(endpoint: str) -> dict:
        if endpoint.endswith("/git/ref/heads/main"):
            reads.append(endpoint)
            current = SHA if not advance or len(reads) == 1 else "b" * 40
            return {"ref": "refs/heads/main", "object": {"type": "commit", "sha": current}}
        return data[endpoint]

    monkeypatch.setattr(recovery, "fetch_github", fetch)
    args = argparse.Namespace(sha=SHA, phase="source", run_id=RUN_ID)
    if advance:
        with pytest.raises(recovery.ReleaseBlockedError, match="main advanced"):
            recovery.verify_current_release(args)
    else:
        recovery.verify_current_release(args)
    first_read, final_read = reads
    assert first_read == final_read


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workflow_id", 1),
        ("path", "other.yml"),
        ("head_sha", "b" * 40),
        ("head_branch", "feature"),
        ("event", "pull_request"),
        ("status", "in_progress"),
        ("repository", {}),
        ("head_repository", {}),
        ("run_attempt", True),
    ],
)
def test_untrusted_source_is_rejected(field: str, value: object) -> None:
    data = receipts()
    data[f"{recovery.API_PREFIX}/actions/runs/{RUN_ID}"][field] = value
    with pytest.raises(recovery.ReleaseBlockedError):
        recovery.verify_source(data.__getitem__, SHA, RUN_ID)


@pytest.mark.parametrize("conclusion", ["failure", "skipped", "cancelled", None])
@pytest.mark.parametrize("step_name", recovery.REQUIRED_STEPS)
def test_incomplete_mutation_never_authorizes_recovery(
    step_name: str, conclusion: str | None
) -> None:
    data = receipts()
    page = data[f"{recovery.API_PREFIX}/actions/runs/{RUN_ID}/attempts/1/jobs?per_page=100&page=1"]
    for step in page["jobs"][0]["steps"]:
        if step["name"] == step_name:
            step["conclusion"] = conclusion
    with pytest.raises(recovery.ReleaseBlockedError):
        recovery.verify_source(data.__getitem__, SHA, RUN_ID)


@pytest.mark.parametrize(
    "case",
    [
        "missing-job",
        "duplicate-job",
        "truncated",
        "wrong-attempt",
        "wrong-sha",
        "wrong-order",
        "duplicate-step",
    ],
)
def test_ambiguous_or_mismatched_job_receipts_block(case: str) -> None:
    data = receipts()
    page = data[f"{recovery.API_PREFIX}/actions/runs/{RUN_ID}/attempts/1/jobs?per_page=100&page=1"]
    job = page["jobs"][0]
    if case == "missing-job":
        job["name"] = "verify-existing"
    elif case == "duplicate-job":
        page["jobs"].append(deepcopy(job))
        page["total_count"] = len(page["jobs"])
    elif case == "truncated":
        page["total_count"] += 1
    elif case == "wrong-attempt":
        job["run_attempt"] += 1
    elif case == "wrong-sha":
        job["head_sha"] = "b" * 40
    elif case == "wrong-order":
        job["steps"][-1]["number"] = 1
    else:
        job["steps"].append(deepcopy(job["steps"][0]))
    with pytest.raises(recovery.ReleaseBlockedError):
        recovery.verify_source(data.__getitem__, SHA, RUN_ID)


def test_single_latest_ready_revision_is_required() -> None:
    assert recovery.serving_revision(service()) == REVISION


@pytest.mark.parametrize("case", ["split", "not-full", "not-latest", "invalid-name", "missing"])
def test_unverified_serving_revision_is_rejected(case: str) -> None:
    data = service()
    status = data["status"]
    if case == "split":
        status["traffic"].append({"revisionName": "foreign", "percent": 0})
    elif case == "not-full":
        status["traffic"][0]["percent"] = 50
    elif case == "not-latest":
        status["latestReadyRevisionName"] = "foreign"
    elif case == "invalid-name":
        status["latestReadyRevisionName"] = "--help"
        status["traffic"][0]["revisionName"] = "--help"
    else:
        status["traffic"] = []
    with pytest.raises(recovery.ReleaseBlockedError):
        recovery.serving_revision(data)


@pytest.mark.parametrize("matching", [True, False])
def test_serving_digest_is_checked_with_only_describe_commands(
    monkeypatch: pytest.MonkeyPatch, *, matching: bool
) -> None:
    image = "me-central2-docker.pkg.dev/sarj-docs/sarj-docs-dammam/backend"
    responses = iter(
        [DIGEST, json.dumps(service()), f"{image}@{DIGEST}" if matching else "foreign"]
    )
    commands = []

    def execute(command: list[str]) -> str:
        commands.append(command)
        return next(responses)

    monkeypatch.setattr(recovery, "command_output", execute)
    args = argparse.Namespace(
        sha=SHA,
        region="me-central2",
        project="sarj-docs",
        repository="sarj-docs-dammam",
        service="sarj-docs-backend-dammam",
        image="backend",
    )
    if matching:
        recovery.verify_serving(args)
    else:
        with pytest.raises(recovery.ReleaseBlockedError):
            recovery.verify_serving(args)
    artifact, serving, revision = commands
    assert artifact[:5] == ["gcloud", "artifacts", "docker", "images", "describe"]
    assert serving[:4] == ["gcloud", "run", "services", "describe"]
    assert revision[:4] == ["gcloud", "run", "revisions", "describe"]
    assert revision[4] == REVISION


def test_recovery_job_is_exclusive_and_contains_no_deployment_mutations() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/deploy-backend.yml").read_text())
    jobs = workflow["jobs"]
    verification = jobs["verify-existing"]
    assert "inputs.verification_run_id == ''" in jobs["deploy"]["if"]
    assert "inputs.verification_run_id != ''" in verification["if"]
    assert "github.event_name == 'workflow_dispatch'" in verification["if"]
    assert verification["needs"] == jobs["deploy"]["needs"]
    steps = verification["steps"]
    uses = [step["uses"].split("@")[0] for step in steps if "uses" in step]
    assert uses == [
        "actions/checkout",
        "astral-sh/setup-uv",
        "google-github-actions/auth",
        "google-github-actions/setup-gcloud",
    ]
    script_calls = [step["run"] for step in steps if "run" in step]
    assert script_calls == [
        "bash scripts/github/verify-release-gate.sh ready backend",
        'uv run --no-project --python 3.14 python -m scripts.backend_release.recovery \\\n  --sha "$GITHUB_SHA" source --run-id "$SOURCE_RUN_ID"\n',
        "bash scripts/backend_release/verify-serving.sh",
        "scripts/github/verify-smart-create-queue.sh",
        'echo "started_at=$(date -u +\'%Y-%m-%dT%H:%M:%S.%NZ\')" >> "$GITHUB_OUTPUT"',
        "scripts/github/verify-backend-production-health.sh",
        "scripts/github/verify-backend-correlated-logs.sh",
        "bash scripts/backend_release/verify-serving.sh",
    ]
