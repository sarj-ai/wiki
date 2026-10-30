from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- fixed repository verifier
from typing import TYPE_CHECKING

import pytest
from scripts.backend_release import recovery
import yaml


if TYPE_CHECKING:
    from scripts.github.release_gate import JsonObject

SOURCE_SHA = "3c5937f7a559fc23ec655dd845efbdbac300943d"
VERIFIER_SHA = "d" * 40
RUN_ID = recovery.RunId(34209018888)
RUN_ATTEMPT = 1
SOURCE_RUN_READ_COUNT = 2
ATTEMPT_JOB_COUNT = 19
SERVING_CHECK_COUNT = 2
REVISION = "sarj-docs-backend-dammam-00001-test"
DIGEST = "sha256:" + "b" * 64


@dataclass(frozen=True)
class _VerifierRun:
    completed: subprocess.CompletedProcess[str]
    trace: list[str]


@dataclass(frozen=True)
class _ModeResolverRun:
    returncode: int
    stderr: str
    output: str


def receipts() -> dict:
    repository = {"id": 1183627122, "full_name": "sarj-ai/wiki-internal"}
    run = {
        "id": RUN_ID,
        "workflow_id": recovery.WORKFLOW_ID,
        "path": recovery.WORKFLOW_PATH,
        "head_sha": SOURCE_SHA,
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
        "head_sha": SOURCE_SHA,
        "status": "completed",
        "steps": [
            {
                "name": name,
                "number": index,
                "status": "completed",
                "conclusion": "success",
            }
            for index, name in enumerate(recovery.REQUIRED_STEPS, start=1)
        ]
        + [
            {
                "name": "Clear legacy global drive-tree cache after traffic switch",
                "number": len(recovery.REQUIRED_STEPS) + 1,
                "status": "completed",
                "conclusion": "failure",
            }
        ],
    }
    verification_jobs = [
        {
            "name": name,
            "run_id": RUN_ID,
            "run_attempt": RUN_ATTEMPT,
            "head_sha": SOURCE_SHA,
            "status": "completed",
            "conclusion": "success",
            "steps": [],
        }
        for name in recovery.REQUIRED_VERIFICATION_JOBS
    ]
    unrelated_jobs = [
        {
            "name": f"completed-unrelated-{index}",
            "run_id": RUN_ID,
            "run_attempt": RUN_ATTEMPT,
            "head_sha": SOURCE_SHA,
            "status": "completed",
            "conclusion": "skipped",
            "steps": [],
        }
        for index in range(ATTEMPT_JOB_COUNT - 1 - len(recovery.REQUIRED_VERIFICATION_JOBS))
    ]
    jobs = [job, *verification_jobs, *unrelated_jobs]
    return {
        recovery.source_attempt_endpoint(RUN_ID, RUN_ATTEMPT): run,
        recovery.source_jobs_endpoint(RUN_ID, RUN_ATTEMPT): {
            "total_count": len(jobs),
            "jobs": jobs,
        },
    }


def service() -> dict:
    return {
        "status": {
            "latestReadyRevisionName": REVISION,
            "traffic": [{"revisionName": REVISION, "percent": 100}],
        }
    }


def test_historical_attempt_34209018888_authorizes_read_only_verification() -> None:
    data = receipts()
    assert (
        data[recovery.source_jobs_endpoint(RUN_ID, RUN_ATTEMPT)]["total_count"] == ATTEMPT_JOB_COUNT
    )
    assert recovery.verify_source(data.__getitem__, RUN_ID, RUN_ATTEMPT) == SOURCE_SHA


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "workflow-disabled",
        "workflow-id",
        "workflow-path",
        "deploy-name",
        "missing-ci",
        "failed-migration",
    ],
)
def test_shared_main_release_attempt_preserves_recovery_receipt_boundaries(
    fault: str | None,
) -> None:
    data = receipts()
    run = data[recovery.source_attempt_endpoint(RUN_ID, RUN_ATTEMPT)]
    run.update(workflow_id=303, path=recovery.MAIN_RELEASE_PATH)
    data[recovery.MAIN_RELEASE_WORKFLOW] = {
        "id": 303,
        "path": recovery.MAIN_RELEASE_PATH,
        "state": "active",
    }
    page = data[recovery.source_jobs_endpoint(RUN_ID, RUN_ATTEMPT)]
    page["jobs"][0]["name"] = "backend-deploy / deploy"
    if fault == "workflow-disabled":
        data[recovery.MAIN_RELEASE_WORKFLOW]["state"] = "disabled_manually"
    elif fault == "workflow-id":
        run["workflow_id"] = 999
    elif fault == "workflow-path":
        data[recovery.MAIN_RELEASE_WORKFLOW]["path"] = "other.yml"
    elif fault == "deploy-name":
        page["jobs"][0]["name"] = "frontend-deploy / deploy"
    elif fault == "missing-ci":
        page["jobs"][1]["name"] = "untrusted ci"
    elif fault == "failed-migration":
        page["jobs"][0]["steps"][1]["conclusion"] = "failure"
    if fault in {None, "workflow-disabled"}:
        assert recovery.verify_source(data.__getitem__, RUN_ID, RUN_ATTEMPT) == SOURCE_SHA
    else:
        with pytest.raises(recovery.ReleaseBlockedError):
            recovery.verify_source(data.__getitem__, RUN_ID, RUN_ATTEMPT)


def test_obsolete_cleanup_failure_does_not_block_read_only_verification() -> None:
    data = receipts()
    page = data[recovery.source_jobs_endpoint(RUN_ID, RUN_ATTEMPT)]
    cleanup = page["jobs"][0]["steps"][-1]
    assert cleanup["name"] == "Clear legacy global drive-tree cache after traffic switch"
    assert cleanup["conclusion"] == "failure"

    assert recovery.verify_source(data.__getitem__, RUN_ID, RUN_ATTEMPT) == SOURCE_SHA


def test_attempt_jobs_are_rechecked_after_receipt_validation() -> None:
    data = receipts()
    jobs_endpoint = recovery.source_jobs_endpoint(RUN_ID, RUN_ATTEMPT)
    jobs_reads = 0

    def fetch(endpoint: str) -> dict:
        nonlocal jobs_reads
        result = deepcopy(data[endpoint])
        if endpoint == jobs_endpoint:
            jobs_reads += 1
            if jobs_reads == SOURCE_RUN_READ_COUNT:
                result["jobs"][0]["status"] = "in_progress"
        return result

    with pytest.raises(recovery.ReleaseBlockedError, match="jobs changed"):
        recovery.verify_source(fetch, RUN_ID, RUN_ATTEMPT)
    assert jobs_reads == SOURCE_RUN_READ_COUNT


@pytest.mark.parametrize("advance", [False, True])
def test_current_main_is_rechecked_after_receipt_reads(*, advance: bool) -> None:
    data = receipts()
    reads = []

    def fetch(endpoint: str) -> dict:
        if endpoint.endswith("/git/ref/heads/main"):
            reads.append(endpoint)
            current = VERIFIER_SHA if not advance or len(reads) == 1 else "b" * 40
            return {
                "ref": "refs/heads/main",
                "object": {"type": "commit", "sha": current},
            }
        return data[endpoint]

    args = recovery.SourceArguments(
        verifier_sha=VERIFIER_SHA,
        run_id=RUN_ID,
        run_attempt=RUN_ATTEMPT,
    )
    if advance:
        with pytest.raises(recovery.ReleaseBlockedError, match="main advanced"):
            recovery.verify_current_release(args, fetch=fetch)
    else:
        recovery.verify_current_release(args, fetch=fetch)
    first_read, final_read = reads
    assert first_read == final_read


def test_current_verifier_must_match_main() -> None:
    def fetch(endpoint: str) -> dict:
        assert endpoint.endswith("/git/ref/heads/main")
        return {
            "ref": "refs/heads/main",
            "object": {"type": "commit", "sha": "b" * 40},
        }

    args = recovery.SourceArguments(
        verifier_sha=VERIFIER_SHA,
        run_id=RUN_ID,
        run_attempt=RUN_ATTEMPT,
    )
    with pytest.raises(recovery.ReleaseBlockedError, match="main advanced"):
        recovery.verify_current_release(args, fetch=fetch)


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
    data[recovery.source_attempt_endpoint(RUN_ID, RUN_ATTEMPT)][field] = value
    with pytest.raises(recovery.ReleaseBlockedError):
        recovery.verify_source(data.__getitem__, RUN_ID, RUN_ATTEMPT)


@pytest.mark.parametrize("run_attempt", [0, -1, True])
def test_invalid_requested_attempt_is_rejected(run_attempt: int) -> None:
    with pytest.raises(recovery.ReleaseBlockedError):
        recovery.verify_source(receipts().__getitem__, RUN_ID, run_attempt)


def test_mutable_latest_attempt_does_not_invalidate_requested_attempt() -> None:
    data = receipts()
    latest_endpoint = f"{recovery.API_PREFIX}/actions/runs/{RUN_ID}"
    data[latest_endpoint] = {
        **data[recovery.source_attempt_endpoint(RUN_ID, RUN_ATTEMPT)],
        "run_attempt": RUN_ATTEMPT + 1,
    }
    reads: list[str] = []

    def fetch(endpoint: str) -> dict:
        reads.append(endpoint)
        return deepcopy(data[endpoint])

    assert recovery.verify_source(fetch, RUN_ID, RUN_ATTEMPT) == SOURCE_SHA
    assert latest_endpoint not in reads


def test_attempt_is_rechecked_after_job_receipts() -> None:
    data = receipts()
    run_endpoint = recovery.source_attempt_endpoint(RUN_ID, RUN_ATTEMPT)
    run_reads = 0

    def fetch(endpoint: str) -> dict:
        nonlocal run_reads
        result = deepcopy(data[endpoint])
        if endpoint == run_endpoint:
            run_reads += 1
            if run_reads == SOURCE_RUN_READ_COUNT:
                result["run_attempt"] = RUN_ATTEMPT + 1
        return result

    with pytest.raises(recovery.ReleaseBlockedError, match="metadata changed"):
        recovery.verify_source(fetch, RUN_ID, RUN_ATTEMPT)
    assert run_reads == SOURCE_RUN_READ_COUNT


@pytest.mark.parametrize("conclusion", ["failure", "skipped", "cancelled", None])
@pytest.mark.parametrize("step_name", recovery.REQUIRED_STEPS)
def test_incomplete_mutation_never_authorizes_recovery(
    step_name: str, conclusion: str | None
) -> None:
    data = receipts()
    page = data[recovery.source_jobs_endpoint(RUN_ID, RUN_ATTEMPT)]
    for step in page["jobs"][0]["steps"]:
        if step["name"] == step_name:
            step["conclusion"] = conclusion
    with pytest.raises(recovery.ReleaseBlockedError):
        recovery.verify_source(data.__getitem__, RUN_ID, RUN_ATTEMPT)


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
    page = data[recovery.source_jobs_endpoint(RUN_ID, RUN_ATTEMPT)]
    job = next(candidate for candidate in page["jobs"] if candidate["name"] == job_name)
    job[field] = value
    with pytest.raises(recovery.ReleaseBlockedError):
        recovery.verify_source(data.__getitem__, RUN_ID, RUN_ATTEMPT)


@pytest.mark.parametrize("case", ["missing", "duplicate"])
def test_ambiguous_source_verification_job_blocks_recovery(case: str) -> None:
    data = receipts()
    page = data[recovery.source_jobs_endpoint(RUN_ID, RUN_ATTEMPT)]
    name = recovery.REQUIRED_VERIFICATION_JOBS[0]
    matches = [job for job in page["jobs"] if job["name"] == name]
    assert len(matches) == 1
    if case == "missing":
        page["jobs"].remove(matches[0])
    else:
        page["jobs"].append(deepcopy(matches[0]))
    page["total_count"] = len(page["jobs"])
    with pytest.raises(recovery.ReleaseBlockedError):
        recovery.verify_source(data.__getitem__, RUN_ID, RUN_ATTEMPT)


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
    page = data[recovery.source_jobs_endpoint(RUN_ID, RUN_ATTEMPT)]
    job = page["jobs"][0]
    match case:
        case "missing-job":
            job["name"] = "verify-existing"
        case "duplicate-job":
            page["jobs"].append(deepcopy(job))
            page["total_count"] = len(page["jobs"])
        case "truncated":
            page["total_count"] += 1
        case "wrong-attempt":
            job["run_attempt"] += 1
        case "wrong-sha":
            job["head_sha"] = "b" * 40
        case "wrong-order":
            job["steps"][len(recovery.REQUIRED_STEPS) - 1]["number"] = 1
        case _:
            job["steps"].append(deepcopy(job["steps"][0]))
    with pytest.raises(recovery.ReleaseBlockedError):
        recovery.verify_source(data.__getitem__, RUN_ID, RUN_ATTEMPT)


def test_single_latest_ready_revision_is_required() -> None:
    assert recovery.serving_revision(service()) == REVISION


@pytest.mark.parametrize("case", ["split", "not-full", "not-latest", "invalid-name", "missing"])
def test_unverified_serving_revision_is_rejected(case: str) -> None:
    data = service()
    status = data["status"]
    match case:
        case "split":
            status["traffic"].append({"revisionName": "foreign", "percent": 0})
        case "not-full":
            status["traffic"][0]["percent"] = 50
        case "not-latest":
            status["latestReadyRevisionName"] = "foreign"
        case "invalid-name":
            status["latestReadyRevisionName"] = "--help"
            status["traffic"][0]["revisionName"] = "--help"
        case _:
            status["traffic"] = []
    with pytest.raises(recovery.ReleaseBlockedError):
        recovery.serving_revision(data)


@pytest.mark.parametrize("matching", [True, False])
def test_serving_digest_is_checked_with_only_describe_commands(*, matching: bool) -> None:
    image = "me-central2-docker.pkg.dev/sarj-docs/sarj-docs-dammam/backend"
    responses = iter(
        [DIGEST, json.dumps(service()), f"{image}@{DIGEST}" if matching else "foreign"]
    )
    commands = []

    def execute(command: list[str]) -> str:
        commands.append(command)
        return next(responses)

    args = recovery.ServingArguments(
        verifier_sha=VERIFIER_SHA,
        source_sha=SOURCE_SHA,
        region="me-central2",
        project="sarj-docs",
        repository="sarj-docs-dammam",
        service="sarj-docs-backend-dammam",
        image="backend",
    )
    if matching:
        recovery.verify_serving(args, execute=execute)
    else:
        with pytest.raises(recovery.ReleaseBlockedError):
            recovery.verify_serving(args, execute=execute)
    artifact, serving, revision = commands
    assert artifact[:5] == ["gcloud", "artifacts", "docker", "images", "describe"]
    assert artifact[5].endswith(f":{SOURCE_SHA}")
    assert serving[:4] == ["gcloud", "run", "services", "describe"]
    assert revision[:4] == ["gcloud", "run", "revisions", "describe"]
    assert revision[4] == REVISION


def test_recovery_job_is_exclusive_and_contains_no_deployment_mutations() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/deploy-backend.yml").read_text())
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"verification_run_id", "verification_run_attempt"}
    assert all(
        isinstance(value["default"], str) and not value["default"] for value in inputs.values()
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
    ):
        assert jobs[job_name]["if"] == f"inputs.release_needs == '' && {deployment_condition}"
    assert deployment_condition in jobs["deploy"]["if"]
    assert "inputs.release_needs != ''" in jobs["deploy"]["if"]
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
        (
            "uv run --no-project --with typer==0.27.3 --python 3.14 "
            'python -m scripts.backend_release.recovery --verifier-sha "$GITHUB_SHA" '
            'source --run-id "$SOURCE_RUN_ID" --run-attempt "$SOURCE_RUN_ATTEMPT" '
            '--github-output >>"$GITHUB_OUTPUT"'
        ),
        "bash scripts/backend_release/verify-serving.sh",
        "scripts/github/verify-smart-create-queue.sh",
        "date -u +'started_at=%Y-%m-%dT%H:%M:%S.%NZ' >>\"$GITHUB_OUTPUT\"",
        "scripts/github/verify-backend-production-health.sh",
        "scripts/github/resolve-mcp-release-mode.sh",
        "scripts/github/verify-mcp-release.sh",
        "scripts/github/verify-backend-correlated-logs.sh",
        "bash scripts/backend_release/verify-serving.sh",
    ]
    for job_name, revision_start in (
        ("deploy", "${{ steps.deploy.outputs.started_at }}"),
        ("verify-existing", "${{ steps.window.outputs.started_at }}"),
    ):
        resolver_step = next(
            step
            for step in jobs[job_name]["steps"]
            if step.get("run") == "scripts/github/resolve-mcp-release-mode.sh"
        )
        assert resolver_step["id"] == "mcp-mode"
        assert resolver_step["env"] == {
            "MCP_VERIFIED_REVISION": "${{ steps.verify.outputs.new_revision }}"
        }
        mcp_step = next(
            step
            for step in jobs[job_name]["steps"]
            if step.get("run") == "scripts/github/verify-mcp-release.sh"
        )
        assert mcp_step["env"] == {
            "MCP_VERIFY_MODE": "${{ steps.mcp-mode.outputs.mode }}",
            "MCP_VERIFIED_REVISION": "${{ steps.verify.outputs.new_revision }}",
            "MCP_LOG_WINDOW_START": revision_start,
        }


def test_recovery_workflow_binds_historical_source_sha_to_live_checks() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/deploy-backend.yml").read_text())
    steps = workflow["jobs"]["verify-existing"]["steps"]
    source_step = next(step for step in steps if step.get("id") == "source")
    assert source_step["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "SOURCE_RUN_ATTEMPT": "${{ inputs.verification_run_attempt }}",
        "SOURCE_RUN_ID": "${{ inputs.verification_run_id }}",
    }
    source_output = "${{ steps.source.outputs.source_sha }}"
    serving_steps = [
        step
        for step in steps
        if step.get("run") == "bash scripts/backend_release/verify-serving.sh"
    ]
    assert len(serving_steps) == SERVING_CHECK_COUNT
    assert all(step["env"]["SOURCE_RELEASE_SHA"] == source_output for step in serving_steps)
    health_step = next(
        step
        for step in steps
        if step.get("run") == "scripts/github/verify-backend-production-health.sh"
    )
    assert health_step["env"] == {"EXPECTED_RELEASE_SHA": source_output}
    log_step = next(
        step
        for step in steps
        if step.get("run") == "scripts/github/verify-backend-correlated-logs.sh"
    )
    assert log_step["env"]["EXPECTED_RELEASE_SHA"] == source_output


def _run_mcp_verifier(tmp_path: Path, *, mode: str) -> _VerifierRun:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    trace = tmp_path / "trace"
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
method=GET
output=/dev/null
headers=/dev/null
hostile=false
bearer=false
url=
while (($#)); do
  case "$1" in
    --request) method=$2; shift 2 ;;
    --output) output=$2; shift 2 ;;
    --dump-header) headers=$2; shift 2 ;;
    --header)
      [[ $2 == Host:* || $2 == Origin:* ]] && hostile=true
      [[ $2 == Authorization:\\ Bearer* ]] && bearer=true
      shift 2
      ;;
    --connect-timeout|--max-time|--write-out|--data) shift 2 ;;
    --silent|--show-error) shift ;;
    *) url=$1; shift ;;
  esac
done
printf 'curl:%s:%s:hostile=%s:bearer=%s\\n' "$method" "$url" "$hostile" "$bearer" >> "$FAKE_TRACE"
if [[ $FAKE_MCP_MODE == disabled ]]; then
  : > "$output"
  : > "$headers"
  printf 404
elif [[ $url == */.well-known/oauth-authorization-server ]]; then
  printf '%s' '{"issuer":"https://api.example.test/","authorization_endpoint":"https://api.example.test/authorize","token_endpoint":"https://api.example.test/token","registration_endpoint":"https://api.example.test/register","revocation_endpoint":"https://api.example.test/revoke","authorization_response_iss_parameter_supported":true,"client_id_metadata_document_supported":false,"code_challenge_methods_supported":["S256"]}' > "$output"
  printf 200
elif [[ $url == */.well-known/oauth-protected-resource/mcp ]]; then
  printf '%s' '{"resource":"https://api.example.test/mcp","authorization_servers":["https://api.example.test/"]}' > "$output"
  printf 200
elif [[ $hostile == true ]]; then
  printf 403
else
  if [[ $bearer == false ]]; then
    printf '%s\\r\\n' 'WWW-Authenticate: Bearer resource_metadata="https://api.example.test/.well-known/oauth-protected-resource/mcp"' > "$headers"
  fi
  printf 401
fi
"""
    )
    fake_gcloud = fake_bin / "gcloud"
    fake_gcloud.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf 'gcloud:%s\\n' "$*" >> "$FAKE_TRACE"
if [[ $1 == run && $2 == revisions && $3 == describe ]]; then
  printf '{"metadata":{"name":"%s","labels":{"serving.knative.dev/service":"%s"}},"status":{"conditions":[{"type":"Ready","status":"True"}]}}\\n' "$MCP_VERIFIED_REVISION" "$SERVICE"
else
  printf '%s\\n' '[{"safe":true}]'
fi
"""
    )
    fake_date = fake_bin / "date"
    fake_date.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' date >> "$FAKE_TRACE"
printf '%s\\n' '2026-09-07T16:00:01.000000000Z'
"""
    )
    fake_sleep = fake_bin / "sleep"
    fake_sleep.write_text("#!/usr/bin/env bash\nexit 0\n")
    for executable in (fake_curl, fake_gcloud, fake_date, fake_sleep):
        executable.chmod(0o755)

    environment = {
        **os.environ,  # ruff: ignore[banned-api] -- controlled test subprocess environment
        "PATH": f"{fake_bin}:{os.environ['PATH']}",  # ruff: ignore[banned-api] -- isolated fakes first
        "FAKE_TRACE": str(trace),
        "FAKE_MCP_MODE": mode,
        "MCP_VERIFY_MODE": mode,
        "MCP_BASE_URL": "https://api.example.test",
        "PROJECT_ID": "wiki-test",
        "REGION": "us-central1",
        "SERVICE": "wiki-backend",
        "MCP_VERIFIED_REVISION": "wiki-backend-00001-test",
        "MCP_LOG_WINDOW_START": "2026-09-07T16:00:00Z",
    }
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] -- fixed repository verifier
        [str(Path(__file__).parent / "verify-mcp-release.sh")],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    return _VerifierRun(completed, trace.read_text().splitlines())


def test_mcp_release_verifier_uses_exact_revision_and_post_probe_log_bound(
    tmp_path: Path,
) -> None:
    result = _run_mcp_verifier(tmp_path, mode="enabled")
    assert result.completed.returncode == 0, result.completed.stderr
    revision = next(
        index for index, event in enumerate(result.trace) if "revisions describe" in event
    )
    bearer = next(index for index, event in enumerate(result.trace) if "bearer=true" in event)
    bounded = result.trace.index("date")
    logs = next(index for index, event in enumerate(result.trace) if "logging read" in event)
    assert "wiki-backend-00001-test" in result.trace[revision]
    assert revision < bearer < bounded < logs


def test_mcp_disabled_inventory_covers_browser_route_shapes(tmp_path: Path) -> None:
    result = _run_mcp_verifier(tmp_path, mode="disabled")
    assert result.completed.returncode == 0, result.completed.stderr
    observed = {
        event.split(":", 3)[1]
        + " "
        + event.split("https://api.example.test", 1)[1].split(":", 1)[0]
        for event in result.trace
    }
    assert {
        "GET /integrations/mcp/consent/synthetic-intent",
        "GET /integrations/mcp/consent/synthetic-intent/status",
        "POST /integrations/mcp/consent/synthetic-intent",
        "DELETE /integrations/mcp/connections/00000000-0000-0000-0000-000000000000",
    }.issubset(observed)


def _mcp_revision(*, enabled: bool) -> dict:
    env: list[JsonObject] = [{"name": "MCP_NATIVE_ENABLED", "value": str(enabled).lower()}]
    if enabled:
        env.extend(
            [
                {"name": "MCP_NATIVE_ISSUER", "value": "https://api-wiki.sarj.ai"},
                {"name": "MCP_NATIVE_LOOPBACK_PATHS", "value": '["/callback"]'},
                {"name": "MCP_NATIVE_HTTPS_CALLBACKS", "value": "[]"},
                {
                    "name": "MCP_NATIVE_FERNET_KEYS",
                    "valueFrom": {"secretKeyRef": {"name": "mcp-fernet", "key": "1"}},
                },
                {
                    "name": "MCP_NATIVE_LOCK_KEY",
                    "valueFrom": {"secretKeyRef": {"name": "mcp-lock", "key": "2"}},
                },
            ]
        )
    return {
        "metadata": {
            "name": REVISION,
            "labels": {"serving.knative.dev/service": "wiki-backend"},
        },
        "spec": {"containers": [{"env": env}]},
        "status": {"conditions": [{"type": "Ready", "status": "True"}]},
    }


def _run_mcp_mode_resolver(tmp_path: Path, revision: dict) -> _ModeResolverRun:
    fake_bin = tmp_path / "resolver-bin"
    fake_bin.mkdir(parents=True)
    fake_gcloud = fake_bin / "gcloud"
    fake_gcloud.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\nprintf '%s' \"$FAKE_REVISION_JSON\"\n"
    )
    fake_gcloud.chmod(0o755)
    output = tmp_path / "github-output"
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] -- fixed repository resolver
        [str(root / "scripts/github/resolve-mcp-release-mode.sh")],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,  # ruff: ignore[banned-api] -- controlled test subprocess environment
            "PATH": f"{fake_bin}:{os.environ['PATH']}",  # ruff: ignore[banned-api] -- isolated fake first
            "FAKE_REVISION_JSON": json.dumps(revision),
            "GITHUB_OUTPUT": str(output),
            "PROJECT_ID": "wiki-test",
            "REGION": "us-central1",
            "SERVICE": "wiki-backend",
            "MCP_VERIFIED_REVISION": REVISION,
        },
    )
    return _ModeResolverRun(
        returncode=completed.returncode,
        stderr=completed.stderr,
        output=output.read_text() if output.exists() else "",
    )


@pytest.mark.parametrize("enabled", [False, True])
def test_mcp_mode_resolver_accepts_exact_revision_contract(
    tmp_path: Path, *, enabled: bool
) -> None:
    result = _run_mcp_mode_resolver(tmp_path, _mcp_revision(enabled=enabled))
    assert result.returncode == 0, result.stderr
    expected_mode = "enabled" if enabled else "disabled"
    assert result.output == f"mode={expected_mode}\n"


def test_mcp_mode_resolver_rejects_missing_or_duplicate_flag(tmp_path: Path) -> None:
    missing = _mcp_revision(enabled=False)
    missing["spec"]["containers"][0]["env"] = []
    result = _run_mcp_mode_resolver(tmp_path / "missing", missing)
    assert result.returncode != 0

    duplicate = _mcp_revision(enabled=False)
    duplicate["spec"]["containers"][0]["env"].append(
        {"name": "MCP_NATIVE_ENABLED", "value": "true"}
    )
    result = _run_mcp_mode_resolver(tmp_path / "duplicate", duplicate)
    assert result.returncode != 0


def test_mcp_mode_resolver_rejects_partial_enabled_key_contract(tmp_path: Path) -> None:
    revision = _mcp_revision(enabled=True)
    revision["spec"]["containers"][0]["env"] = [
        setting
        for setting in revision["spec"]["containers"][0]["env"]
        if setting["name"] != "MCP_NATIVE_LOCK_KEY"
    ]
    result = _run_mcp_mode_resolver(tmp_path, revision)
    assert result.returncode != 0


def test_mcp_mode_resolver_rejects_wrong_service_or_unready_revision(
    tmp_path: Path,
) -> None:
    wrong_service = _mcp_revision(enabled=False)
    wrong_service["metadata"]["labels"]["serving.knative.dev/service"] = "other"
    result = _run_mcp_mode_resolver(tmp_path / "service", wrong_service)
    assert result.returncode != 0

    unready = _mcp_revision(enabled=False)
    unready["status"]["conditions"][0]["status"] = "False"
    result = _run_mcp_mode_resolver(tmp_path / "ready", unready)
    assert result.returncode != 0
