from copy import deepcopy
from pathlib import Path
import runpy
from typing import Any, Self


def dependency(value: object) -> Any:
    return value


class WordDriveFake:
    def files(self) -> Self:
        return self

    def get_media(self, **kwargs: Any) -> object:
        return object()


class WordCreateDriveFake(WordDriveFake):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        return object()


ROOT = Path(__file__).resolve().parents[2]
MODULE = runpy.run_path(str(ROOT / "scripts/github/branch_protection.py"))


class FakeGitHub:
    def __init__(self, policy: dict[str, Any]) -> None:
        self.remote = self.remote_from_policy(policy)
        self.signatures = policy["required_signatures"]
        self.writes: list[tuple[str, str]] = []
        self.ignore_put = False
        self.fail_signature = False
        self.deleted = False

    @staticmethod
    def remote_from_policy(policy: dict[str, Any]) -> dict[str, Any]:
        remote = deepcopy(policy)
        remote.pop("required_signatures")
        for key in (*MODULE["BOOLEAN_SETTINGS"], "enforce_admins"):
            remote[key] = {"enabled": remote[key]}
        return remote

    def api(
        self, endpoint: str, *, method: str = "GET", payload: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        if method != "GET":
            self.writes.append((endpoint, method))
        if endpoint.endswith("/required_signatures"):
            if self.fail_signature and method != "GET":
                message = "simulated signature failure"
                raise RuntimeError(message)
            if method != "GET":
                self.signatures = method == "POST"
            return {"enabled": self.signatures}
        if method == "PUT" and not self.ignore_put:
            assert payload is not None
            self.remote = self.remote_from_policy(
                {**payload, "required_signatures": self.signatures}
            )
        if method == "DELETE":
            self.deleted = True
            return None
        if self.deleted:
            raise MODULE["MissingProtectionError"](endpoint)
        return deepcopy(self.remote)
