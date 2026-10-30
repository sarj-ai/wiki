"""Verify a serving release without replaying migration or traffic mutations."""

from __future__ import annotations

from dataclasses import dataclass
import re
import sys
from typing import TYPE_CHECKING, Annotated, NewType

from scripts.github.release_gate import (
    API_PREFIX,
    MAIN_RELEASE_PATH,
    MAIN_RELEASE_WORKFLOW,
    ReleaseBlockedError,
    command_output,
    fetch_github,
    object_value,
    positive_id,
    read_json,
    require,
    require_current_main,
    validate_repository,
)
import typer


if TYPE_CHECKING:
    from collections.abc import Callable

    from scripts.github.release_gate import Fetch, JsonObject

WORKFLOW_ID = 247052637
WORKFLOW_PATH = ".github/workflows/deploy-backend.yml"
WorkflowId = NewType("WorkflowId", int)
RunId = NewType("RunId", int)
REQUIRED_STEPS = (
    "Deploy candidate without traffic",
    "Run database migrations once",
    "Switch production traffic",
)
REQUIRED_VERIFICATION_JOBS = (
    "standards-receipt",
    "ci-backend / Backend / required",
    "ci-frontend / Frontend / required",
    "ci-iac / IaC / required",
    "ci-quality / pre-commit (actionlint, hygiene, sarj rules)",
    "ci-quality / OpenAPI spec in sync",
    "ci-quality / gitleaks (full history)",
)
MAX_JOBS = 100
FULL_TRAFFIC = 100


def source_attempt_endpoint(run_id: RunId, run_attempt: int) -> str:
    return f"{API_PREFIX}/actions/runs/{run_id}/attempts/{run_attempt}"


def source_jobs_endpoint(run_id: RunId, run_attempt: int) -> str:
    return f"{source_attempt_endpoint(run_id, run_attempt)}/jobs?per_page=100&page=1"


def verify_source_run(
    run: JsonObject,
    run_id: RunId,
    run_attempt: int,
    shared_workflow_id: WorkflowId | None = None,
) -> str:
    source_sha = run.get("head_sha")
    require(
        run.get("id") == run_id
        and run.get("workflow_id")
        == (shared_workflow_id if shared_workflow_id is not None else WORKFLOW_ID)
        and run.get("path")
        == (MAIN_RELEASE_PATH if shared_workflow_id is not None else WORKFLOW_PATH)
        and isinstance(source_sha, str)
        and re.fullmatch(r"[0-9a-f]{40}", source_sha)
        and run.get("head_branch") == "main"
        and run.get("event") in {"push", "workflow_dispatch"}
        and run.get("status") == "completed"
        and type(run.get("run_attempt")) is int
        and run.get("run_attempt") == run_attempt,
        "Untrusted or unfinished source release",
    )
    if shared_workflow_id is not None:
        require(run.get("event") == "push", "Untrusted shared release event")
    validate_repository(run.get("repository"))
    validate_repository(run.get("head_repository"))
    require(
        run.get("conclusion") in {"success", "failure", "cancelled", "timed_out"},
        "Invalid source result",
    )
    return str(source_sha)


def verified_jobs(page: JsonObject) -> list[JsonObject]:
    raw_jobs = page.get("jobs")
    require(isinstance(raw_jobs, list), "Invalid source jobs")
    if not isinstance(raw_jobs, list):
        msg = "Invalid source jobs"
        raise ReleaseBlockedError(msg)
    jobs = [object_value(job) for job in raw_jobs]
    require(
        type(page.get("total_count")) is int
        and page.get("total_count") == len(jobs)
        and len(jobs) <= MAX_JOBS,
        "Truncated job receipt",
    )
    return jobs


def verify_prerequisite_jobs(
    jobs: list[JsonObject], source_sha: str, run_id: RunId, run_attempt: int
) -> None:
    for name in REQUIRED_VERIFICATION_JOBS:
        matches = [job for job in jobs if job.get("name") == name]
        require(len(matches) == 1, "Missing or ambiguous source verification receipt")
        verification_job = matches[0]
        require(
            verification_job.get("run_id") == run_id
            and type(verification_job.get("run_attempt")) is int
            and verification_job.get("run_attempt") == run_attempt
            and verification_job.get("head_sha") == source_sha
            and verification_job.get("status") == "completed"
            and verification_job.get("conclusion") == "success",
            "Source verification did not complete successfully",
        )


def verify_deploy_job(
    jobs: list[JsonObject],
    source_sha: str,
    run_id: RunId,
    run_attempt: int,
    *,
    shared: bool = False,
) -> None:
    name = "backend-deploy / deploy" if shared else "deploy"
    deployments = [job for job in jobs if job.get("name") == name]
    require(len(deployments) == 1, "Missing or ambiguous source deploy job")
    job = deployments[0]
    require(
        job.get("run_id") == run_id
        and type(job.get("run_attempt")) is int
        and job.get("run_attempt") == run_attempt
        and job.get("head_sha") == source_sha
        and job.get("status") == "completed",
        "Source deploy job identity changed",
    )
    steps = job.get("steps")
    require(isinstance(steps, list), "Missing source steps")
    if not isinstance(steps, list):
        msg = "Invalid source steps"
        raise ReleaseBlockedError(msg)
    previous_number = 0
    for name in REQUIRED_STEPS:
        matches = [
            candidate_step
            for step in steps
            if (candidate_step := object_value(step)).get("name") == name
        ]
        require(len(matches) == 1, "Missing or ambiguous release mutation receipt")
        step = matches[0]
        number = positive_id(step.get("number"))
        require(
            step.get("status") == "completed"
            and step.get("conclusion") == "success"
            and number > previous_number,
            "Release mutation did not complete successfully in order",
        )
        previous_number = number


def verify_source(fetch: Fetch, run_id: RunId, run_attempt: int) -> str:
    run_id = RunId(positive_id(run_id))
    run_attempt = positive_id(run_attempt)
    attempt_endpoint = source_attempt_endpoint(run_id, run_attempt)
    jobs_endpoint = source_jobs_endpoint(run_id, run_attempt)
    run = fetch(attempt_endpoint)
    shared_workflow_id = None
    if run.get("path") == MAIN_RELEASE_PATH:
        workflow = fetch(MAIN_RELEASE_WORKFLOW)
        require(
            workflow.get("path") == MAIN_RELEASE_PATH,
            "Untrusted shared release workflow",
        )
        shared_workflow_id = WorkflowId(positive_id(workflow.get("id")))
    source_sha = verify_source_run(run, run_id, run_attempt, shared_workflow_id)
    page = fetch(jobs_endpoint)
    jobs = verified_jobs(page)
    verify_prerequisite_jobs(jobs, source_sha, run_id, run_attempt)
    verify_deploy_job(jobs, source_sha, run_id, run_attempt, shared=shared_workflow_id is not None)
    # Attempt-specific metadata and jobs are immutable after completion. Re-read
    # both exact resources so a provider inconsistency cannot race authorization.
    require(fetch(attempt_endpoint) == run, "Source attempt metadata changed")
    require(fetch(jobs_endpoint) == page, "Source attempt jobs changed")
    return source_sha


def serving_revision(service: JsonObject) -> str:
    status = object_value(service.get("status"))
    traffic = status.get("traffic")
    require(isinstance(traffic, list) and len(traffic) == 1, "Ambiguous serving traffic")
    if not isinstance(traffic, list):
        msg = "Invalid serving traffic"
        raise ReleaseBlockedError(msg)
    allocation = object_value(traffic[0])
    revision = allocation.get("revisionName")
    require(
        allocation.get("percent") == FULL_TRAFFIC
        and isinstance(revision, str)
        and revision == status.get("latestReadyRevisionName"),
        "Serving revision is not the single latest ready revision",
    )
    require(re.fullmatch(r"[a-z][a-z0-9-]{0,62}", str(revision)), "Invalid revision name")
    return str(revision)


@dataclass(frozen=True, kw_only=True)
class ServingArguments:
    verifier_sha: str
    source_sha: str
    project: str
    region: str
    repository: str
    service: str
    image: str


@dataclass(frozen=True, kw_only=True)
class SourceArguments:
    verifier_sha: str
    run_id: RunId
    run_attempt: int


def verify_serving(
    args: ServingArguments, *, execute: Callable[[list[str]], str] = command_output
) -> None:
    for value in (args.project, args.region, args.repository, args.service, args.image):
        require(re.fullmatch(r"[a-z][a-z0-9-]{0,62}", value), "Invalid cloud resource name")
    image = f"{args.region}-docker.pkg.dev/{args.project}/{args.repository}/{args.image}"
    digest = execute(
        [
            "gcloud",
            "artifacts",
            "docker",
            "images",
            "describe",
            f"{image}:{args.source_sha}",
            "--project",
            args.project,
            "--format=value(image_summary.digest)",
        ]
    )
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", digest), "Invalid immutable release digest")
    service = read_json(
        execute(
            [
                "gcloud",
                "run",
                "services",
                "describe",
                args.service,
                "--project",
                args.project,
                "--region",
                args.region,
                "--format=json",
            ]
        )
    )
    revision = serving_revision(service)
    actual = execute(
        [
            "gcloud",
            "run",
            "revisions",
            "describe",
            revision,
            "--project",
            args.project,
            "--region",
            args.region,
            "--format=value(spec.containers[0].image)",
        ]
    )
    require(
        actual == f"{image}@{digest}",
        "Serving image does not match the exact release digest",
    )


def verify_current_release(
    args: SourceArguments | ServingArguments,
    *,
    fetch: Fetch = fetch_github,
    execute: Callable[[list[str]], str] = command_output,
) -> str | None:
    require(re.fullmatch(r"[0-9a-f]{40}", args.verifier_sha), "Invalid verifier SHA")
    require_current_main(fetch, args.verifier_sha)
    if isinstance(args, SourceArguments):
        source_sha = verify_source(fetch, args.run_id, args.run_attempt)
    else:
        require(re.fullmatch(r"[0-9a-f]{40}", args.source_sha), "Invalid source SHA")
        verify_serving(args, execute=execute)
        source_sha = None
    require_current_main(fetch, args.verifier_sha)
    return source_sha


def _report(args: SourceArguments | ServingArguments, *, github_output: bool = False) -> None:
    try:
        source_sha = verify_current_release(args)
    except ReleaseBlockedError, OSError, ValueError, TypeError:
        sys.stderr.write(
            "Read-only recovery blocked: release identity or completed mutations are unverified.\n"
        )
        raise typer.Exit(1) from None
    if source_sha is not None:
        prefix = "source_sha=" if github_output else ""
        sys.stdout.write(f"{prefix}{source_sha}\n")
    else:
        sys.stdout.write("Read-only recovery evidence verified.\n")


CLI = typer.Typer(
    add_completion=False,
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    help=__doc__,
)


@CLI.callback()
def configure(context: typer.Context, verifier_sha: Annotated[str, typer.Option()]) -> None:
    context.obj = verifier_sha


def _verifier_sha(context: typer.Context) -> str:
    if not isinstance(context.obj, str):
        message = "--verifier-sha is required"
        raise typer.BadParameter(message)
    return context.obj


def _parse_run_id(value: str) -> RunId:
    try:
        return RunId(int(value))
    except ValueError as exc:
        message = f"{value!r} is not a valid int."
        raise typer.BadParameter(message) from exc


@CLI.command("source")
def source_command(
    context: typer.Context,
    run_id: Annotated[RunId, typer.Option(parser=_parse_run_id, metavar="<int>")],
    run_attempt: Annotated[int, typer.Option()],
    *,
    github_output: Annotated[bool, typer.Option("--github-output")] = False,
) -> None:
    _report(
        SourceArguments(
            verifier_sha=_verifier_sha(context), run_id=run_id, run_attempt=run_attempt
        ),
        github_output=github_output,
    )


@CLI.command("serving")
def serving_command(
    context: typer.Context,
    *,
    source_sha: Annotated[str, typer.Option()],
    project: Annotated[str, typer.Option()],
    region: Annotated[str, typer.Option()],
    repository: Annotated[str, typer.Option()],
    service: Annotated[str, typer.Option()],
    image: Annotated[str, typer.Option()],
) -> None:
    _report(
        ServingArguments(
            verifier_sha=_verifier_sha(context),
            source_sha=source_sha,
            project=project,
            region=region,
            repository=repository,
            service=service,
            image=image,
        )
    )


if __name__ == "__main__":
    CLI()
