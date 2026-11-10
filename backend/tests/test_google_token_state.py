from datetime import UTC, datetime, timedelta

import pytest

from app.auth.google_token_state import (
    AcquireGoogleRefresh,
    AwaitGoogleRefresh,
    RequireGoogleLogin,
    TokenSnapshot,
    UseGoogleAccess,
    decide_google_token,
)


NOW = datetime(2026, 11, 10, tzinfo=UTC)
SKEW = timedelta(minutes=5)


@pytest.mark.parametrize(
    ("access", "expiry", "refresh", "lease", "expected"),
    [
        (None, None, None, None, RequireGoogleLogin),
        ("legacy-ciphertext", None, None, None, RequireGoogleLogin),
        ("access", NOW + timedelta(hours=1), None, None, UseGoogleAccess),
        ("access", NOW + SKEW, "refresh", None, AcquireGoogleRefresh),
        (None, None, "refresh", NOW + timedelta(seconds=1), AwaitGoogleRefresh),
        (None, None, "refresh", NOW, AcquireGoogleRefresh),
    ],
)
def test_credential_decisions_have_only_the_data_required_for_the_selected_action(
    access: str | None,
    expiry: datetime | None,
    refresh: str | None,
    lease: datetime | None,
    expected: type[
        UseGoogleAccess | AcquireGoogleRefresh | AwaitGoogleRefresh | RequireGoogleLogin
    ],
) -> None:
    decision = decide_google_token(
        TokenSnapshot(access, refresh, expiry, 2, 3, lease),
        NOW,
        expiry_skew=SKEW,
        force_generation=None,
    )
    assert isinstance(decision, expected)
    if isinstance(decision, AcquireGoogleRefresh):
        assert decision.grant.credential_ciphertext == "refresh"
        assert decision.grant.generation == 2
        assert decision.grant.session_revision == 3


def test_a_provider_refreshes_only_its_observed_generation_and_reuses_a_newer_token() -> None:
    snapshot = TokenSnapshot("access", "refresh", NOW + timedelta(hours=1), 2, 3, None)
    assert isinstance(
        decide_google_token(snapshot, NOW, expiry_skew=SKEW, force_generation=2),
        AcquireGoogleRefresh,
    )
    assert isinstance(
        decide_google_token(snapshot, NOW, expiry_skew=SKEW, force_generation=1), UseGoogleAccess
    )


def test_invalid_persisted_generations_cannot_authorize_provider_work() -> None:
    snapshot = TokenSnapshot("access", "refresh", NOW + timedelta(hours=1), -1, 3, None)
    assert isinstance(
        decide_google_token(snapshot, NOW, expiry_skew=SKEW, force_generation=None),
        RequireGoogleLogin,
    )
