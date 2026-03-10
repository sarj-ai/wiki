"""Search API endpoint."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.search.service import reindex_all, search_pages

router = APIRouter(prefix="/search", tags=["search"])


@router.get("")
async def search(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=20, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    return await search_pages(db, user, q, limit=limit)


@router.post("/reindex")
async def reindex(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Rebuild search vectors for all pages from cached content."""
    count = await reindex_all(db)
    return {"reindexed": count}
