from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.database import async_session, close_connector


_CLEAR_LEGACY_CACHE = text(
    """
    DO $$
    BEGIN
        IF to_regclass('public.drive_tree_cache') IS NOT NULL THEN
            DELETE FROM drive_tree_cache;
        END IF;
    END $$
    """
)


async def clear_legacy_drive_tree_cache() -> None:
    try:
        async with async_session.begin() as db:
            await db.execute(_CLEAR_LEGACY_CACHE)
    finally:
        await close_connector()


if __name__ == "__main__":
    asyncio.run(clear_legacy_drive_tree_cache())
