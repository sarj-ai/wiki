"""Verify a serving release without replaying migration or traffic mutations."""

from __future__ import annotations

import argparse
import re
import sys
from typing import TYPE_CHECKING

from scripts.github.release_gate import (
    API_PREFIX,
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


if TYPE_CHECKING:
    from scripts.github.release_gate import Fetch, JsonObject

WORKFLOW_ID = 247052637
WORKFLOW_PATH = ".github/workflows/deploy-backend.yml"
REQUIRED_STEPS = (
    "Deploy candidate without traffic",
    "Run database migrations once",
    "Switch production traffic",
    "Clear legacy global drive-tree cache after traffic switch",
)
MAX_JOBS = 100
FULL_TRAFFIC = 100


def verify_source(fetch: Fetch, sha: str, run_id: int) -> None:
    require(run_id > 0, "Invalid source run")
    run = fetch(f"{API_PREFIX}/actions/runs/{run_id}")
    require(
        run.get("id") == run_id
        and run.get("workflow_id") == WORKFLOW_ID
        and run.get("path") == WORKFLOW_PATH
        and run.get("head_sha") == sha
        and run.get("head_branch") == "main"
        and run.get("event") in {"push", "workflow_dispatch"}
        and run.get("status") == "completed",
        "Untrusted or unfinished source release",
    )
    validate_repository(run.get("repository"))
    validate_repository(run.get("head_repository"))
    require(
        run.get("conclusion") in {"success", "failure", "cancelled", "timed_out"},
        "Invalid source result",
    )
    attempt = positive_id(run.get("run_attempt"))
    page = fetch(f"{API_PREFIX}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100&page=1")
    jobs = page.get("jobs")
    require(isinstance(jobs, list), "Invalid source jobs")
    if not isinstance(jobs, list):
        msg = "Invalid source jobs"
        raise ReleaseBlockedError(msg)
    require(
        type(page.get("total_count")) is int
        and page.get("total_count") == len(jobs)
        and len(jobs) <= MAX_JOBS,
        "Truncated job receipt",
    )
    deployments = [
        candidate_job
        for job in jobs
        if (candidate_job := object_value(job)).get("name") == "deploy"
    ]
    require(len(deployments) == 1, "Missing or ambiguous source deploy job")
    job = deployments[0]
    require(
        job.get("run_id") == run_id
        and job.get("run_attempt") == attempt
        and job.get("head_sha") == sha
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


def verify_serving(args: argparse.Namespace) -> None:
    for value in (args.project, args.region, args.repository, args.service, args.image):
        require(re.fullmatch(r"[a-z][a-z0-9-]{0,62}", value), "Invalid cloud resource name")
    image = f"{args.region}-docker.pkg.dev/{args.project}/{args.repository}/{args.image}"
    digest = command_output(
        [
            "gcloud",
            "artifacts",
            "docker",
            "images",
            "describe",
            f"{image}:{args.sha}",
            "--project",
            args.project,
            "--format=value(image_summary.digest)",
        ]
    )
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", digest), "Invalid immutable release digest")
    service = read_json(
        command_output(
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
    actual = command_output(
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
    require(actual == f"{image}@{digest}", "Serving image does not match the exact release digest")


def verify_current_release(args: argparse.Namespace) -> None:
    require(re.fullmatch(r"[0-9a-f]{40}", args.sha), "Invalid release SHA")
    require_current_main(fetch_github, args.sha)
    if args.phase == "source":
        verify_source(fetch_github, args.sha, args.run_id)
    else:
        require(args.phase == "serving", "Invalid recovery phase")
        verify_serving(args)
    require_current_main(fetch_github, args.sha)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    phases = parser.add_subparsers(dest="phase", required=True)
    source = phases.add_parser("source")
    source.add_argument("--run-id", type=int, required=True)
    serving = phases.add_parser("serving")
    for name in ("project", "region", "repository", "service", "image"):
        serving.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    try:
        verify_current_release(args)
    except ReleaseBlockedError, OSError, ValueError, TypeError:
        sys.stderr.write(
            "Read-only recovery blocked: release identity or completed mutations are unverified.\n"
        )
        return 1
    sys.stdout.write("Read-only recovery evidence verified.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
