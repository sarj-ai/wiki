"""Check or safely strengthen branch protection; destructive rollback is disabled."""  # ruff: ignore[implicit-namespace-package] -- standalone repository CLI, not an import package.

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- fixed gh CLI, no shell
import sys
from typing import Annotated

import typer


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

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
type JsonObject = dict[str, JsonValue]


class MissingProtectionError(RuntimeError):
    pass


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
        missing_markers = ("Branch not protected",)
        if result.returncode == 1 and any(marker in result.stderr for marker in missing_markers):
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
    if isinstance(value, bool):
        return value
    if isinstance(value, dict) and set(value) <= {"enabled", "url"}:
        enabled = value.get("enabled")
        if isinstance(enabled, bool):
            return enabled
    msg = "Malformed remote protection flag"
    raise ValueError(msg)


def _normalize_policy(policy: JsonObject) -> JsonObject:
    allowed = {
        *BOOLEAN_SETTINGS,
        "required_status_checks",
        "enforce_admins",
        "required_pull_request_reviews",
        "restrictions",
        "required_signatures",
    }
    if set(policy) != allowed:
        msg = "Policy must explicitly contain exactly the supported protection settings"
        raise ValueError(msg)
    for name in (*BOOLEAN_SETTINGS, "enforce_admins", "required_signatures"):
        if not isinstance(policy[name], bool):
            msg = "Protection flags must be explicit booleans"
            raise TypeError(msg)
    normalized = {key: value for key, value in policy.items() if key != "required_signatures"}
    checks = normalized.get("required_status_checks")
    if not isinstance(checks, dict):
        msg = "Policy requires an explicit nonempty app-bound status-check object"
        raise TypeError(msg)
    if set(checks) != {"strict", "checks"} or not isinstance(checks["strict"], bool):
        msg = "Policy must use app-bound checks, not legacy contexts"
        raise ValueError(msg)
    entries = _checks(checks.get("checks"), require_app=True)
    normalized["required_status_checks"] = {
        "strict": checks.get("strict"),
        "checks": entries,
    }
    return normalized


def _checks(value: JsonValue, *, require_app: bool = False) -> list[JsonValue]:
    if not isinstance(value, list):
        msg = "Status checks must explicitly include app identities"
        raise TypeError(msg)
    result: list[JsonValue] = []
    contexts: set[str] = set()
    if require_app and not value:
        msg = "Policy requires at least one app-bound status check"
        raise ValueError(msg)
    for entry in value:
        if not isinstance(entry, dict) or not isinstance(entry.get("context"), str):
            msg = "Invalid required status check"
            raise TypeError(msg)
        context = entry["context"]
        if (
            not isinstance(context, str)
            or not context.strip()
            or context in contexts
            or set(entry) != {"context", "app_id"}
        ):
            msg = "Required check contexts must be unique, nonblank and have exact fields"
            raise ValueError(msg)
        contexts.add(context)
        app_id = entry.get("app_id")
        if app_id is not None and (type(app_id) is not int or app_id < -1):
            msg = "Invalid required check app identity"
            raise ValueError(msg)
        if require_app and (not isinstance(app_id, int) or app_id <= 0):
            msg = "Policy check producers must use explicit positive app IDs"
            raise ValueError(msg)
        result.append({"context": entry["context"], "app_id": app_id})
    return sorted(result, key=lambda item: json.dumps(item, sort_keys=True))


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
    return _normalize_remote(remote) == _normalize_policy(policy) and _signatures_enabled(
        endpoints.signatures
    ) == bool(policy.get("required_signatures"))


def _normalize_remote(remote: JsonObject) -> JsonObject:
    required = {
        *BOOLEAN_SETTINGS,
        "required_status_checks",
        "enforce_admins",
    }
    if not required <= remote.keys() or remote.keys() - required - {
        "url",
        "required_signatures",
        "required_pull_request_reviews",
        "restrictions",
    }:
        msg = "Unsupported or missing remote protection settings"
        raise ValueError(msg)
    checks = remote.get("required_status_checks")
    reviews = remote.get("required_pull_request_reviews")
    if isinstance(reviews, dict):
        reviews = {key: value for key, value in reviews.items() if key != "url"}
    normalized: JsonObject = {
        "required_status_checks": None,
        "enforce_admins": _enabled(remote.get("enforce_admins")),
        "required_pull_request_reviews": reviews,
        "restrictions": remote.get("restrictions"),
    }
    if isinstance(checks, dict):
        if checks.keys() - {"strict", "checks", "contexts", "url", "contexts_url"}:
            msg = "Unsupported remote status-check settings"
            raise ValueError(msg)
        if not isinstance(checks.get("strict"), bool):
            msg = "Malformed remote status-check strictness"
            raise TypeError(msg)
        normalized["required_status_checks"] = {
            "strict": checks.get("strict"),
            "checks": _checks(checks.get("checks")),
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


def snapshot(repository: str, branch: str) -> JsonObject:
    endpoints = _endpoints(repository, branch)
    remote = _api(endpoints.protection)
    if remote is None:
        raise MissingProtectionError(endpoints.protection)
    return {
        "target": f"{repository}:{branch}",
        "protection": _normalize_remote(remote),
        "required_signatures": _signatures_enabled(endpoints.signatures),
    }


def fingerprint(state: JsonObject) -> str:
    encoded = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def apply(
    policy: JsonObject,
    repository: str,
    branch: str,
    *,
    expected_live: str | None = None,
    confirm_target: str | None = None,
) -> None:
    endpoints = _endpoints(repository, branch)
    desired = _normalize_policy(policy)
    state = _confirmed_state(repository, branch, expected_live, confirm_target)
    _reject_weakening(state, desired, signatures=bool(policy["required_signatures"]))
    if state["protection"] == desired and state["required_signatures"] == policy.get(
        "required_signatures"
    ):
        sys.stdout.write(f"branch protection already matches {repository}:{branch}\n")
        return
    # This precondition is not an atomic GitHub compare-and-swap; serialize operators.
    _api(endpoints.protection, method="PUT", payload=desired)
    if state["required_signatures"] != policy.get("required_signatures"):
        signature_method = "POST" if policy.get("required_signatures") else "DELETE"
        _api(endpoints.signatures, method=signature_method)
    if not check(policy, repository, branch):
        msg = "Protection final readback differs; inspect partial changes before retrying"
        raise RuntimeError(msg)
    sys.stdout.write(f"applied branch protection to {repository}:{branch}\n")


def _confirmed_state(
    repository: str, branch: str, expected_live: str | None, confirm_target: str | None
) -> JsonObject:
    if confirm_target != f"{repository}:{branch}" or not expected_live:
        msg = "Mutation requires --confirm-target and reviewed --expected-live fingerprint"
        raise ValueError(msg)
    state = snapshot(repository, branch)
    if fingerprint(state) != expected_live:
        msg = "Live protection changed; refusing stale policy mutation"
        raise RuntimeError(msg)
    return state


def _reject_weakening(state: JsonObject, desired: JsonObject, *, signatures: bool) -> None:
    current = state["protection"]
    if not isinstance(current, dict):
        msg = "Current protection must be explicit before applying a policy"
        raise TypeError(msg)
    tighten = (
        "enforce_admins",
        "required_linear_history",
        "required_conversation_resolution",
        "block_creations",
        "lock_branch",
    )
    loosen = ("allow_force_pushes", "allow_deletions", "allow_fork_syncing")
    if (
        any(current.get(key) and not desired.get(key) for key in tighten)
        or any(not current.get(key) and desired.get(key) for key in loosen)
        or (state["required_signatures"] and not signatures)
    ):
        msg = "Protection weakening is unavailable in this safety tool"
        raise ValueError(msg)
    if any(
        current.get(key) != desired.get(key)
        for key in ("required_pull_request_reviews", "restrictions")
    ):
        msg = "Review or actor restriction changes require a separately reviewed procedure"
        raise ValueError(msg)
    old_checks = current.get("required_status_checks")
    new_checks = desired.get("required_status_checks")
    if not isinstance(old_checks, dict) or not isinstance(new_checks, dict):
        return
    if old_checks.get("strict") and not new_checks.get("strict"):
        msg = "Strict status-check weakening is unavailable in this safety tool"
        raise ValueError(msg)
    new_ids = _check_ids(new_checks.get("checks"))
    for context, app_id in _check_ids(old_checks.get("checks")).items():
        if context not in new_ids or (app_id not in {None, -1} and new_ids[context] != app_id):
            msg = "Removing checks or changing established app bindings is unavailable"
            raise ValueError(msg)


def _check_ids(value: JsonValue) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for entry in _checks(value):
        if isinstance(entry, dict) and isinstance(entry.get("context"), str):
            result[str(entry["context"])] = entry["app_id"]
    return result


def rollback(*_args: object, **_kwargs: object) -> None:
    msg = "Deletion of ALL protection is disabled; restore a reviewed policy instead"
    raise ValueError(msg)


class _Command(StrEnum):
    CHECK = "check"
    SNAPSHOT = "snapshot"
    APPLY = "apply"
    ROLLBACK = "rollback"


@dataclass(frozen=True, kw_only=True)
class _Arguments:
    command: _Command
    expected_live: str | None
    confirm_target: str | None
    confirm_delete: str | None
    repository: str
    branch: str
    policy: Path


CLI = typer.Typer(
    add_completion=False,
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    help=__doc__,
)


@CLI.command()
def main(
    command: Annotated[_Command, typer.Argument()],
    *,
    expected_live: Annotated[
        str | None, typer.Option(help="SHA256 from a reviewed read-only snapshot")
    ] = None,
    confirm_target: Annotated[
        str | None, typer.Option(help="Explicit OWNER/REPO:BRANCH mutation target")
    ] = None,
    confirm_delete: Annotated[
        str | None, typer.Option(help="Dangerous DELETE ALL PROTECTION target phrase")
    ] = None,
    repository: Annotated[str, typer.Option(metavar="OWNER/REPO")] = DEFAULT_REPOSITORY,
    branch: Annotated[str, typer.Option()] = DEFAULT_BRANCH,
    policy: Annotated[Path, typer.Option()] = DEFAULT_POLICY,
) -> None:
    args = _Arguments(
        command=command,
        expected_live=expected_live,
        confirm_target=confirm_target,
        confirm_delete=confirm_delete,
        repository=repository,
        branch=branch,
        policy=policy,
    )
    try:
        result = _run(args)
    except (
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
        TypeError,
        ValueError,
    ) as error:
        sys.stderr.write(f"branch protection failed: {error}\n")
        raise typer.Exit(1) from None
    raise typer.Exit(result)


def _run(args: _Arguments) -> int:
    if args.command == "snapshot":
        state = snapshot(args.repository, args.branch)
        sys.stdout.write(
            json.dumps({"fingerprint": fingerprint(state), "state": state}, indent=2) + "\n"
        )
        return 0
    decoded: JsonValue = json.loads(args.policy.read_text(encoding="utf-8"))
    if not isinstance(decoded, dict):
        message = "branch protection policy must be a JSON object"
        raise TypeError(message)
    match args.command:
        case _Command.CHECK:
            if not check(decoded, args.repository, args.branch):
                sys.stderr.write(f"branch protection differs for {args.repository}:{args.branch}\n")
                return 1
            sys.stdout.write(f"branch protection matches {args.repository}:{args.branch}\n")
        case _Command.APPLY:
            apply(
                decoded,
                args.repository,
                args.branch,
                expected_live=args.expected_live,
                confirm_target=args.confirm_target,
            )
        case _:
            rollback(
                args.repository,
                args.branch,
                expected_live=args.expected_live,
                confirm_target=args.confirm_target,
                confirm_delete=args.confirm_delete,
            )
    return 0


if __name__ == "__main__":
    CLI()
