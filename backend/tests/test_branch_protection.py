from dataclasses import dataclass
import json
from types import SimpleNamespace
from typing import Any

import pytest

from tests.support import MODULE, ROOT, FakeGitHub


TARGET = "sarj-ai/wiki:main"


@pytest.mark.parametrize("fault", ["unknown", "missing", "malformed"])
def test_unrecognized_remote_security_state_fails_closed(lab: Lab, fault: str) -> None:
    match fault:
        case "unknown":
            lab.github.remote["future_security_setting"] = True
        case "missing":
            del lab.github.remote["enforce_admins"]
        case _:
            lab.github.remote["enforce_admins"] = {"enabled": "false"}
    with pytest.raises(ValueError, match="remote"):
        state_fingerprint()
    assert lab.github.writes == []


@pytest.mark.parametrize("message", ["Not Found", "HTTP 404"])
def test_ambiguous_api_404_is_not_confirmed_absence(
    monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    result = SimpleNamespace(returncode=1, stderr=message, stdout="")
    monkeypatch.setattr(MODULE["subprocess"], "run", lambda *_args, **_kwargs: result)
    with pytest.raises(RuntimeError, match=message) as caught:
        MODULE["_api"]("repos/sarj-ai/wiki/branches/main/protection")
    assert not isinstance(caught.value, MODULE["MissingProtectionError"])


@dataclass
class Lab:
    policy: dict[str, Any]
    github: FakeGitHub


@pytest.fixture
def lab(monkeypatch: pytest.MonkeyPatch):
    policy = json.loads((ROOT / "scripts/github/branch-protection.json").read_text())
    fake = FakeGitHub(policy)
    monkeypatch.setitem(MODULE["check"].__globals__, "_api", fake.api)
    return Lab(policy, fake)


def state_fingerprint() -> str:
    return MODULE["fingerprint"](MODULE["snapshot"]("sarj-ai/wiki", "main"))


def apply(policy: dict[str, Any], expected: str) -> None:
    MODULE["apply"](policy, "sarj-ai/wiki", "main", expected_live=expected, confirm_target=TARGET)


def test_canonical_matches_live_fixture_and_is_app_bound(lab: Lab) -> None:
    policy, fake = lab.policy, lab.github
    assert MODULE["check"](policy, "sarj-ai/wiki", "main")
    assert len(policy["required_status_checks"]["checks"]) == 8
    assert any(
        check == {"context": "commit-policy-v1", "app_id": 15368}
        for check in policy["required_status_checks"]["checks"]
    )
    assert {check["app_id"] for check in policy["required_status_checks"]["checks"]} == {15368}
    fake.remote["required_status_checks"]["checks"].reverse()
    assert MODULE["check"](policy, "sarj-ai/wiki", "main")
    apply(policy, state_fingerprint())
    assert fake.writes == []


def test_same_name_different_app_is_drift(lab: Lab) -> None:
    policy, fake = lab.policy, lab.github
    fake.remote["required_status_checks"]["checks"][0]["app_id"] = 42
    assert not MODULE["check"](policy, "sarj-ai/wiki", "main")
    assert fake.writes == []


def test_omitted_disabled_optional_fields_match(lab: Lab) -> None:
    del lab.github.remote["required_pull_request_reviews"]
    del lab.github.remote["restrictions"]
    assert MODULE["check"](lab.policy, "sarj-ai/wiki", "main")
    apply(lab.policy, state_fingerprint())
    assert lab.github.writes == []


def test_stale_fingerprint_refuses_all_writes(lab: Lab) -> None:
    policy, fake = lab.policy, lab.github
    expected = state_fingerprint()
    fake.remote["enforce_admins"] = {"enabled": False}
    with pytest.raises(RuntimeError, match="stale"):
        apply(policy, expected)
    assert fake.writes == []


@pytest.mark.parametrize("target", [None, "sarj-ai/other:main", "sarj-ai/wiki:other"])
def test_target_confirmation_is_required(lab: Lab, target: str | None) -> None:
    policy, fake = lab.policy, lab.github
    expected = state_fingerprint()
    with pytest.raises(ValueError, match="confirm-target"):
        MODULE["apply"](
            policy, "sarj-ai/wiki", "main", expected_live=expected, confirm_target=target
        )
    assert fake.writes == []


def test_missing_fingerprint_refuses_all_writes(lab: Lab) -> None:
    policy, fake = lab.policy, lab.github
    with pytest.raises(ValueError, match="expected-live"):
        MODULE["apply"](policy, "sarj-ai/wiki", "main", confirm_target=TARGET)
    assert fake.writes == []


def test_success_requires_final_readback(lab: Lab) -> None:
    policy, fake = lab.policy, lab.github
    fake.remote["enforce_admins"] = {"enabled": False}
    apply(policy, state_fingerprint())
    assert MODULE["check"](policy, "sarj-ai/wiki", "main")
    assert fake.writes == [("repos/sarj-ai/wiki/branches/main/protection", "PUT")]


def test_mismatched_final_readback_is_failure(lab: Lab) -> None:
    policy, fake = lab.policy, lab.github
    fake.remote["enforce_admins"] = {"enabled": False}
    fake.ignore_put = True
    expected = state_fingerprint()
    with pytest.raises(RuntimeError, match="readback"):
        apply(policy, expected)
    assert len(fake.writes) == 1


def test_signature_drift_and_partial_failure_never_delete_protection(lab: Lab) -> None:
    policy, fake = lab.policy, lab.github
    policy["required_signatures"] = True
    assert not MODULE["check"](policy, "sarj-ai/wiki", "main")
    fake.fail_signature = True
    expected = state_fingerprint()
    with pytest.raises(RuntimeError, match="signature failure"):
        apply(policy, expected)
    assert all(method != "DELETE" for _, method in fake.writes)


def test_deletion_style_rollback_disabled_by_default(lab: Lab) -> None:
    fake = lab.github
    with pytest.raises(ValueError, match="ALL protection"):
        MODULE["rollback"]("sarj-ai/wiki", "main")
    assert fake.writes == []


def test_explicit_deletion_still_requires_fresh_precondition(lab: Lab) -> None:
    fake = lab.github
    with pytest.raises(ValueError, match="ALL protection"):
        MODULE["rollback"](
            "sarj-ai/wiki",
            "main",
            expected_live="stale",
            confirm_target=TARGET,
            confirm_delete=f"DELETE ALL PROTECTION {TARGET}",
        )
    assert fake.writes == []


def test_unknown_policy_field_fails_without_mutation(lab: Lab) -> None:
    policy, fake = lab.policy, lab.github
    policy["unknown_future_policy"] = True
    expected = state_fingerprint()
    with pytest.raises(ValueError, match="supported"):
        apply(policy, expected)
    assert fake.writes == []


def test_signature_change_invalidates_reviewed_fingerprint(lab: Lab) -> None:
    expected = state_fingerprint()
    lab.github.signatures = True
    with pytest.raises(RuntimeError, match="stale"):
        apply(lab.policy, expected)
    assert lab.github.writes == []


@pytest.mark.parametrize("app_id", [None, -1, 0])
def test_policy_cannot_silently_remove_app_binding(lab: Lab, app_id: int | None) -> None:
    lab.policy["required_status_checks"]["checks"][0]["app_id"] = app_id
    expected = state_fingerprint()
    with pytest.raises(ValueError, match="positive app IDs"):
        apply(lab.policy, expected)
    assert lab.github.writes == []


def test_explicit_deletion_is_confirmed_by_final_readback(lab: Lab) -> None:
    with pytest.raises(ValueError, match="ALL protection"):
        MODULE["rollback"](
            "sarj-ai/wiki",
            "main",
            expected_live=state_fingerprint(),
            confirm_target=TARGET,
            confirm_delete=f"DELETE ALL PROTECTION {TARGET}",
        )
    assert not lab.github.deleted
    assert lab.github.writes == []


@pytest.mark.parametrize(
    "checks",
    [None, [], "invalid", {}, {"strict": True, "checks": []}],
    ids=["null", "list", "string", "empty-object", "empty-checks"],
)
def test_malformed_or_empty_check_policy_never_writes(lab: Lab, checks: Any) -> None:
    lab.policy["required_status_checks"] = checks
    expected = state_fingerprint()
    with pytest.raises((ValueError, TypeError)):
        apply(lab.policy, expected)
    assert lab.github.writes == []


@pytest.mark.parametrize("fault", ["blank", "duplicate", "unknown-field"])
def test_ambiguous_check_entries_never_write(lab: Lab, fault: str) -> None:
    checks = lab.policy["required_status_checks"]["checks"]
    match fault:
        case "blank":
            checks[0]["context"] = " "
        case "duplicate":
            checks.append(checks[0].copy())
        case _:
            checks[0]["unknown"] = True
    expected = state_fingerprint()
    with pytest.raises(ValueError, match="exact fields"):
        apply(lab.policy, expected)
    assert lab.github.writes == []


@pytest.mark.parametrize(
    "setting", ["enforce_admins", "required_linear_history", "required_conversation_resolution"]
)
def test_fresh_fingerprint_does_not_authorize_weakening(lab: Lab, setting: str) -> None:
    lab.policy[setting] = False
    expected = state_fingerprint()
    with pytest.raises(ValueError, match="weakening"):
        apply(lab.policy, expected)
    assert lab.github.writes == []


@pytest.mark.parametrize("fault", ["remove", "app", "strict"])
def test_fresh_fingerprint_does_not_authorize_weaker_checks(lab: Lab, fault: str) -> None:
    checks = lab.policy["required_status_checks"]
    match fault:
        case "remove":
            checks["checks"].pop()
        case "app":
            checks["checks"][0]["app_id"] = 42
        case _:
            checks["strict"] = False
    expected = state_fingerprint()
    with pytest.raises(ValueError, match="unavailable"):
        apply(lab.policy, expected)
    assert lab.github.writes == []


@pytest.mark.parametrize("setting", ["allow_force_pushes", "allow_deletions", "allow_fork_syncing"])
def test_fresh_fingerprint_cannot_enable_bypasses(lab: Lab, setting: str) -> None:
    lab.policy[setting] = True
    expected = state_fingerprint()
    with pytest.raises(ValueError, match="weakening"):
        apply(lab.policy, expected)
    assert lab.github.writes == []


def test_fresh_fingerprint_cannot_disable_signatures(lab: Lab) -> None:
    lab.github.signatures = True
    expected = state_fingerprint()
    with pytest.raises(ValueError, match="weakening"):
        apply(lab.policy, expected)
    assert lab.github.writes == []


@pytest.mark.parametrize("setting", ["required_pull_request_reviews", "restrictions"])
def test_review_and_actor_changes_need_separate_procedure(lab: Lab, setting: str) -> None:
    lab.policy[setting] = {}
    expected = state_fingerprint()
    with pytest.raises(ValueError, match="separately reviewed"):
        apply(lab.policy, expected)
    assert lab.github.writes == []


def test_required_app_bound_check_can_be_added_without_removing_existing_checks(lab: Lab) -> None:
    checks = lab.policy["required_status_checks"]["checks"]
    checks.append({"context": "additional-security-check", "app_id": 15368})
    apply(lab.policy, state_fingerprint())
    assert MODULE["check"](lab.policy, "sarj-ai/wiki", "main")
    assert lab.github.writes == [("repos/sarj-ai/wiki/branches/main/protection", "PUT")]


def test_protection_without_status_checks_can_require_app_bound_checks(lab: Lab) -> None:
    lab.github.remote["required_status_checks"] = None
    apply(lab.policy, state_fingerprint())
    assert MODULE["check"](lab.policy, "sarj-ai/wiki", "main")
    assert lab.github.writes == [("repos/sarj-ai/wiki/branches/main/protection", "PUT")]


@pytest.mark.parametrize("setting", ["enforce_admins", "required_signatures", "restrictions"])
def test_absent_status_checks_do_not_allow_other_security_weakening(lab: Lab, setting: str) -> None:
    lab.github.remote["required_status_checks"] = None
    match setting:
        case "required_signatures":
            lab.github.signatures = True
        case "restrictions":
            lab.policy[setting] = {}
        case _:
            lab.policy[setting] = False
    with pytest.raises(ValueError, match=r"weakening|separately reviewed"):
        apply(lab.policy, state_fingerprint())
    assert lab.github.writes == []
