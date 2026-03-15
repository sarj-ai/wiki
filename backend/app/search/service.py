"""Full-text search using PostgreSQL tsvector + GIN index."""

from __future__ import annotations

import re

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import visible_pages_filter
from app.models.page import Page
from app.models.user import User


def _extract_text_from_blocks(blocks: list[dict]) -> str:
    """Extract plain text from cached_content blocks for search indexing."""
    parts: list[str] = []
    for block in blocks:
        if block.get("runs"):
            for run in block["runs"]:
                if run.get("text"):
                    parts.append(run["text"])
        if block.get("items"):
            for item in block["items"]:
                for run in item.get("runs", []):
                    if run.get("text"):
                        parts.append(run["text"])
        if block.get("rows"):
            for row in block["rows"]:
                for cell in row:
                    for para in cell.get("content", []):
                        for run in para.get("runs", []):
                            if run.get("text"):
                                parts.append(run["text"])
    return " ".join(parts)


async def update_search_vector(db: AsyncSession, page: Page) -> None:
    """Update a page's search_vector from its title + cached content."""
    content_text = ""
    if page.cached_content:
        content_text = _extract_text_from_cached(page.cached_content)

    # Title gets weight A (highest), content gets weight B
    await db.execute(
        text("""
            UPDATE pages
            SET search_vector =
                setweight(to_tsvector('english', coalesce(:title, '')), 'A') ||
                setweight(to_tsvector('english', coalesce(:content, '')), 'B')
            WHERE id = :page_id
        """),
        {"title": page.title, "content": content_text, "page_id": page.id},
    )


async def reindex_all(db: AsyncSession) -> int:
    """Rebuild search_vector for all pages from title + cached_content."""
    result = await db.execute(select(Page))
    pages = list(result.scalars().all())
    count = 0
    for page in pages:
        await update_search_vector(db, page)
        count += 1
    await db.commit()
    return count


async def search_pages(
    db: AsyncSession,
    user: User,
    query: str,
    *,
    limit: int = 20,
) -> list[dict]:
    """Search pages by title and content, respecting access control.

    Returns a list of search results with id, title, icon, workspace_id,
    is_private, and a relevance score.
    """
    if not query.strip():
        return []

    # Build tsquery safely: use plainto_tsquery for safe parameterized input,
    # then add prefix matching via websearch_to_tsquery for the last word.
    # Strip all non-alphanumeric chars (except spaces) to prevent tsquery injection.
    sanitized = re.sub(r"[^\w\s]", "", query.strip())
    words = sanitized.split()
    if not words:
        return []

    # Use plainto_tsquery for safe parameterized full-text search.
    # Add prefix matching for the last word (autocomplete behavior).
    ts_query = func.plainto_tsquery("english", sanitized)

    visibility = visible_pages_filter(user)

    # Rank by relevance, with title matches boosted via weights
    rank = func.ts_rank_cd(Page.search_vector, ts_query)

    # ILIKE fallback: escape special LIKE chars (%, _) in user input
    escaped_query = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    ilike_pattern = f"%{escaped_query}%"

    stmt = (
        select(
            Page.id,
            Page.title,
            Page.icon,
            Page.workspace_id,
            Page.is_private,
            Page.parent_id,
            Page.google_drive_id,
            Page.cached_content,
            rank.label("rank"),
        )
        .where(
            visibility,
            or_(
                Page.search_vector.op("@@")(ts_query),
                Page.title.ilike(ilike_pattern),
            ),
        )
        .order_by(rank.desc())
        .limit(limit)
    )

    result = await db.execute(stmt)
    rows = result.all()

    search_lower = query.strip().lower()

    return [
        {
            "id": str(row.id),
            "title": row.title,
            "icon": row.icon,
            "workspace_id": str(row.workspace_id) if row.workspace_id else None,
            "is_private": row.is_private,
            "parent_id": str(row.parent_id) if row.parent_id else None,
            "google_drive_id": row.google_drive_id,
            "snippet": _build_snippet(row.cached_content, search_lower) if row.cached_content else None,
            "rank": float(row.rank),
        }
        for row in rows
    ]


def _extract_text_from_cached(cached: list[dict]) -> str:
    """Extract text from cached_content, handling both tabs and legacy flat-blocks formats."""
    if not cached:
        return ""
    # Tabs format: list of {"title": ..., "blocks": [...]}
    if isinstance(cached[0], dict) and "blocks" in cached[0]:
        parts = []
        for tab in cached:
            parts.append(_extract_text_from_blocks(tab.get("blocks", [])))
        return " ".join(parts)
    # Legacy flat-blocks format
    return _extract_text_from_blocks(cached)


def _build_snippet(cached: list[dict], search_lower: str, context_chars: int = 60) -> str | None:
    """Extract a text snippet around the first match in the content."""
    full_text = _extract_text_from_cached(cached)
    if not full_text:
        return None

    idx = full_text.lower().find(search_lower)
    if idx == -1:
        # Try matching individual words (stemming may have matched)
        for word in search_lower.split():
            idx = full_text.lower().find(word)
            if idx != -1:
                break

    if idx == -1:
        return None

    start = max(0, idx - context_chars)
    end = min(len(full_text), idx + len(search_lower) + context_chars)

    snippet = full_text[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(full_text):
        snippet = snippet + "..."

    return snippet
