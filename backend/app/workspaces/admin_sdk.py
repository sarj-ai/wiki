"""Google Admin SDK operations — fetch domain groups and user memberships.

This module handles all direct interactions with the Google Admin SDK
Directory API via the service account (with domain-wide delegation).
"""

from __future__ import annotations

import logging

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app.config import settings

log = logging.getLogger(__name__)

# Scopes for the service account (read-only directory access)
_SA_SCOPES = [
    "https://www.googleapis.com/auth/admin.directory.group.readonly",
    "https://www.googleapis.com/auth/admin.directory.group.member.readonly",
]


def _get_admin_service():
    """Build an Admin SDK Directory service using the service account
    with domain-wide delegation (impersonating the admin email)."""
    credentials = service_account.Credentials.from_service_account_file(
        settings.SERVICE_ACCOUNT_KEY_PATH,
        scopes=_SA_SCOPES,
    )
    delegated = credentials.with_subject(settings.ADMIN_IMPERSONATE_EMAIL)
    return build("admin", "directory_v1", credentials=delegated, static_discovery=True)


def _get_all_domain_groups() -> set[str]:
    """Fetch all group emails in the domain from Admin SDK."""
    service = _get_admin_service()
    group_emails: set[str] = set()
    page_token = None

    while True:
        result = service.groups().list(
            domain=settings.ALLOWED_DOMAIN,
            pageToken=page_token,
            maxResults=200,
        ).execute()

        for g in result.get("groups", []):
            group_emails.add(g["email"])
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return group_emails


def _fetch_all_groups_sync() -> list[dict]:
    """Fetch all groups from Admin SDK synchronously (for use in a thread)."""
    service = _get_admin_service()
    groups: list[dict] = []
    page_token = None
    while True:
        result = service.groups().list(
            domain=settings.ALLOWED_DOMAIN,
            pageToken=page_token,
            maxResults=200,
        ).execute()
        groups.extend(result.get("groups", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return groups


def _get_user_groups_via_service_account(email: str) -> list[dict]:
    """Fallback: fetch a user's groups using the service account."""
    service = _get_admin_service()
    groups: list[dict] = []
    page_token = None

    while True:
        result = service.groups().list(
            userKey=email,
            pageToken=page_token,
            maxResults=200,
        ).execute()

        groups.extend(result.get("groups", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return groups


def _fetch_user_groups_with_user_token(credentials, email: str) -> list[dict]:
    """Fetch a user's groups using their own OAuth credentials.

    Parameters
    ----------
    credentials:
        A ``google.oauth2.credentials.Credentials`` instance built from the
        user's stored tokens (via ``build_credentials``).
    email:
        The user's email address, used as the ``userKey`` for the groups list.
    """
    service = build("admin", "directory_v1", credentials=credentials, static_discovery=True)
    groups: list[dict] = []
    page_token = None
    while True:
        result = service.groups().list(
            userKey=email,
            pageToken=page_token,
            maxResults=200,
        ).execute()
        groups.extend(result.get("groups", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return groups
