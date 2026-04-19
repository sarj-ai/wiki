"""``drives.list()`` wrapper — returns shared drives the user is a member of.

The Navigator uses this as its access-control boundary: ``drives.list()``
called with a user's own OAuth token returns **only** the drives that
user is a member of, so any drive that shows up here is safe to render
in the sidebar and any drive that doesn't is safe to hide.
"""

from __future__ import annotations

import asyncio
from typing import Any

from googleapiclient.errors import HttpError
from loguru import logger

from app.drive.credentials import get_drive_service
from app.drive.schemas import DriveSummary
from app.models.user import User


async def list_user_drives(user: User) -> list[DriveSummary]:
    """Return every shared drive ``user`` is a direct member of.

    Runs the sync Google API client in a worker thread so the caller's
    event loop stays unblocked. Paginates until Drive stops returning a
    ``nextPageToken``.

    Returns an empty list on HTTP errors so a single failed request
    doesn't blank out the sidebar; errors are logged for triage.
    """

    def _sync() -> list[DriveSummary]:
        service = get_drive_service(user)
        out: list[DriveSummary] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {
                "pageSize": 100,
                "fields": "nextPageToken,drives(id,name)",
            }
            if page_token:
                params["pageToken"] = page_token
            try:
                resp = service.drives().list(**params).execute()
            except HttpError:
                logger.exception("drives.list failed for user={}", user.id)
                return out
            for d in resp.get("drives", []):
                drive_id = d.get("id")
                name = d.get("name")
                if drive_id and name:
                    out.append(DriveSummary(id=drive_id, name=name))
            page_token = resp.get("nextPageToken")
            if not page_token:
                return out

    return await asyncio.to_thread(_sync)
