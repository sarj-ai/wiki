"""Credential management and thread-local service instances."""

from __future__ import annotations

import threading
import time

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.auth.encryption import decrypt_token
from app.config import settings
from app.models.user import User

_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]

# Thread-local service instances AND credentials — httplib2 is NOT thread-safe
# for concurrent requests, and Credentials are mutated during token refresh.
# Each thread gets its own isolated copies.
_thread_local = threading.local()

# Rebuild Google API service instances after this many seconds to avoid
# stale HTTP connections (e.g., after Cloud Run freeze/thaw cycles).
_SA_SERVICE_TTL = 300  # 5 minutes


def build_credentials(user: User) -> Credentials:
    """Build Google OAuth credentials from a user's encrypted tokens."""
    access_token = decrypt_token(user.encrypted_access_token)
    refresh_token = (
        decrypt_token(user.encrypted_refresh_token)
        if user.encrypted_refresh_token
        else None
    )
    return Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
    )


def get_drive_service(user: User):
    """Build an authenticated Drive v3 service for the given user (reads only)."""
    return build("drive", "v3", credentials=build_credentials(user), static_discovery=True)


def _get_sa_credentials():
    """Get thread-local service account credentials.

    Each thread gets its own Credentials instance because token refresh
    mutates the object (self.token, self.expiry) which is not thread-safe.
    """
    creds = getattr(_thread_local, "sa_credentials", None)
    if creds is not None:
        return creds
    creds = service_account.Credentials.from_service_account_file(
        settings.SERVICE_ACCOUNT_KEY_PATH,
        scopes=_DRIVE_SCOPES,
    )
    _thread_local.sa_credentials = creds
    return creds


def get_sa_drive_service():
    """Build a Drive v3 service using the service account's own credentials.

    Used for all write operations (create, move, rename, trash) on the
    Shared Drive where the service account is a Manager.

    Returns a thread-local instance to avoid httplib2 concurrency crashes.
    Rebuilds after _SA_SERVICE_TTL seconds to avoid stale connections
    (e.g., after Cloud Run freeze/thaw).
    """
    svc = getattr(_thread_local, "sa_drive_service", None)
    ts = getattr(_thread_local, "sa_drive_service_ts", 0)
    if svc is not None and (time.monotonic() - ts) < _SA_SERVICE_TTL:
        return svc
    svc = build("drive", "v3", credentials=_get_sa_credentials(), static_discovery=True)
    _thread_local.sa_drive_service = svc
    _thread_local.sa_drive_service_ts = time.monotonic()
    return svc


def _get_sa_docs_service():
    """Build a Docs v1 service using the service account.

    Returns a thread-local instance to avoid httplib2 concurrency crashes.
    Rebuilds after _SA_SERVICE_TTL seconds.
    """
    svc = getattr(_thread_local, "sa_docs_service", None)
    ts = getattr(_thread_local, "sa_docs_service_ts", 0)
    if svc is not None and (time.monotonic() - ts) < _SA_SERVICE_TTL:
        return svc
    svc = build("docs", "v1", credentials=_get_sa_credentials(), static_discovery=True)
    _thread_local.sa_docs_service = svc
    _thread_local.sa_docs_service_ts = time.monotonic()
    return svc
