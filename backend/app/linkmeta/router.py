from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.linkmeta.schemas import LinkMeta
from app.ratelimit import limiter
from app.sidebar.cache import find_cached_file_name


router = APIRouter(prefix="/link-meta", tags=["link-meta"])


@router.get("/{file_id}")
@limiter.limit("60/minute")  # pyright: ignore[reportUntypedFunctionDecorator]
async def get_link_meta(
    request: Request,  # noqa: ARG001 — slowapi's limiter requires the request in the signature
    file_id: str,
    db: AsyncSession = Depends(get_db),
) -> LinkMeta:
    """Doc name for link previews — public by design, no auth.

    Serves ``og:title`` to unfurl crawlers (Slack, Teams, iMessage, …).
    Names come only from the already-populated sidebar cache: no Google
    calls, no user credentials, and a miss returns ``name: null``. Doc ids
    are high-entropy Drive ids, so possessing an id is the deliberate
    capability to read that doc's title — and nothing else.
    """
    return LinkMeta(name=await find_cached_file_name(db, file_id))
