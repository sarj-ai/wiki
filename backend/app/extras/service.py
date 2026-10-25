from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from datetime import UTC, datetime
from functools import cache
from typing import TYPE_CHECKING, NamedTuple, override

from sqlalchemy import (
    Boolean,
    Double,
    Text,
    Uuid,
    bindparam,
    column,
    func,
    literal,
    select,
    union_all,
)
from sqlalchemy.dialects.postgresql import ARRAY, insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import CompoundSelect

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
    from collections.abc import Sequence

    from sqlalchemy.engine import Row

    from app.files.service import FilesService


_DEFAULT_PREFS = PreferencesRead(show_non_doc_files=True, hidden_workspace_ids=[])
_ARRAY_SORT_ORDER_THRESHOLD = 32
_DOC_ICON_CPU_OFFLOAD_THRESHOLD = 4_096


class SidebarExtras(NamedTuple):
    preferences: PreferencesRead
    folder_icons: list[FolderIconRead]
    sort_orders: list[SortOrderRead]


def _doc_icon_models(
    rows: Sequence[Row[FileId, str | None, str | None]],
) -> list[DocIconRead]:
    return [
        DocIconRead(drive_id=drive_id, icon=icon, cover_url=cover_url)
        for drive_id, icon, cover_url in rows
    ]


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


@cache
def _sidebar_read_statement() -> CompoundSelect[*tuple[object, ...]]:
    user_id = bindparam("sidebar_user_id", type_=Uuid)
    return union_all(
        select(
            literal("preferences").label("kind"),
            literal(None, type_=Text).label("item_id"),
            literal(None, type_=Text).label("icon"),
            literal(None, type_=Double).label("sort_order"),
            UserPreferences.show_non_doc_files,
            UserPreferences.hidden_workspace_ids,
        ).where(UserPreferences.user_id == user_id),
        select(
            literal("folder"),
            UserFolderIcon.folder_drive_id,
            UserFolderIcon.icon,
            literal(None, type_=Double),
            literal(None, type_=Boolean),
            literal(None, type_=ARRAY(Text)),
        ).where(UserFolderIcon.user_id == user_id),
        select(
            literal("sort"),
            UserSortOrder.drive_id,
            literal(None, type_=Text),
            UserSortOrder.sort_order,
            literal(None, type_=Boolean),
            literal(None, type_=ARRAY(Text)),
        ).where(UserSortOrder.user_id == user_id),
    )


class PostgresExtrasService(ExtrasService):
    def __init__(self, *, db: AsyncSession, user: User) -> None:
        self._db = db
        self._user = user

    @override
    async def load_sidebar(self) -> SidebarExtras:
        # UNION ALL keeps each indexed user-scoped lookup independent while
        # paying one database round trip and avoiding ORM entity construction.
        preferences = _DEFAULT_PREFS
        folder_icons: list[FolderIconRead] = []
        sort_orders: list[SortOrderRead] = []
        for kind, item_id, icon, sort_order, show_non_doc_files, hidden_workspace_ids in (
            await self._db.execute(_sidebar_read_statement(), {"sidebar_user_id": self._user.id})
        ).all():
            match kind:
                case "preferences":
                    preferences = PreferencesRead.model_validate(
                        {
                            "show_non_doc_files": show_non_doc_files,
                            "hidden_workspace_ids": hidden_workspace_ids,
                        }
                    )
                case "folder":
                    folder_icons.append(
                        FolderIconRead.model_validate({"folder_drive_id": item_id, "icon": icon})
                    )
                case "sort":
                    sort_orders.append(
                        SortOrderRead.model_validate(
                            {"drive_id": item_id, "sort_order": sort_order}
                        )
                    )
                case _:
                    msg = "Unexpected sidebar overlay kind"
                    raise ValueError(msg)
        return SidebarExtras(preferences, folder_icons, sort_orders)

    @override
    async def list_doc_icons(self) -> list[DocIconRead]:
        rows = (
            await self._db.execute(select(DocIcon.drive_id, DocIcon.icon, DocIcon.cover_url))
        ).all()
        # Permission filtering can open an independent cache session. Release
        # this read transaction before waiting on the provider/cache path.
        await self._db.commit()
        if len(rows) >= _DOC_ICON_CPU_OFFLOAD_THRESHOLD:
            return await asyncio.to_thread(_doc_icon_models, rows)
        return _doc_icon_models(rows)

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
            await self._db.execute(
                select(UserFolderIcon.folder_drive_id, UserFolderIcon.icon).where(
                    UserFolderIcon.user_id == self._user.id
                )
            )
        ).all()
        return [
            FolderIconRead(folder_drive_id=folder_drive_id, icon=icon)
            for folder_drive_id, icon in rows
        ]

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
            await self._db.execute(
                select(UserSortOrder.drive_id, UserSortOrder.sort_order).where(
                    UserSortOrder.user_id == self._user.id
                )
            )
        ).all()
        return [
            SortOrderRead(drive_id=drive_id, sort_order=sort_order) for drive_id, sort_order in rows
        ]

    @override
    async def upsert_sort_orders(self, items: list[SortOrderWrite]) -> None:
        if not items:
            return
        ordered = sorted(items, key=lambda item: item.drive_id)
        if len(ordered) >= _ARRAY_SORT_ORDER_THRESHOLD:
            rows = (
                func.unnest(
                    bindparam(
                        "sort_order_drive_ids",
                        [item.drive_id for item in ordered],
                        type_=ARRAY(Text),
                    ),
                    bindparam(
                        "sort_order_values",
                        [item.sort_order for item in ordered],
                        type_=ARRAY(Double),
                    ),
                )
                .table_valued(column("drive_id", Text), column("sort_order", Double))
                .render_derived()
            )
            stmt = pg_insert(UserSortOrder).from_select(
                [UserSortOrder.user_id, UserSortOrder.drive_id, UserSortOrder.sort_order],
                select(literal(self._user.id), rows.c.drive_id, rows.c.sort_order),
            )
        else:
            stmt = pg_insert(UserSortOrder).values(
                [
                    {
                        "user_id": self._user.id,
                        "drive_id": item.drive_id,
                        "sort_order": item.sort_order,
                    }
                    for item in ordered
                ]
            )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "drive_id"],
            set_={"sort_order": stmt.excluded.sort_order},
            where=UserSortOrder.sort_order.is_distinct_from(stmt.excluded.sort_order),
        )
        await self._db.execute(stmt)
        await self._db.commit()

    @override
    async def get_preferences(self) -> PreferencesRead:
        row = (
            await self._db.execute(
                select(
                    UserPreferences.show_non_doc_files, UserPreferences.hidden_workspace_ids
                ).where(UserPreferences.user_id == self._user.id)
            )
        ).one_or_none()
        if row is None:
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
            UserPreferences.show_non_doc_files, UserPreferences.hidden_workspace_ids
        )
        row = (await self._db.execute(upsert)).one()
        await self._db.commit()
        return PreferencesRead(
            show_non_doc_files=row.show_non_doc_files,
            hidden_workspace_ids=list(row.hidden_workspace_ids),
        )
