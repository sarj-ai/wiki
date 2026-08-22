# ruff: file-ignore[implicit-namespace-package] -- standalone repository CLI, not a package
"""Check, apply, or remove GitHub branch protection from the canonical JSON policy."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- fixed gh CLI, no shell
import sys
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Sequence


DEFAULT_POLICY = Path(__file__).with_name("branch-protection.json")
DEFAULT_REPOSITORY = "sarj-ai/wiki"
DEFAULT_BRANCH = "main"
BOOLEAN_SETTINGS = (
    "required_linear_history",
    "allow_force_pushes",
    "allow_deletions",
    "block_creations",
    "required_conversation_resolution",
    "lock_branch",
    "allow_fork_syncing",
)

type JsonValue = (
    bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
)
type JsonObject = dict[str, JsonValue]


class MissingProtectionError(RuntimeError):
    """The branch does not have protection configured."""


@dataclass(frozen=True, slots=True)
class Endpoints:
    protection: str
    signatures: str


def _api(
    endpoint: str, *, method: str = "GET", payload: JsonObject | None = None
) -> JsonObject | None:
    command = ["gh", "api", endpoint, "--method", method]
    if payload is not None:
        command.extend(["--input", "-"])
    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] -- fixed gh CLI
        command,
        check=False,
        capture_output=True,
        input=None if payload is None else json.dumps(payload),
        text=True,
    )
    if result.returncode:
        missing_markers = ("Branch not protected", "Not Found", "HTTP 404")
        if result.returncode == 1 and any(
            marker in result.stderr for marker in missing_markers
        ):
            raise MissingProtectionError(endpoint)
        message = result.stderr.strip() or f"gh api exited {result.returncode}"
        raise RuntimeError(message)
    if not result.stdout.strip():
        return None
    decoded: JsonValue = json.loads(result.stdout)
    if not isinstance(decoded, dict):
        message = "gh api returned a non-object response"
        raise TypeError(message)
    return decoded


def _enabled(value: JsonValue) -> bool:
    return bool(value.get("enabled")) if isinstance(value, dict) else bool(value)


def _normalize_policy(policy: JsonObject) -> JsonObject:
    normalized = {
        key: value for key, value in policy.items() if key != "required_signatures"
    }
    checks = normalized.get("required_status_checks")
    if isinstance(checks, dict):
        contexts = checks.get("contexts")
        normalized["required_status_checks"] = {
            **checks,
            "contexts": sorted(str(item) for item in contexts)
            if isinstance(contexts, list)
            else [],
        }
    return normalized


def _endpoints(repository: str, branch: str) -> Endpoints:
    base = f"repos/{repository}/branches/{branch}/protection"
    return Endpoints(protection=base, signatures=f"{base}/required_signatures")


def check(policy: JsonObject, repository: str, branch: str) -> bool:
    endpoints = _endpoints(repository, branch)
    try:
        remote = _api(endpoints.protection)
    except MissingProtectionError:
        return False
    if remote is None:
        return False
    return _normalize_remote(remote, policy) == _normalize_policy(
        policy
    ) and _signatures_enabled(endpoints.signatures) == bool(
        policy.get("required_signatures")
    )


def _normalize_remote(remote: JsonObject, policy: JsonObject) -> JsonObject:
    checks = remote.get("required_status_checks")
    reviews = remote.get("required_pull_request_reviews")
    expected_reviews = policy.get("required_pull_request_reviews")
    if isinstance(reviews, dict) and isinstance(expected_reviews, dict):
        reviews = {key: reviews.get(key) for key in expected_reviews}
    normalized: JsonObject = {
        "required_status_checks": None,
        "enforce_admins": _enabled(remote.get("enforce_admins")),
        "required_pull_request_reviews": reviews,
        "restrictions": remote.get("restrictions"),
    }
    if isinstance(checks, dict):
        contexts = checks.get("contexts")
        normalized["required_status_checks"] = {
            "strict": checks.get("strict"),
            "contexts": sorted(str(item) for item in contexts)
            if isinstance(contexts, list)
            else [],
        }
    for key in BOOLEAN_SETTINGS:
        normalized[key] = _enabled(remote.get(key))
    return normalized


def _signatures_enabled(endpoint: str) -> bool:
    try:
        remote = _api(endpoint)
    except MissingProtectionError:
        return False
    return _enabled(remote)


def apply(policy: JsonObject, repository: str, branch: str) -> None:
    endpoints = _endpoints(repository, branch)
    if check(policy, repository, branch):
        sys.stdout.write(f"branch protection already matches {repository}:{branch}\n")
        return
    _api(endpoints.protection, method="PUT", payload=_normalize_policy(policy))
    signature_method = "POST" if policy.get("required_signatures") else "DELETE"
    try:
        _api(endpoints.signatures, method=signature_method)
    except MissingProtectionError:
        if signature_method != "DELETE":
            raise
    sys.stdout.write(f"applied branch protection to {repository}:{branch}\n")


def rollback(repository: str, branch: str) -> None:
    endpoint = _endpoints(repository, branch).protection
    try:
        _api(endpoint, method="DELETE")
    except MissingProtectionError:
        sys.stdout.write(
            f"branch protection already absent from {repository}:{branch}\n"
        )
        return
    sys.stdout.write(f"removed branch protection from {repository}:{branch}\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "apply", "rollback"))
    parser.add_argument(
        "--repository", default=DEFAULT_REPOSITORY, metavar="OWNER/REPO"
    )
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return _run(args)
    except (
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
        TypeError,
        ValueError,
    ) as error:
        sys.stderr.write(f"branch protection failed: {error}\n")
        return 1


def _run(args: argparse.Namespace) -> int:
    decoded: JsonValue = json.loads(args.policy.read_text(encoding="utf-8"))
    if not isinstance(decoded, dict):
        message = "branch protection policy must be a JSON object"
        raise TypeError(message)
    if args.command == "check":
        if not check(decoded, args.repository, args.branch):
            sys.stderr.write(
                f"branch protection differs for {args.repository}:{args.branch}\n"
            )
            return 1
        sys.stdout.write(f"branch protection matches {args.repository}:{args.branch}\n")
    elif args.command == "apply":
        apply(decoded, args.repository, args.branch)
    else:
        rollback(args.repository, args.branch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
