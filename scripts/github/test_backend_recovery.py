import argparse
from copy import deepcopy
import json
from pathlib import Path

import pytest
from scripts.backend_release import recovery
import yaml


SHA = "a" * 40
RUN_ID = 123
RUN_ATTEMPT = 2
SOURCE_RUN_READ_COUNT = 2
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
        "run_attempt": RUN_ATTEMPT,
        "repository": repository,
        "head_repository": repository,
        "conclusion": "failure",
    }
    job = {
        "name": "deploy",
        "run_id": RUN_ID,
        "run_attempt": RUN_ATTEMPT,
        "head_sha": SHA,
        "status": "completed",
        "steps": [
            {
                "name": name,
                "number": index,
                "status": "completed",
                "conclusion": "success",
            }
            for index, name in enumerate(recovery.REQUIRED_STEPS, start=1)
        ],
    }
    verification_jobs = [
        {
            "name": name,
            "run_id": RUN_ID,
            "run_attempt": RUN_ATTEMPT,
            "head_sha": SHA,
            "status": "completed",
            "conclusion": "success",
            "steps": [],
        }
        for name in recovery.REQUIRED_VERIFICATION_JOBS
    ]
    return {
        f"{recovery.API_PREFIX}/actions/runs/{RUN_ID}": run,
        f"{recovery.API_PREFIX}/actions/runs/{RUN_ID}/attempts/{RUN_ATTEMPT}/jobs?per_page=100&page=1": {
            "total_count": 1 + len(verification_jobs),
            "jobs": [job, *verification_jobs],
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
    recovery.verify_source(receipts().__getitem__, SHA, RUN_ID, RUN_ATTEMPT)


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
            return {
                "ref": "refs/heads/main",
                "object": {"type": "commit", "sha": current},
            }
        return data[endpoint]

    monkeypatch.setattr(recovery, "fetch_github", fetch)
    args = argparse.Namespace(
        sha=SHA, phase="source", run_id=RUN_ID, run_attempt=RUN_ATTEMPT
    )
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
    ],
)
def test_untrusted_source_is_rejected(field: str, value: object) -> None:
    data = receipts()
    data[f"{recovery.API_PREFIX}/actions/runs/{RUN_ID}"][field] = value
    with pytest.raises(recovery.ReleaseBlockedError):
        recovery.verify_source(data.__getitem__, SHA, RUN_ID, RUN_ATTEMPT)


@pytest.mark.parametrize("run_attempt", [0, -1, True])
def test_invalid_requested_attempt_is_rejected(run_attempt: int) -> None:
    with pytest.raises(recovery.ReleaseBlockedError):
        recovery.verify_source(receipts().__getitem__, SHA, RUN_ID, run_attempt)


def test_latest_attempt_must_match_requested_attempt() -> None:
    data = receipts()
    data[f"{recovery.API_PREFIX}/actions/runs/{RUN_ID}"]["run_attempt"] = (
        RUN_ATTEMPT + 1
    )
    with pytest.raises(recovery.ReleaseBlockedError, match="attempt changed"):
        recovery.verify_source(data.__getitem__, SHA, RUN_ID, RUN_ATTEMPT)


def test_attempt_is_rechecked_after_job_receipts() -> None:
    data = receipts()
    run_endpoint = f"{recovery.API_PREFIX}/actions/runs/{RUN_ID}"
    run_reads = 0

    def fetch(endpoint: str) -> dict:
        nonlocal run_reads
        result = deepcopy(data[endpoint])
        if endpoint == run_endpoint:
            run_reads += 1
            if run_reads == SOURCE_RUN_READ_COUNT:
                result["run_attempt"] = RUN_ATTEMPT + 1
        return result

    with pytest.raises(recovery.ReleaseBlockedError, match="attempt changed"):
        recovery.verify_source(fetch, SHA, RUN_ID, RUN_ATTEMPT)
    assert run_reads == SOURCE_RUN_READ_COUNT


@pytest.mark.parametrize("conclusion", ["failure", "skipped", "cancelled", None])
@pytest.mark.parametrize("step_name", recovery.REQUIRED_STEPS)
def test_incomplete_mutation_never_authorizes_recovery(
    step_name: str, conclusion: str | None
) -> None:
    data = receipts()
    page = data[
        f"{recovery.API_PREFIX}/actions/runs/{RUN_ID}/attempts/{RUN_ATTEMPT}/jobs?per_page=100&page=1"
    ]
    for step in page["jobs"][0]["steps"]:
        if step["name"] == step_name:
            step["conclusion"] = conclusion
    with pytest.raises(recovery.ReleaseBlockedError):
        recovery.verify_source(data.__getitem__, SHA, RUN_ID, RUN_ATTEMPT)


@pytest.mark.parametrize("job_name", recovery.REQUIRED_VERIFICATION_JOBS)
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("conclusion", "failure"),
        ("status", "in_progress"),
        ("run_id", RUN_ID + 1),
        ("run_attempt", RUN_ATTEMPT + 1),
        ("head_sha", "b" * 40),
    ],
)
def test_unproven_source_verification_job_blocks_recovery(
    job_name: str, field: str, value: object
) -> None:
    data = receipts()
    page = data[
        f"{recovery.API_PREFIX}/actions/runs/{RUN_ID}/attempts/{RUN_ATTEMPT}/jobs?per_page=100&page=1"
    ]
    job = next(candidate for candidate in page["jobs"] if candidate["name"] == job_name)
    job[field] = value
    with pytest.raises(recovery.ReleaseBlockedError):
        recovery.verify_source(data.__getitem__, SHA, RUN_ID, RUN_ATTEMPT)


@pytest.mark.parametrize("case", ["missing", "duplicate"])
def test_ambiguous_source_verification_job_blocks_recovery(case: str) -> None:
    data = receipts()
    page = data[
        f"{recovery.API_PREFIX}/actions/runs/{RUN_ID}/attempts/{RUN_ATTEMPT}/jobs?per_page=100&page=1"
    ]
    name = recovery.REQUIRED_VERIFICATION_JOBS[0]
    matches = [job for job in page["jobs"] if job["name"] == name]
    assert len(matches) == 1
    if case == "missing":
        page["jobs"].remove(matches[0])
    else:
        page["jobs"].append(deepcopy(matches[0]))
    page["total_count"] = len(page["jobs"])
    with pytest.raises(recovery.ReleaseBlockedError):
        recovery.verify_source(data.__getitem__, SHA, RUN_ID, RUN_ATTEMPT)


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
    page = data[
        f"{recovery.API_PREFIX}/actions/runs/{RUN_ID}/attempts/{RUN_ATTEMPT}/jobs?per_page=100&page=1"
    ]
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
        recovery.verify_source(data.__getitem__, SHA, RUN_ID, RUN_ATTEMPT)


def test_single_latest_ready_revision_is_required() -> None:
    assert recovery.serving_revision(service()) == REVISION


@pytest.mark.parametrize(
    "case", ["split", "not-full", "not-latest", "invalid-name", "missing"]
)
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
    workflow = yaml.safe_load(
        (root / ".github/workflows/deploy-backend.yml").read_text()
    )
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"verification_run_id", "verification_run_attempt"}
    assert all(
        isinstance(value["default"], str) and not value["default"]
        for value in inputs.values()
    )
    jobs = workflow["jobs"]
    verification = jobs["verify-existing"]
    deployment_condition = (
        "github.ref == 'refs/heads/main' && (github.event_name == 'push' || "
        "(github.event_name == 'workflow_dispatch' && "
        "inputs.verification_run_id == '' && "
        "inputs.verification_run_attempt == ''))"
    )
    for job_name in (
        "ci-backend",
        "ci-frontend",
        "ci-quality",
        "ci-iac",
        "standards-receipt",
        "deploy",
    ):
        assert jobs[job_name]["if"] == deployment_condition
    assert verification["if"] == (
        "github.ref == 'refs/heads/main' && github.event_name == 'workflow_dispatch' && "
        "inputs.verification_run_id != '' && inputs.verification_run_attempt != ''"
    )
    assert "needs" not in verification
    rejection = jobs["reject-partial-verification-inputs"]
    assert rejection["if"] == (
        "github.ref == 'refs/heads/main' && github.event_name == 'workflow_dispatch' && "
        "((inputs.verification_run_id == '' && inputs.verification_run_attempt != '') || "
        "(inputs.verification_run_id != '' && inputs.verification_run_attempt == ''))"
    )
    assert rejection["permissions"] == {}
    assert rejection["steps"] == [
        {"name": "Require a complete verification source identity", "run": "exit 1"}
    ]
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
        'uv run --no-project --python 3.14 python -m scripts.backend_release.recovery \\\n  --sha "$GITHUB_SHA" source --run-id "$SOURCE_RUN_ID" \\\n  --run-attempt "$SOURCE_RUN_ATTEMPT"\n',
        "bash scripts/backend_release/verify-serving.sh",
        "scripts/github/verify-smart-create-queue.sh",
        'echo "started_at=$(date -u +\'%Y-%m-%dT%H:%M:%S.%NZ\')" >> "$GITHUB_OUTPUT"',
        "scripts/github/verify-backend-production-health.sh",
        "scripts/github/verify-backend-correlated-logs.sh",
        "bash scripts/backend_release/verify-serving.sh",
    ]
