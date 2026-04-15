"""Document content fetching and HTML export."""

from __future__ import annotations

import logging
import time

from fastapi import HTTPException
from googleapiclient.errors import HttpError

from app.drive.credentials import _get_sa_credentials, _get_sa_docs_service, get_sa_drive_service
from app.models.user import User

log = logging.getLogger(__name__)

_RETRYABLE_STATUSES = {429, 500, 502, 503}
_MAX_RETRIES = 2
_RETRY_DELAY = 1.0  # seconds


def _retry_on_transient(func, *args, **kwargs):
    """Retry a Google API call on transient errors (429, 5xx)."""
    last_exc = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except HttpError as exc:
            last_exc = exc
            if exc.resp.status in _RETRYABLE_STATUSES and attempt < _MAX_RETRIES:
                delay = _RETRY_DELAY * (2 ** attempt)
                log.warning(
                    "Google API %s (attempt %d/%d), retrying in %.1fs",
                    exc.resp.status, attempt + 1, _MAX_RETRIES + 1, delay,
                )
                time.sleep(delay)
                continue
            raise
    raise last_exc  # unreachable, but satisfies type checker


def get_doc_content(user: User, google_drive_id: str) -> dict:
    """Fetch a Google Doc's full content via the Docs API (service account).

    Returns the raw document JSON from documents.get().
    Retries on transient errors (429, 500, 502, 503).
    """
    docs_service = _get_sa_docs_service()
    try:
        return _retry_on_transient(
            docs_service.documents().get(
                documentId=google_drive_id,
                includeTabsContent=True,
            ).execute,
        )
    except HttpError as exc:
        if exc.resp.status == 404:
            raise HTTPException(404, "Document not found or deleted") from exc
        if exc.resp.status == 403:
            raise HTTPException(403, "Permission denied") from exc
        log.error("Failed to fetch document %s: %s", google_drive_id, exc)
        raise HTTPException(502, "Failed to fetch document content") from exc


def export_doc_html(user: User, google_drive_id: str, *, tab_id: str | None = None) -> str:
    """Export a Google Doc as HTML via the Drive API (service account).

    The HTML export renders smart chips (Person, Status, File, Date, etc.)
    as actual text — unlike the Docs API which returns opaque placeholders.

    If tab_id is provided, exports only that specific tab using a direct
    HTTP request with the ``?tab=`` parameter (not supported by the SDK).
    """
    if tab_id:
        return _export_doc_html_tab(google_drive_id, tab_id)

    service = get_sa_drive_service()
    response = service.files().export(
        fileId=google_drive_id,
        mimeType="text/html",
    ).execute()
    if isinstance(response, bytes):
        return response.decode("utf-8")
    return response


def _export_doc_html_tab(google_drive_id: str, tab_id: str) -> str:
    """Export a single tab of a Google Doc as HTML.

    Uses a direct authenticated HTTP request because the Drive SDK's
    files().export() doesn't support the ``tab`` query parameter.
    """
    import re
    import urllib.parse
    import urllib.request

    from google.auth.transport.requests import Request

    # Validate inputs to prevent URL injection / SSRF
    if not re.fullmatch(r"[A-Za-z0-9_-]+", google_drive_id):
        raise ValueError(f"Invalid google_drive_id: {google_drive_id!r}")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", tab_id):
        raise ValueError(f"Invalid tab_id: {tab_id!r}")

    creds = _get_sa_credentials()
    if not creds.valid:
        creds.refresh(Request())

    safe_tab_id = urllib.parse.quote(tab_id, safe="")
    url = (
        f"https://docs.google.com/document/d/{google_drive_id}"
        f"/export?format=html&tab={safe_tab_id}"
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {creds.token}"})
    resp = urllib.request.urlopen(req, timeout=30)
    return resp.read().decode("utf-8")
