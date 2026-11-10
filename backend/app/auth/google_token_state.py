from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal


@dataclass(frozen=True, slots=True)
class TokenSnapshot:
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
    kind: Literal["use-access"] = "use-access"


@dataclass(frozen=True, slots=True)
class RefreshGrant:
    credential_ciphertext: str
    generation: int
    session_revision: int
    lease_until: datetime | None


@dataclass(frozen=True, slots=True)
class AcquireGoogleRefresh:
    grant: RefreshGrant
    kind: Literal["acquire-refresh"] = "acquire-refresh"


@dataclass(frozen=True, slots=True)
class AwaitGoogleRefresh:
    until: datetime
    kind: Literal["await-refresh"] = "await-refresh"


@dataclass(frozen=True, slots=True)
class RequireGoogleLogin:
    kind: Literal["require-login"] = "require-login"


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
