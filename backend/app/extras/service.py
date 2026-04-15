"""Persistence for icons, sort order, and user preferences.

Pure DB helpers — no Drive API calls. Routers translate HTTP into
these functions. Keeping the translation layer thin makes it easy
to cover the logic with DB-only unit tests in C4.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.extras.schemas import (
    DocIconRead,
    DocIconWrite,
    FolderIconRead,
    FolderIconWrite,
    PreferencesRead,
    PreferencesWrite,
    SortOrderRead,
    SortOrderWrite,
)
from app.models.navigator import (
    DocIcon,
    UserFolderIcon,
    UserPreferences,
    UserSortOrder,
)
from app.models.user import User


async def get_doc_icon(db: AsyncSession, drive_id: str) -> DocIconRead | None:
    row = await db.get(DocIcon, drive_id)
    if row is None:
        return None
    return DocIconRead(drive_id=row.drive_id, icon=row.icon, cover_url=row.cover_url)


async def upsert_doc_icon(
    db: AsyncSession,
    drive_id: str,
    payload: DocIconWrite,
    user: User,
) -> DocIconRead:
    stmt = (
        pg_insert(DocIcon)
        .values(
            drive_id=drive_id,
            icon=payload.icon,
            cover_url=payload.cover_url,
            updated_at=datetime.now(UTC),
            updated_by=user.id,
        )
        .on_conflict_do_update(
            index_elements=["drive_id"],
            set_={
                "icon": pg_insert(DocIcon).excluded.icon,
                "cover_url": pg_insert(DocIcon).excluded.cover_url,
                "updated_at": pg_insert(DocIcon).excluded.updated_at,
                "updated_by": pg_insert(DocIcon).excluded.updated_by,
            },
        )
    )
    await db.execute(stmt)
    await db.commit()
    return DocIconRead(drive_id=drive_id, icon=payload.icon, cover_url=payload.cover_url)


async def upsert_folder_icon(
    db: AsyncSession,
    user: User,
    folder_drive_id: str,
    payload: FolderIconWrite,
) -> FolderIconRead:
    stmt = (
        pg_insert(UserFolderIcon)
        .values(
            user_id=user.id,
            folder_drive_id=folder_drive_id,
            icon=payload.icon,
            updated_at=datetime.now(UTC),
        )
        .on_conflict_do_update(
            index_elements=["user_id", "folder_drive_id"],
            set_={
                "icon": pg_insert(UserFolderIcon).excluded.icon,
                "updated_at": pg_insert(UserFolderIcon).excluded.updated_at,
            },
        )
    )
    await db.execute(stmt)
    await db.commit()
    return FolderIconRead(folder_drive_id=folder_drive_id, icon=payload.icon)


async def list_folder_icons(db: AsyncSession, user: User) -> list[FolderIconRead]:
    rows = (
        await db.execute(
            select(UserFolderIcon).where(UserFolderIcon.user_id == user.id)
        )
    ).scalars().all()
    return [FolderIconRead(folder_drive_id=r.folder_drive_id, icon=r.icon) for r in rows]


async def upsert_sort_orders(
    db: AsyncSession,
    user: User,
    items: list[SortOrderWrite],
) -> None:
    if not items:
        return
    for item in items:
        stmt = (
            pg_insert(UserSortOrder)
            .values(
                user_id=user.id,
                drive_id=item.drive_id,
                sort_order=item.sort_order,
            )
            .on_conflict_do_update(
                index_elements=["user_id", "drive_id"],
                set_={"sort_order": pg_insert(UserSortOrder).excluded.sort_order},
            )
        )
        await db.execute(stmt)
    await db.commit()


async def list_sort_orders(db: AsyncSession, user: User) -> list[SortOrderRead]:
    rows = (
        await db.execute(
            select(UserSortOrder).where(UserSortOrder.user_id == user.id)
        )
    ).scalars().all()
    return [SortOrderRead(drive_id=r.drive_id, sort_order=r.sort_order) for r in rows]


async def clear_sort_order(db: AsyncSession, user: User, drive_id: str) -> None:
    await db.execute(
        delete(UserSortOrder).where(
            UserSortOrder.user_id == user.id,
            UserSortOrder.drive_id == drive_id,
        )
    )
    await db.commit()


_DEFAULT_PREFS = PreferencesRead(show_non_doc_files=True, hidden_workspace_ids=[])


async def get_preferences(db: AsyncSession, user: User) -> PreferencesRead:
    row = await db.get(UserPreferences, user.id)
    if row is None:
        return _DEFAULT_PREFS
    return PreferencesRead(
        show_non_doc_files=row.show_non_doc_files,
        hidden_workspace_ids=list(row.hidden_workspace_ids),
    )


async def upsert_preferences(
    db: AsyncSession,
    user: User,
    payload: PreferencesWrite,
) -> PreferencesRead:
    """Partial update of per-user preferences.

    Unset fields keep their current value (or the default if the row
    doesn't exist yet).
    """
    current = await get_preferences(db, user)
    show = (
        current.show_non_doc_files
        if payload.show_non_doc_files is None
        else payload.show_non_doc_files
    )
    hidden = (
        list(current.hidden_workspace_ids)
        if payload.hidden_workspace_ids is None
        else payload.hidden_workspace_ids
    )
    stmt = (
        pg_insert(UserPreferences)
        .values(
            user_id=user.id,
            show_non_doc_files=show,
            hidden_workspace_ids=hidden,
            updated_at=datetime.now(UTC),
        )
        .on_conflict_do_update(
            index_elements=["user_id"],
            set_={
                "show_non_doc_files": pg_insert(UserPreferences).excluded.show_non_doc_files,
                "hidden_workspace_ids": pg_insert(UserPreferences).excluded.hidden_workspace_ids,
                "updated_at": pg_insert(UserPreferences).excluded.updated_at,
            },
        )
    )
    await db.execute(stmt)
    await db.commit()
    return PreferencesRead(show_non_doc_files=show, hidden_workspace_ids=hidden)
