"""Drive changes tracking (incremental sync)."""

from __future__ import annotations

import logging

from app.drive.credentials import get_sa_drive_service
from app.config import settings

log = logging.getLogger(__name__)


def get_drive_changes_start_token() -> str:
    """Get the current start page token for Drive changes tracking.

    When SHARED_DRIVE_ID is set, scopes tracking to that Shared Drive.
    Otherwise tracks all files the service account can see (filtered
    downstream in _incremental_sync by matching against known pages).
    """
    service = get_sa_drive_service()
    kwargs: dict = {"supportsAllDrives": True}
    if settings.SHARED_DRIVE_ID:
        kwargs["driveId"] = settings.SHARED_DRIVE_ID
    response = service.changes().getStartPageToken(**kwargs).execute()
    return response["startPageToken"]


def get_drive_changes(start_token: str) -> tuple[list[dict], str]:
    """Fetch all changes since the given start token.

    Returns (list_of_changes, new_start_token).
    Each change has: fileId, file (metadata or None if removed), removed (bool).
    """
    service = get_sa_drive_service()
    changes: list[dict] = []
    page_token = start_token

    kwargs: dict = {
        "spaces": "drive",
        "fields": "nextPageToken,newStartPageToken,changes(fileId,file(id,name,mimeType,parents,trashed),removed,time)",
        "supportsAllDrives": True,
        "includeItemsFromAllDrives": True,
        "pageSize": 100,
    }
    if settings.SHARED_DRIVE_ID:
        kwargs["driveId"] = settings.SHARED_DRIVE_ID

    while True:
        response = service.changes().list(
            pageToken=page_token,
            **kwargs,
        ).execute()

        changes.extend(response.get("changes", []))

        if "newStartPageToken" in response:
            return changes, response["newStartPageToken"]

        next_token = response.get("nextPageToken")
        if not next_token:
            # Shouldn't happen, but avoid infinite loop
            log.warning("Drive Changes API returned neither newStartPageToken nor nextPageToken")
            return changes, start_token
        page_token = next_token
