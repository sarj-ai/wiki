from datetime import timedelta
import uuid

from pydantic import JsonValue, RootModel, ValidationError
import pytest

from app.identifiers import UserId
from app.mcp.native_schemas import (
    AccessPayload,
    ActiveAccess,
    ActiveRefresh,
    ApprovedIntent,
    BoundIntent,
    CodePayload,
    ConsentCommand,
    ConsentStatus,
    DeniedIntent,
    NativeIntentPayload,
    NativeOAuthError,
    ReadyCode,
    RefreshPayload,
    SpentCode,
    SpentRefresh,
    UnboundIntent,
    WikiConsentIdentity,
    bind_intent,
    intent_status,
    settle_intent,
    spend_code,
    spend_refresh,
)
from app.mcp.native_state import NativePurpose, native_state_payload
from tests.support import dependency


class _StatusPayload(RootModel[ConsentStatus]):
    pass


@pytest.fixture
def intent_data() -> dict[str, JsonValue]:
    return {
        "expires_at": "2030-01-01T00:00:00Z",
        "issuer": "https://wiki.example",
        "client_id": "client",
        "redirect_uri": "http://127.0.0.1:4567/callback",
        "redirect_explicit": True,
        "resource": "https://wiki.example/mcp",
        "state": None,
        "challenge": "a" * 43,
        "scopes": ["wiki:read"],
        "browser_binding": "b" * 64,
    }


@pytest.fixture
def binding_data() -> dict[str, JsonValue]:
    return {
        "user_id": "00000000-0000-4000-8000-000000000001",
        "session_revision": 0,
        "csrf": "wm_intent_" + "c" * 43,
    }


@pytest.fixture
def credential_data() -> dict[str, JsonValue]:
    return {
        "issuer": "https://wiki.example",
        "grant_id": "00000000-0000-4000-8000-000000000002",
        "client_id": "client",
        "user_id": "00000000-0000-4000-8000-000000000001",
        "google_subject": "google-user",
        "issued_at": 1_893_450_000,
        "expires_at": "2030-01-01T00:00:00Z",
        "resource": "https://wiki.example/mcp",
        "scopes": ["wiki:read"],
    }


def test_unbound_legacy_intent_has_no_partial_identity(intent_data: dict[str, JsonValue]) -> None:
    intent = NativeIntentPayload.model_validate(intent_data).root
    assert isinstance(intent, UnboundIntent)
    assert intent_status(intent) == "pending"
    canonical = intent.model_dump(mode="json")
    assert canonical["kind"] == "unbound"
    assert {"user_id", "session_revision", "csrf", "decided", "decision"}.isdisjoint(canonical)
    assert NativeIntentPayload.model_validate(canonical).root == intent


@pytest.mark.parametrize("missing", ["user_id", "session_revision", "csrf"])
def test_partial_legacy_consent_binding_is_rejected(
    intent_data: dict[str, JsonValue], binding_data: dict[str, JsonValue], missing: str
) -> None:
    payload = {**intent_data, **binding_data}
    payload.pop(missing)
    with pytest.raises(ValidationError, match="Incomplete"):
        NativeIntentPayload.model_validate(payload)


@pytest.mark.parametrize(
    ("decided", "decision"), [(False, "approved"), (False, "denied"), (True, None)]
)
def test_contradictory_legacy_consent_decision_is_rejected(
    intent_data: dict[str, JsonValue],
    binding_data: dict[str, JsonValue],
    decided: bool,
    decision: str | None,
) -> None:
    with pytest.raises(ValidationError, match="Contradictory"):
        NativeIntentPayload.model_validate(
            {**intent_data, **binding_data, "decided": decided, "decision": decision}
        )


@pytest.mark.parametrize("command", list(ConsentCommand))
def test_only_a_bound_pending_intent_can_be_decided(
    intent_data: dict[str, JsonValue], command: ConsentCommand
) -> None:
    initial = UnboundIntent.model_validate(intent_data)
    identity = WikiConsentIdentity(
        user_id=UserId(uuid.UUID("00000000-0000-4000-8000-000000000001")), session_revision=0
    )
    bound = bind_intent(initial, identity, "wm_intent_" + "c" * 43)
    assert isinstance(bound, BoundIntent)
    assert initial.kind == "unbound"
    settled = settle_intent(bound, command)
    assert isinstance(settled, DeniedIntent if command is ConsentCommand.DENY else ApprovedIntent)
    assert settled.csrf == bound.csrf
    assert settled.user_id == identity.user_id
    assert bound.kind == "bound"
    with pytest.raises(ValidationError):
        settle_intent(dependency(settled), command)
    with pytest.raises(ValidationError):
        bind_intent(dependency(bound), identity, "another-csrf")


@pytest.mark.parametrize("consumed", [False, True])
def test_legacy_code_binding_is_required_and_consumption_is_explicit(
    credential_data: dict[str, JsonValue], consumed: bool
) -> None:
    payload = {
        **credential_data,
        "consumed": consumed,
        "challenge": "a" * 43,
        "redirect_uri": "http://127.0.0.1:4567/callback",
        "redirect_explicit": True,
    }
    code = CodePayload.model_validate(payload).root
    assert isinstance(code, SpentCode if consumed else ReadyCode)
    assert "consumed" not in code.model_dump()
    assert CodePayload.model_validate_json(code.model_dump_json()).root == code
    for missing in ("challenge", "redirect_uri"):
        with pytest.raises(ValidationError):
            CodePayload.model_validate({**payload, missing: None})


def test_legacy_token_purpose_comes_from_its_collection(
    credential_data: dict[str, JsonValue],
) -> None:
    access = AccessPayload.model_validate(credential_data).root
    refresh = RefreshPayload.model_validate(credential_data).root
    assert isinstance(access, ActiveAccess)
    assert isinstance(refresh, ActiveRefresh)
    assert isinstance(
        RefreshPayload.model_validate({**credential_data, "consumed": True}).root, SpentRefresh
    )
    with pytest.raises(ValidationError):
        AccessPayload.model_validate({**credential_data, "consumed": True})
    with pytest.raises(ValidationError):
        AccessPayload.model_validate(refresh.model_dump(mode="json"))
    with pytest.raises(ValidationError):
        RefreshPayload.model_validate(access.model_dump(mode="json"))


def test_spent_code_and_refresh_cannot_be_consumed_again(
    credential_data: dict[str, JsonValue],
) -> None:
    code = ReadyCode.model_validate(
        {
            **credential_data,
            "challenge": "a" * 43,
            "redirect_uri": "http://127.0.0.1:4567/callback",
            "redirect_explicit": True,
        }
    )
    spent_code = spend_code(code)
    assert spent_code.challenge == code.challenge
    with pytest.raises(ValidationError):
        spend_code(dependency(spent_code))
    refresh = ActiveRefresh.model_validate(credential_data)
    spent_refresh = spend_refresh(refresh)
    assert spent_refresh.grant_id == refresh.grant_id
    with pytest.raises(ValidationError):
        spend_refresh(dependency(spent_refresh))


def test_state_write_cannot_change_collection_or_expiry(
    credential_data: dict[str, JsonValue],
) -> None:
    refresh = ActiveRefresh.model_validate(credential_data)
    assert (
        native_state_payload(NativePurpose.REFRESH, refresh, refresh.expires_at)["kind"]
        == "refresh"
    )
    with pytest.raises(ValidationError):
        native_state_payload(NativePurpose.ACCESS, refresh, refresh.expires_at)
    with pytest.raises(NativeOAuthError):
        native_state_payload(
            NativePurpose.REFRESH, refresh, refresh.expires_at + timedelta(seconds=1)
        )


@pytest.mark.parametrize("scopes", [["wiki:transform"], ["wiki:read", "wiki:read"]])
def test_credential_scopes_require_unique_read_capability(
    credential_data: dict[str, JsonValue], scopes: list[str]
) -> None:
    with pytest.raises(ValidationError, match="unique scopes including read"):
        RefreshPayload.model_validate({**credential_data, "scopes": scopes})


@pytest.mark.parametrize("status", ["pending", "approved", "denied", "expired"])
def test_restart_requirement_is_derived_from_the_authoritative_status(status: str) -> None:
    state = _StatusPayload.model_validate({"status": status}).root
    assert state.model_dump()["restart_required"] is (status != "pending")


@pytest.mark.parametrize("status", ["pending", "approved", "denied", "expired"])
def test_recovery_status_rejects_a_contradictory_restart_flag(status: str) -> None:
    with pytest.raises(ValidationError):
        _StatusPayload.model_validate({"status": status, "restart_required": status == "pending"})
