"""Persistence service for icons, sort order, and user preferences.

Pure DB; no Drive API. One instance per request, constructed with the
acting user and the request-bound session via ``dependencies.py``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import override

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

_DEFAULT_PREFS = PreferencesRead(show_non_doc_files=True, hidden_workspace_ids=[])


class ExtrasService(ABC):
    """Contract for Navigator UI overlay persistence."""

    # Doc icons (global, any user with Drive write access can edit).
    @abstractmethod
    async def list_doc_icons(self) -> list[DocIconRead]: ...
    @abstractmethod
    async def get_doc_icon(self, drive_id: str) -> DocIconRead | None: ...
    @abstractmethod
    async def upsert_doc_icon(self, drive_id: str, payload: DocIconWrite) -> DocIconRead: ...

    # Folder icons (per-user overrides).
    @abstractmethod
    async def list_folder_icons(self) -> list[FolderIconRead]: ...
    @abstractmethod
    async def upsert_folder_icon(
        self, folder_drive_id: str, payload: FolderIconWrite
    ) -> FolderIconRead: ...

    # Drag-drop sort order (per-user, fractional index).
    @abstractmethod
    async def list_sort_orders(self) -> list[SortOrderRead]: ...
    @abstractmethod
    async def upsert_sort_orders(self, items: list[SortOrderWrite]) -> None: ...
    @abstractmethod
    async def clear_sort_order(self, drive_id: str) -> None: ...

    # UI preferences.
    @abstractmethod
    async def get_preferences(self) -> PreferencesRead: ...
    @abstractmethod
    async def upsert_preferences(self, payload: PreferencesWrite) -> PreferencesRead: ...


class PostgresExtrasService(ExtrasService):
    """Postgres-backed implementation."""

    def __init__(self, *, db: AsyncSession, user: User) -> None:
        self._db = db
        self._user = user

    @override
    async def list_doc_icons(self) -> list[DocIconRead]:
        rows = (await self._db.execute(select(DocIcon))).scalars().all()
        return [DocIconRead(drive_id=r.drive_id, icon=r.icon, cover_url=r.cover_url) for r in rows]

    @override
    async def get_doc_icon(self, drive_id: str) -> DocIconRead | None:
        row = await self._db.get(DocIcon, drive_id)
        if row is None:
            return None
        return DocIconRead(drive_id=row.drive_id, icon=row.icon, cover_url=row.cover_url)

    @override
    async def upsert_doc_icon(self, drive_id: str, payload: DocIconWrite) -> DocIconRead:
        stmt = (
            pg_insert(DocIcon)
            .values(
                drive_id=drive_id,
                icon=payload.icon,
                cover_url=payload.cover_url,
                updated_at=datetime.now(UTC),
                updated_by=self._user.id,
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
        await self._db.execute(stmt)
        await self._db.commit()
        return DocIconRead(drive_id=drive_id, icon=payload.icon, cover_url=payload.cover_url)

    @override
    async def list_folder_icons(self) -> list[FolderIconRead]:
        rows = (
            (
                await self._db.execute(
                    select(UserFolderIcon).where(UserFolderIcon.user_id == self._user.id)
                )
            )
            .scalars()
            .all()
        )
        return [FolderIconRead(folder_drive_id=r.folder_drive_id, icon=r.icon) for r in rows]

    @override
    async def upsert_folder_icon(
        self, folder_drive_id: str, payload: FolderIconWrite
    ) -> FolderIconRead:
        stmt = (
            pg_insert(UserFolderIcon)
            .values(
                user_id=self._user.id,
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
        await self._db.execute(stmt)
        await self._db.commit()
        return FolderIconRead(folder_drive_id=folder_drive_id, icon=payload.icon)

    @override
    async def list_sort_orders(self) -> list[SortOrderRead]:
        rows = (
            (
                await self._db.execute(
                    select(UserSortOrder).where(UserSortOrder.user_id == self._user.id)
                )
            )
            .scalars()
            .all()
        )
        return [SortOrderRead(drive_id=r.drive_id, sort_order=r.sort_order) for r in rows]

    @override
    async def upsert_sort_orders(self, items: list[SortOrderWrite]) -> None:
        if not items:
            return
        # Single round-trip bulk upsert. Drag-drop reorders batch through
        # /extras/sort-order in one request; this keeps that to one INSERT.
        stmt = pg_insert(UserSortOrder).values(
            [
                {
                    "user_id": self._user.id,
                    "drive_id": item.drive_id,
                    "sort_order": item.sort_order,
                }
                for item in items
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "drive_id"],
            set_={"sort_order": stmt.excluded.sort_order},
        )
        await self._db.execute(stmt)
        await self._db.commit()

    @override
    async def clear_sort_order(self, drive_id: str) -> None:
        await self._db.execute(
            delete(UserSortOrder).where(
                UserSortOrder.user_id == self._user.id,
                UserSortOrder.drive_id == drive_id,
            )
        )
        await self._db.commit()

    @override
    async def get_preferences(self) -> PreferencesRead:
        row = await self._db.get(UserPreferences, self._user.id)
        if row is None:
            return _DEFAULT_PREFS
        return PreferencesRead(
            show_non_doc_files=row.show_non_doc_files,
            hidden_workspace_ids=list(row.hidden_workspace_ids),
        )

    @override
    async def upsert_preferences(self, payload: PreferencesWrite) -> PreferencesRead:
        # Partial update with no read-modify-write race: only the fields the
        # caller actually provided land in ``set_``; INSERT path supplies
        # safe defaults for the rest. RETURNING reflects the persisted row.
        now = datetime.now(UTC)
        stmt = pg_insert(UserPreferences).values(
            user_id=self._user.id,
            show_non_doc_files=(
                _DEFAULT_PREFS.show_non_doc_files
                if payload.show_non_doc_files is None
                else payload.show_non_doc_files
            ),
            hidden_workspace_ids=(
                list(_DEFAULT_PREFS.hidden_workspace_ids)
                if payload.hidden_workspace_ids is None
                else payload.hidden_workspace_ids
            ),
            updated_at=now,
        )
        set_: dict[str, object] = {"updated_at": stmt.excluded.updated_at}
        if payload.show_non_doc_files is not None:
            set_["show_non_doc_files"] = stmt.excluded.show_non_doc_files
        if payload.hidden_workspace_ids is not None:
            set_["hidden_workspace_ids"] = stmt.excluded.hidden_workspace_ids
        upsert = stmt.on_conflict_do_update(index_elements=["user_id"], set_=set_).returning(
            UserPreferences
        )
        row = (await self._db.execute(upsert)).scalar_one()
        await self._db.commit()
        return PreferencesRead(
            show_non_doc_files=row.show_non_doc_files,
            hidden_workspace_ids=list(row.hidden_workspace_ids),
        )
