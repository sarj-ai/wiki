from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class TokenSnapshot:
    """Database boundary data, decoded into a decision before it authorizes work."""

    access_ciphertext: str | None
    credential_ciphertext: str | None
    expires_at: datetime | None
    generation: int
    session_revision: int
    lease_until: datetime | None


@dataclass(frozen=True, slots=True)
class UseGoogleAccess:
    access_ciphertext: str
    expires_at: datetime
    generation: int


@dataclass(frozen=True, slots=True)
class RefreshGrant:
    credential_ciphertext: str
    generation: int
    session_revision: int
    lease_until: datetime | None


@dataclass(frozen=True, slots=True)
class AcquireGoogleRefresh:
    grant: RefreshGrant


@dataclass(frozen=True, slots=True)
class AwaitGoogleRefresh:
    until: datetime


@dataclass(frozen=True, slots=True)
class RequireGoogleLogin:
    pass


type GoogleTokenDecision = (
    UseGoogleAccess | AcquireGoogleRefresh | AwaitGoogleRefresh | RequireGoogleLogin
)


def decide_google_token(
    snapshot: TokenSnapshot,
    now: datetime,
    *,
    expiry_skew: timedelta,
    force_generation: int | None,
) -> GoogleTokenDecision:
    if snapshot.generation < 0 or snapshot.session_revision < 0:
        return RequireGoogleLogin()
    forced = force_generation is not None and snapshot.generation == force_generation
    if (
        not forced
        and snapshot.access_ciphertext is not None
        and snapshot.expires_at is not None
        and snapshot.expires_at > now + expiry_skew
    ):
        return UseGoogleAccess(snapshot.access_ciphertext, snapshot.expires_at, snapshot.generation)
    if snapshot.credential_ciphertext is None:
        return RequireGoogleLogin()
    if snapshot.lease_until is not None and snapshot.lease_until > now:
        return AwaitGoogleRefresh(snapshot.lease_until)
    return AcquireGoogleRefresh(
        RefreshGrant(
            snapshot.credential_ciphertext,
            snapshot.generation,
            snapshot.session_revision,
            snapshot.lease_until,
        )
    )
