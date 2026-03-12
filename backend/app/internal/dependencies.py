"""Dependency for verifying Cloud Scheduler OIDC tokens on internal endpoints."""

from __future__ import annotations

import logging
import secrets

from fastapi import HTTPException, Request, status

from app.config import settings

log = logging.getLogger(__name__)


async def verify_scheduler(request: Request) -> None:
    """Verify the request comes from Cloud Scheduler.

    Production: validates the OIDC token in the Authorization header.
    Local dev: accepts a shared secret via X-Scheduler-Secret header.
    """
    # Local dev fallback — shared secret (constant-time comparison)
    if settings.INTERNAL_SCHEDULER_SECRET:
        header_secret = request.headers.get("X-Scheduler-Secret", "")
        if secrets.compare_digest(header_secret, settings.INTERNAL_SCHEDULER_SECRET):
            return

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing token")

    token = auth_header.removeprefix("Bearer ")

    try:
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2 import id_token

        # Skip audience verification — Cloud Scheduler sets audience to the
        # Cloud Run service URI, which can change on redeployment.  We verify
        # the caller's identity via the email claim instead.
        claims = id_token.verify_oauth2_token(token, GoogleRequest(), audience=None)

        email = claims.get("email", "")
        if settings.CLOUD_SCHEDULER_SA_EMAIL and email != settings.CLOUD_SCHEDULER_SA_EMAIL:
            log.warning("OIDC token email mismatch: got %s, expected %s", email, settings.CLOUD_SCHEDULER_SA_EMAIL)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized caller")

    except HTTPException:
        raise
    except Exception:
        log.exception("OIDC token verification failed")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")
