from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from typing import TYPE_CHECKING, NamedTuple, override

from sqlalchemy import select, update
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
from app.models.wiki import DocIcon


if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.engine import Row

    from app.files.service import FilesService


_DOC_ICON_CPU_OFFLOAD_THRESHOLD = 4_096


class SidebarExtras(NamedTuple):
    preferences: PreferencesRead
    folder_icons: list[FolderIconRead]
    sort_orders: list[SortOrderRead]


def _doc_icon_models(
    rows: Sequence[Row[FileId, str | None]],
) -> list[DocIconRead]:
    return [DocIconRead(drive_id=drive_id, icon=icon) for drive_id, icon in rows]


async def list_accessible_doc_icons(
    extras: ExtrasService,
    files: FilesService,
) -> list[DocIconRead]:
    icons = await extras.list_doc_icons()
    if not icons:
        return []
    accessible = await files.get_accessible_ids([icon.drive_id for icon in icons])
    return [icon for icon in icons if icon.drive_id in accessible]


class ExtrasService(ABC):
    async def load_sidebar(self) -> SidebarExtras:
        # These reads share one AsyncSession and must not run concurrently.
        return SidebarExtras(
            await self.get_preferences(),
            await self.list_folder_icons(),
            await self.list_sort_orders(),
        )

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
    async def load_sidebar(self) -> SidebarExtras:
        row = (
            await self._db.execute(
                select(
                    User.show_non_doc_files,
                    User.hidden_workspace_ids,
                    User.folder_icons,
                    User.sort_orders,
                ).where(User.id == self._user.id)
            )
        ).one()
        return SidebarExtras(
            PreferencesRead(
                show_non_doc_files=row.show_non_doc_files,
                hidden_workspace_ids=row.hidden_workspace_ids,
            ),
            [
                FolderIconRead(folder_drive_id=key, icon=value)
                for key, value in row.folder_icons.items()
            ],
            [
                SortOrderRead(drive_id=key, sort_order=value)
                for key, value in row.sort_orders.items()
            ],
        )

    @override
    async def list_doc_icons(self) -> list[DocIconRead]:
        rows = (await self._db.execute(select(DocIcon.drive_id, DocIcon.icon))).all()
        # Permission filtering can use another session; release this read first.
        await self._db.commit()
        if len(rows) >= _DOC_ICON_CPU_OFFLOAD_THRESHOLD:
            return await asyncio.to_thread(_doc_icon_models, rows)
        return _doc_icon_models(rows)

    @override
    async def upsert_doc_icon(self, drive_id: FileId, payload: DocIconWrite) -> DocIconRead:
        stmt = pg_insert(DocIcon).values(drive_id=drive_id, icon=payload.icon)
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=[DocIcon.drive_id],
                set_={"icon": stmt.excluded.icon},
            )
        )
        await self._db.commit()
        return DocIconRead(drive_id=drive_id, icon=payload.icon)

    @override
    async def list_folder_icons(self) -> list[FolderIconRead]:
        icons = await self._db.scalar(select(User.folder_icons).where(User.id == self._user.id))
        return [
            FolderIconRead(folder_drive_id=key, icon=value) for key, value in (icons or {}).items()
        ]

    @override
    async def upsert_folder_icon(
        self, folder_drive_id: FileId, payload: FolderIconWrite
    ) -> FolderIconRead:
        await self._db.execute(
            update(User)
            .where(User.id == self._user.id)
            .values(
                folder_icons=User.folder_icons.op("||")({folder_drive_id: payload.icon}),
            )
        )
        await self._db.commit()
        return FolderIconRead(folder_drive_id=folder_drive_id, icon=payload.icon)

    @override
    async def list_sort_orders(self) -> list[SortOrderRead]:
        orders = await self._db.scalar(select(User.sort_orders).where(User.id == self._user.id))
        return [
            SortOrderRead(drive_id=key, sort_order=value) for key, value in (orders or {}).items()
        ]

    @override
    async def upsert_sort_orders(self, items: list[SortOrderWrite]) -> None:
        if not items:
            return
        # PostgreSQL merges against the locked current row, preserving concurrent edits.
        merged = User.sort_orders.op("||")({item.drive_id: item.sort_order for item in items})
        await self._db.execute(
            update(User)
            .where(
                User.id == self._user.id,
                User.sort_orders.is_distinct_from(merged),
            )
            .values(sort_orders=merged)
        )
        await self._db.commit()

    @override
    async def get_preferences(self) -> PreferencesRead:
        row = (
            await self._db.execute(
                select(
                    User.show_non_doc_files,
                    User.hidden_workspace_ids,
                ).where(User.id == self._user.id)
            )
        ).one()
        return PreferencesRead(
            show_non_doc_files=row.show_non_doc_files, hidden_workspace_ids=row.hidden_workspace_ids
        )

    @override
    async def upsert_preferences(self, payload: PreferencesWrite) -> PreferencesRead:
        values: dict[str, object] = {}
        if payload.show_non_doc_files is not None:
            values["show_non_doc_files"] = payload.show_non_doc_files
        if "hidden_workspace_ids" in payload.model_fields_set:
            values["hidden_workspace_ids"] = payload.hidden_workspace_ids
        if not values:
            return await self.get_preferences()
        row = (
            await self._db.execute(
                update(User)
                .where(User.id == self._user.id)
                .values(**values)
                .returning(User.show_non_doc_files, User.hidden_workspace_ids)
            )
        ).one()
        await self._db.commit()
        return PreferencesRead(
            show_non_doc_files=row.show_non_doc_files, hidden_workspace_ids=row.hidden_workspace_ids
        )
