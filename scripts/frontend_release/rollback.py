"""Reconcile a failed frontend release without replacing a foreign version.

The two ownership reads are not an atomic compare-and-swap. Cloudflare writers
must still be serialized operationally; a changed or ambiguous receipt blocks.
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys
import time
from typing import TYPE_CHECKING, Annotated, NamedTuple
from uuid import UUID

from scripts.github.release_gate import (
    MAX_RESPONSE_BYTES,
    JsonValue,
    ReleaseBlockedError,
    command_output,
    object_value,
    require,
)
import typer


WORKER = "sarj-wiki"
WRANGLER = ("npm", "exec", "--prefix", "frontend", "--", "wrangler")
READBACK_ATTEMPTS = 6
FULL_TRAFFIC = 100

if TYPE_CHECKING:
    from collections.abc import Callable


class Deployment(NamedTuple):
    id: str
    version: str


def version_id(value: JsonValue) -> str:
    require(isinstance(value, str), "Missing version identity")
    parsed = str(UUID(str(value)))
    require(parsed == value, "Noncanonical version identity")
    return parsed


def active_deployment(raw: str) -> Deployment:
    deployments = json.loads(raw)
    require(isinstance(deployments, list) and deployments, "Missing deployments")
    dated = []
    for item in deployments:
        deployment = object_value(item)
        created = deployment.get("created_on")
        require(isinstance(created, str), "Missing deployment timestamp")
        timestamp = datetime.fromisoformat(str(created))
        require(timestamp.tzinfo is not None, "Ambiguous deployment timestamp")
        dated.append((timestamp, deployment))
    latest_time = max(timestamp for timestamp, _deployment in dated)
    latest = [deployment for timestamp, deployment in dated if timestamp == latest_time]
    require(len(latest) == 1, "Ambiguous latest deployment")
    deployment = latest[0]
    versions = deployment.get("versions")
    require(isinstance(versions, list) and len(versions) == 1, "Split or missing traffic")
    if not isinstance(versions, list):
        msg = "Invalid traffic receipt"
        raise ReleaseBlockedError(msg)
    version = object_value(versions[0])
    require(
        version.get("percentage") == FULL_TRAFFIC,
        "Expected exactly 100 percent traffic",
    )
    return Deployment(version_id(deployment.get("id")), version_id(version.get("version_id")))


def candidate_version(receipt: Path) -> str:
    with receipt.open(encoding="utf-8") as source:
        raw = source.read(MAX_RESPONSE_BYTES + 1)
    require(len(raw) <= MAX_RESPONSE_BYTES, "Oversized deployment receipt")
    entries = [object_value(json.loads(line)) for line in raw.splitlines() if line.strip()]
    deploys = [entry for entry in entries if entry.get("type") == "deploy"]
    require(len(deploys) == 1, "Missing or ambiguous candidate receipt")
    deploy = deploys[0]
    require(
        type(deploy.get("version")) is int
        and deploy.get("version") == 1
        and deploy.get("worker_name") == WORKER
        and deploy.get("worker_name_overridden") is False
        and deploy.get("wrangler_environment") in {None, ""},
        "Untrusted candidate target",
    )
    return version_id(deploy.get("version_id"))


def current_deployment() -> Deployment:
    return active_deployment(
        command_output([*WRANGLER, "deployments", "list", "--name", WORKER, "--json"])
    )


def reconcile(previous: str, receipt: Path, *, pause: Callable[[float], None] = time.sleep) -> None:
    previous = version_id(previous)
    current = current_deployment()
    if current.version == previous:
        return
    candidate = candidate_version(receipt)
    require(current.version == candidate, "Foreign active version; refusing rollback")
    require(current_deployment() == current, "Deployment changed; refusing rollback")
    try:
        command_output(
            [
                *WRANGLER,
                "rollback",
                previous,
                "--name",
                WORKER,
                "--message",
                "automatic ownership-verified rollback",
                "--yes",
            ]
        )
    except ReleaseBlockedError, OSError:
        # A command timeout is ambiguous: reconcile, never blindly retry the write.
        sys.stderr.write("Rollback command was inconclusive; checking restoration.\n")
    for attempt in range(READBACK_ATTEMPTS):
        active = current_deployment().version
        if active == previous:
            return
        require(active == candidate, "Foreign version appeared during rollback")
        if attempt + 1 < READBACK_ATTEMPTS:
            pause(5)
    msg = "Previous frontend version could not be verified"
    raise ReleaseBlockedError(msg)


CLI = typer.Typer(
    add_completion=False,
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    help=__doc__,
)


def _report(action: Callable[[], object]) -> None:
    try:
        action()
    except ReleaseBlockedError, OSError, ValueError, TypeError:
        sys.stderr.write(
            "Frontend rollback blocked: ownership or restoration could not be verified.\n"
        )
        raise typer.Exit(1) from None


@CLI.command("active")
def active_command(
    *,
    github_output: Annotated[bool, typer.Option("--github-output")] = False,
) -> None:
    prefix = "version_id=" if github_output else ""
    _report(lambda: sys.stdout.write(f"{prefix}{current_deployment().version}\n"))


@CLI.command("rollback")
def rollback_command(
    previous: Annotated[str, typer.Option()], receipt: Annotated[Path, typer.Option()]
) -> None:
    _report(lambda: reconcile(previous, receipt))
    sys.stdout.write("Previous frontend version is verified.\n")


if __name__ == "__main__":
    CLI()
