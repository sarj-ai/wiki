from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import TYPE_CHECKING, override

from sqlalchemy import select
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
from app.identifiers import FileId
from app.models.user import User
from app.models.wiki import (
    DocIcon,
    UserFolderIcon,
    UserPreferences,
    UserSortOrder,
)


if TYPE_CHECKING:
    from app.files.service import FilesService


_DEFAULT_PREFS = PreferencesRead(show_non_doc_files=True, hidden_workspace_ids=[])


async def list_accessible_doc_icons(
    extras: ExtrasService,
    files: FilesService,
) -> list[DocIconRead]:
    icons = await extras.list_doc_icons()
    accessible = await files.get_accessible_many([icon.drive_id for icon in icons])
    return [icon for icon in icons if icon.drive_id in accessible]


class ExtrasService(ABC):
    @abstractmethod
    async def list_doc_icons(self) -> list[DocIconRead]: ...
    @abstractmethod
    async def upsert_doc_icon(self, drive_id: FileId, payload: DocIconWrite) -> DocIconRead: ...

    @abstractmethod
    async def list_folder_icons(self) -> list[FolderIconRead]: ...
    @abstractmethod
    async def upsert_folder_icon(
        self, folder_drive_id: FileId, payload: FolderIconWrite
    ) -> FolderIconRead: ...

    @abstractmethod
    async def list_sort_orders(self) -> list[SortOrderRead]: ...
    @abstractmethod
    async def upsert_sort_orders(self, items: list[SortOrderWrite]) -> None: ...
    @abstractmethod
    async def get_preferences(self) -> PreferencesRead: ...
    @abstractmethod
    async def upsert_preferences(self, payload: PreferencesWrite) -> PreferencesRead: ...


class PostgresExtrasService(ExtrasService):
    def __init__(self, *, db: AsyncSession, user: User) -> None:
        self._db = db
        self._user = user

    @override
    async def list_doc_icons(self) -> list[DocIconRead]:
        rows = (await self._db.execute(select(DocIcon))).scalars().all()
        return [DocIconRead(drive_id=r.drive_id, icon=r.icon, cover_url=r.cover_url) for r in rows]

    @override
    async def upsert_doc_icon(self, drive_id: FileId, payload: DocIconWrite) -> DocIconRead:
        cover_url = str(payload.cover_url) if payload.cover_url is not None else None
        stmt = (
            pg_insert(DocIcon)
            .values(
                drive_id=drive_id,
                icon=payload.icon,
                cover_url=cover_url,
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
        return DocIconRead(drive_id=drive_id, icon=payload.icon, cover_url=cover_url)

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
        self, folder_drive_id: FileId, payload: FolderIconWrite
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
    async def get_preferences(self) -> PreferencesRead:
        if (row := await self._db.get(UserPreferences, self._user.id)) is None:
            return _DEFAULT_PREFS
        return PreferencesRead(
            show_non_doc_files=row.show_non_doc_files,
            hidden_workspace_ids=list(row.hidden_workspace_ids),
        )

    @override
    async def upsert_preferences(self, payload: PreferencesWrite) -> PreferencesRead:
        now = datetime.now(UTC)
        updates_hidden_workspaces = "hidden_workspace_ids" in payload.model_fields_set
        stmt = pg_insert(UserPreferences).values(
            user_id=self._user.id,
            show_non_doc_files=(
                _DEFAULT_PREFS.show_non_doc_files
                if payload.show_non_doc_files is None
                else payload.show_non_doc_files
            ),
            hidden_workspace_ids=(
                payload.hidden_workspace_ids
                if updates_hidden_workspaces
                else list(_DEFAULT_PREFS.hidden_workspace_ids)
            ),
            updated_at=now,
        )
        set_: dict[str, object] = {"updated_at": stmt.excluded.updated_at}
        if payload.show_non_doc_files is not None:
            set_["show_non_doc_files"] = stmt.excluded.show_non_doc_files
        if updates_hidden_workspaces:
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
