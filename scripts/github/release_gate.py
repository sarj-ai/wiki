from __future__ import annotations  # ruff: ignore[implicit-namespace-package] -- standalone repository CLI, not an import package.

from collections.abc import Callable, Mapping
from datetime import timedelta
from enum import StrEnum
from fnmatch import fnmatchcase
import json
from pathlib import Path
import re
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- fixed read-only git/gh commands
import sys
import time
from typing import Annotated

import typer


type JsonValue = str | int | bool | list[JsonValue] | dict[str, JsonValue] | None
type JsonObject = dict[str, JsonValue]
type Fetch = Callable[[str], JsonObject]
REPOSITORY = "sarj-ai/wiki-internal"
REPOSITORY_ID = 1183627122
GITHUB_ACTIONS_APP_ID = 15368
MAX_RESPONSE_BYTES = 2_000_000
STANDARDS_ID = 333929494
STANDARDS_PATH = ".github/workflows/standards.yml"
API_PREFIX = f"repos/{REPOSITORY}"
MAIN_REF = f"{API_PREFIX}/git/ref/heads/main"
WORKFLOW = f"{API_PREFIX}/actions/workflows/{STANDARDS_ID}"
MAIN_RELEASE_PATH = ".github/workflows/release-main.yml"
MAIN_RELEASE_REF = f"{REPOSITORY}/{MAIN_RELEASE_PATH}@refs/heads/main"
MAIN_RELEASE_WORKFLOW = f"{API_PREFIX}/actions/workflows/release-main.yml"
BACKEND_RELEASE_PATHS = (
    "backend/.dockerignore",
    "backend/Dockerfile",
    "backend/alembic.ini",
    "backend/alembic/**",
    "backend/app/**",
    "backend/pyproject.toml",
    "backend/uv.lock",
    "backend/uv.toml",
    ".github/workflows/deploy-backend.yml",
    "scripts/backend_release/**",
    "scripts/github/deploy-backend-candidate.sh",
    "scripts/github/report-blocked-backend-release.sh",
    "scripts/github/run-backend-migrations.sh",
    "scripts/github/switch-backend-production-traffic.sh",
    "scripts/github/verify-backend-production-health.sh",
    "scripts/github/verify-backend-correlated-logs.sh",
    "scripts/github/verify-mcp-release.sh",
    "scripts/github/resolve-mcp-release-mode.sh",
    "scripts/github/verify-smart-create-queue.sh",
)
FRONTEND_RELEASE_PATHS = (
    "frontend/**",
    ".github/workflows/ci-frontend.yml",
    ".github/workflows/deploy-frontend.yml",
    "scripts/frontend_release/**",
)
SHARED_RELEASE_PATHS = (
    "scripts/wiki.py",
    MAIN_RELEASE_PATH,
    "scripts/github/release_gate.py",
    "scripts/github/verify-release-gate.sh",
)
REQUIRED_JOBS = frozenset(
    {"ci-backend", "ci-frontend", "ci-quality", "ci-iac", "standards-receipt"}
)
POLL_LIMIT = timedelta(minutes=20)
POLL_INTERVAL = timedelta(seconds=15)


class ReleaseBlockedError(RuntimeError):
    pass


def require(condition: object, reason: str) -> None:
    if not condition:
        raise ReleaseBlockedError(reason)


def require_ci_gates(environment: Mapping[str, str], component: str) -> None:
    require(component in {"backend", "frontend"}, "Unknown CI component")
    if environment.get("FORCE_FULL") == "true":
        require(environment.get("ROUTING_RESULT") == "skipped", "Unexpected CI routing")
        expected = "success"
    else:
        require(environment.get("ROUTING_RESULT") == "success", "CI routing failed")
        selected = environment.get("RUNTIME_SELECTED")
        require(selected in {"true", "false"}, "Missing runtime selection")
        expected = "success" if selected == "true" else "skipped"
    require(environment.get("LINT_RESULT") == "success", "CI lint failed")
    require(environment.get("TEST_RESULT") == expected, "Unexpected CI tests result")
    if component == "backend":
        require(
            environment.get("POSTGRES_RESULT") == expected,
            "Unexpected CI Postgres result",
        )
        require(environment.get("IMAGE_RESULT") == "success", "CI image failed")
    else:
        require(environment.get("BUILD_RESULT") == expected, "Unexpected CI build result")


def object_value(value: JsonValue) -> JsonObject:
    require(isinstance(value, dict), "Invalid GitHub receipt shape")
    if not isinstance(value, dict):
        msg = "Invalid GitHub receipt shape"
        raise ReleaseBlockedError(msg)
    return value


def validate_context(environment: Mapping[str, str], component: str, checkout_sha: str) -> str:
    sha = environment.get("GITHUB_SHA", "")
    require(component in {"backend", "frontend", "main"}, "Unknown release workflow")
    require(
        environment.get("GITHUB_REPOSITORY") == REPOSITORY,
        "Untrusted release repository",
    )
    require(
        environment.get("GITHUB_REPOSITORY_ID") == str(REPOSITORY_ID),
        "Untrusted repository identity",
    )
    require(
        environment.get("GITHUB_EVENT_NAME") in {"push", "workflow_dispatch"},
        "Untrusted release event",
    )
    require(environment.get("GITHUB_REF") == "refs/heads/main", "Only main may deploy")
    require(re.fullmatch(r"[0-9a-f]{40}", sha) is not None, "Invalid release SHA")
    require(checkout_sha == sha, "Checkout does not match release SHA")
    require(
        environment.get("GITHUB_WORKFLOW_SHA") == sha,
        "Workflow does not match release SHA",
    )
    expected_workflow = f"{REPOSITORY}/.github/workflows/deploy-{component}.yml@refs/heads/main"
    if component == "main" or environment.get("GITHUB_WORKFLOW_REF") == MAIN_RELEASE_REF:
        require(
            environment.get("GITHUB_EVENT_NAME") == "push",
            "Untrusted shared release event",
        )
        expected_workflow = MAIN_RELEASE_REF
    require(
        environment.get("GITHUB_WORKFLOW_REF") == expected_workflow,
        "Untrusted release workflow source",
    )
    return sha


def require_current_main(fetch: Fetch, sha: str) -> None:
    ref = fetch(MAIN_REF)
    require(ref.get("ref") == "refs/heads/main", "Invalid main ref receipt")
    target = object_value(ref.get("object"))
    require(
        target.get("type") == "commit" and target.get("sha") == sha,
        "Release is stale: main advanced",
    )


def validate_repository(value: JsonValue) -> None:
    repository = object_value(value)
    require(
        repository.get("id") == REPOSITORY_ID and repository.get("full_name") == REPOSITORY,
        "Untrusted receipt repository",
    )


def validate_run(run: JsonObject, sha: str) -> None:
    require(
        run.get("workflow_id") == STANDARDS_ID and run.get("path") == STANDARDS_PATH,
        "Untrusted Standards workflow",
    )
    require(
        run.get("event") == "push" and run.get("head_branch") == "main",
        "Untrusted Standards event or ref",
    )
    require(run.get("head_sha") == sha, "Standards checked a different SHA")
    validate_repository(run.get("repository"))
    validate_repository(run.get("head_repository"))


def positive_id(value: JsonValue) -> int:
    require(
        isinstance(value, int) and not isinstance(value, bool) and value > 0,
        "Invalid GitHub receipt ID",
    )
    if not isinstance(value, int):
        msg = "Invalid GitHub receipt ID"
        raise ReleaseBlockedError(msg)
    return value


def standards_receipt(fetch: Fetch, sha: str) -> bool:
    workflow = fetch(WORKFLOW)
    require(
        workflow.get("id") == STANDARDS_ID
        and workflow.get("path") == STANDARDS_PATH
        and workflow.get("state") == "active",
        "Standards workflow identity changed",
    )
    # Exact server-side filters and total_count prevent pagination from hiding ambiguity.
    page = fetch(f"{WORKFLOW}/runs?event=push&branch=main&head_sha={sha}&per_page=2&page=1")
    runs = page.get("workflow_runs")
    require(isinstance(runs, list), "Invalid Standards run listing")
    if not isinstance(runs, list):
        msg = "Invalid Standards run listing"
        raise ReleaseBlockedError(msg)
    count = page.get("total_count")
    require(
        type(count) is int and count == len(runs) and count in {0, 1},
        "Ambiguous Standards runs",
    )
    if not runs:
        return False
    listed = object_value(runs[0])
    validate_run(listed, sha)
    run_id = positive_id(listed.get("id"))
    run = fetch(f"{API_PREFIX}/actions/runs/{run_id}")
    validate_run(run, sha)
    require(run.get("id") == run_id, "Standards run identity changed")
    if run.get("status") in {
        "queued",
        "in_progress",
        "requested",
        "waiting",
        "pending",
    }:
        require(run.get("conclusion") is None, "Invalid pending Standards receipt")
        return False
    require(
        run.get("status") == "completed" and run.get("conclusion") == "success",
        "Standards did not succeed",
    )
    suite = fetch(f"{API_PREFIX}/check-suites/{positive_id(run.get('check_suite_id'))}")
    app = object_value(suite.get("app"))
    require(
        app.get("id") == GITHUB_ACTIONS_APP_ID and app.get("slug") == "github-actions",
        "Untrusted Standards check app",
    )
    validate_repository(suite.get("repository"))
    require(
        suite.get("head_sha") == sha and suite.get("head_branch") == "main",
        "Check suite does not match release",
    )
    require(
        suite.get("status") == "completed" and suite.get("conclusion") == "success",
        "Standards suite did not succeed",
    )
    return True


def wait_for_standards(
    fetch: Fetch,
    sha: str,
    *,
    now: Callable[[], float] = time.monotonic,
    pause: Callable[[float], None] = time.sleep,
) -> None:
    deadline = now() + POLL_LIMIT.total_seconds()
    while now() < deadline:
        require_current_main(fetch, sha)
        if standards_receipt(fetch, sha):
            require_current_main(fetch, sha)
            return
        pause(POLL_INTERVAL.total_seconds())
    msg = "Standards receipt missing or unfinished at deadline"
    raise ReleaseBlockedError(msg)


def validate_needs(needs: JsonObject, sha: str, component: str | None = None) -> None:
    require(needs.keys() == REQUIRED_JOBS, "Missing or unexpected required CI gates")
    for receipt in needs.values():
        require(
            object_value(receipt).get("result") == "success",
            "A required CI gate did not succeed",
        )
    standards = object_value(needs["standards-receipt"])
    require(
        object_value(standards.get("outputs")).get("release_sha") == sha,
        "Standards receipt SHA does not match release",
    )
    if component is not None:
        require(
            object_value(standards.get("outputs")).get(component) == "true",
            "Component was not selected for release",
        )


def selected_components(paths: list[str]) -> dict[str, bool]:
    return {
        component: any(
            fnmatchcase(path, pattern)
            for path in paths
            for pattern in patterns + SHARED_RELEASE_PATHS
        )
        for component, patterns in (
            ("backend", BACKEND_RELEASE_PATHS),
            ("frontend", FRONTEND_RELEASE_PATHS),
        )
    }


def release_baseline(fetch: Fetch) -> str:
    workflow = fetch(MAIN_RELEASE_WORKFLOW)
    require(
        workflow.get("path") == MAIN_RELEASE_PATH and workflow.get("state") == "active",
        "Invalid shared release workflow",
    )
    workflow_id = positive_id(workflow.get("id"))
    page = fetch(
        f"{MAIN_RELEASE_WORKFLOW}/runs?event=push&branch=main&status=success&per_page=1&page=1"
    )
    runs = page.get("workflow_runs")
    require(
        isinstance(runs, list) and len(runs) == 1,
        "Shared release baseline unavailable",
    )
    if not isinstance(runs, list):
        msg = "Shared release baseline unavailable"
        raise ReleaseBlockedError(msg)
    run = object_value(runs[0])
    validate_repository(run.get("repository"))
    validate_repository(run.get("head_repository"))
    baseline = run.get("head_sha")
    require(
        run.get("workflow_id") == workflow_id
        and run.get("path") == MAIN_RELEASE_PATH
        and run.get("event") == "push"
        and run.get("head_branch") == "main"
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and isinstance(baseline, str)
        and re.fullmatch(r"[0-9a-f]{40}", baseline),
        "Untrusted shared release baseline",
    )

    return str(baseline)


def release_components(fetch: Fetch, sha: str) -> dict[str, bool]:
    # A successful whole release proves every selected component completed.
    # Include every path touched since that SHA: an intervening failed release
    # may have switched traffic, so reverting its tree still needs deployment.
    try:
        baseline = release_baseline(fetch)
        command_output(["git", "merge-base", "--is-ancestor", str(baseline), sha])
        changed = command_output(
            [
                "git",
                "log",
                "--first-parent",
                "--diff-merges=first-parent",
                "--format=",
                "--no-ext-diff",
                "--no-textconv",
                "--no-renames",
                "--name-only",
                "-z",
                f"{baseline}..{sha}",
                "--",
            ]
        )
        return selected_components(changed.split("\0"))
    except ReleaseBlockedError:
        # Missing history, rebased main, or unavailable receipts select both;
        # they never exempt qualification or the fresh-main deployment fences.
        sys.stderr.write("::warning::No usable main release baseline; selecting both components\n")
        return {"backend": True, "frontend": True}


def read_json(raw: str) -> JsonObject:
    value: JsonValue = json.loads(raw)
    return object_value(value)


def command_output(command: list[str]) -> str:
    try:
        result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] -- fixed commands, no shell
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        msg = "Release command timed out; completion is unconfirmed"
        raise ReleaseBlockedError(msg) from exc
    require(result.returncode == 0, "Release command failed")
    require(
        len(result.stdout) <= MAX_RESPONSE_BYTES,
        "Oversized release verification response",
    )
    return result.stdout.strip()


def fetch_github(endpoint: str) -> JsonObject:
    require(endpoint.startswith(f"{API_PREFIX}/"), "Untrusted API endpoint")
    return read_json(
        command_output(
            [
                "gh",
                "api",
                "--hostname",
                "github.com",
                "--method",
                "GET",
                "-H",
                "X-GitHub-Api-Version: 2022-11-28",
                endpoint,
            ]
        )
    )


def execute(phase: str, component: str) -> str:
    raw = read_json(sys.stdin.read())
    require(all(isinstance(value, str) for value in raw.values()), "Invalid release input")
    environment = {key: str(value) for key, value in raw.items()}
    sha = validate_context(environment, component, command_output(["git", "rev-parse", "HEAD"]))
    match phase:
        case "components":
            require(component == "main", "Routing requires the shared main workflow")
            require_current_main(fetch_github, sha)
            selected = release_components(fetch_github, sha)
            require_current_main(fetch_github, sha)
            with Path(environment["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
                output.writelines(
                    f"{name}={str(enabled).lower()}\n" for name, enabled in selected.items()
                )
        case "standards":
            wait_for_standards(fetch_github, sha)
            with Path(environment["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
                output.write(f"release_sha={sha}\n")
        case _:
            shared = environment.get("GITHUB_WORKFLOW_REF") == MAIN_RELEASE_REF
            validate_needs(
                read_json(environment["RELEASE_NEEDS"]),
                sha,
                component if shared else None,
            )
            require_current_main(fetch_github, sha)
    return sha


class _Phase(StrEnum):
    CI = "ci"
    COMPONENTS = "components"
    STANDARDS = "standards"
    READY = "ready"


class _Component(StrEnum):
    MAIN = "main"
    BACKEND = "backend"
    FRONTEND = "frontend"


CLI = typer.Typer(
    add_completion=False,
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


@CLI.command()
def main(
    phase: Annotated[_Phase, typer.Argument()],
    component: Annotated[_Component, typer.Argument()],
    *,
    force_full: Annotated[str, typer.Option(envvar="FORCE_FULL")] = "",
    routing_result: Annotated[str, typer.Option(envvar="ROUTING_RESULT")] = "",
    runtime_selected: Annotated[str, typer.Option(envvar="RUNTIME_SELECTED")] = "",
    image_result: Annotated[str, typer.Option(envvar="IMAGE_RESULT")] = "",
    lint_result: Annotated[str, typer.Option(envvar="LINT_RESULT")] = "",
    postgres_result: Annotated[str, typer.Option(envvar="POSTGRES_RESULT")] = "",
    test_result: Annotated[str, typer.Option(envvar="TEST_RESULT")] = "",
    build_result: Annotated[str, typer.Option(envvar="BUILD_RESULT")] = "",
) -> None:
    """Fail closed before deploying an exact trusted main commit."""
    try:
        if phase == _Phase.CI:
            require_ci_gates(
                {
                    "FORCE_FULL": force_full,
                    "ROUTING_RESULT": routing_result,
                    "RUNTIME_SELECTED": runtime_selected,
                    "IMAGE_RESULT": image_result,
                    "LINT_RESULT": lint_result,
                    "POSTGRES_RESULT": postgres_result,
                    "TEST_RESULT": test_result,
                    "BUILD_RESULT": build_result,
                },
                component,
            )
            return
        sha = execute(phase, component)
    except ReleaseBlockedError as error:
        sys.stderr.write(f"Release blocked: {error}\n")
        raise typer.Exit(1) from None
    except OSError, subprocess.TimeoutExpired, ValueError, KeyError:
        # Never emit GitHub payloads, subprocess stderr, or environment credentials.
        sys.stderr.write("Release blocked: verification unavailable\n")
        raise typer.Exit(1) from None
    sys.stdout.write(f"Verified exact main release {sha}\n")


if __name__ == "__main__":
    CLI()
