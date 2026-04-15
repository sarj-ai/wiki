"""User-scoped Drive credential management.

The Navigator has no service account in the read or write path
(PIVOT_PLAN.md §8.2). Every Drive call is attributed to a real user
via their encrypted OAuth tokens, which makes Drive's audit log
useful and eliminates an entire class of ACL divergence bugs.
"""

from __future__ import annotations

from typing import cast

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build

from app.auth.encryption import decrypt_token
from app.config import settings
from app.models.user import User


def build_credentials(user: User) -> Credentials:
    """Build Google OAuth credentials from a user's encrypted tokens."""
    if user.encrypted_access_token is None:
        raise ValueError(f"User {user.id} has no encrypted_access_token")
    access_token = decrypt_token(user.encrypted_access_token)
    refresh_token = (
        decrypt_token(user.encrypted_refresh_token) if user.encrypted_refresh_token else None
    )
    return Credentials(  # type: ignore[no-untyped-call]
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",  # noqa: S106 — public OAuth endpoint
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
    )


def get_drive_service(user: User) -> Resource:
    """Build an authenticated Drive v3 service for the given user."""
    return cast(
        "Resource",
        build("drive", "v3", credentials=build_credentials(user), static_discovery=True),
    )
