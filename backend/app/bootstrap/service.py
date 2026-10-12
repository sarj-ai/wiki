from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from typing import NamedTuple, override

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.schemas import UserProfile
from app.bootstrap.schemas import BootstrapRead
from app.drive.schemas import DriveSummary, DriveTree
from app.drive.service import DriveService
from app.extras.schemas import DocIconRead, FolderIconRead, PreferencesRead, SortOrderRead
from app.extras.service import ExtrasService, list_accessible_doc_icons
from app.files.service import FilesService
from app.identifiers import DriveId
from app.models.user import User
from app.sidebar.cache import SidebarCacheService


class _ExtrasReads(NamedTuple):
    preferences: PreferencesRead
    folder_icons: list[FolderIconRead]
    doc_icons: list[DocIconRead]
    sort_orders: list[SortOrderRead]


class BootstrapService(ABC):
    @abstractmethod
    async def load(self, *, defer_doc_icons: bool = False) -> BootstrapRead: ...


class AggregatingBootstrapService(BootstrapService):
    def __init__(
        self,
        *,
        drive: DriveService,
        extras: ExtrasService,
        files: FilesService,
        cache: SidebarCacheService,
        db: AsyncSession,
        user: User,
    ) -> None:
        self._drive = drive
        self._extras = extras
        self._files = files
        self._cache = cache
        self._db = db
        self._user = user

    @override
    async def load(self, *, defer_doc_icons: bool = False) -> BootstrapRead:
        drive_read = asyncio.create_task(self._drive.list_drives())
        extras_read = asyncio.create_task(self._extras_reads(defer_doc_icons=defer_doc_icons))
        try:
            drives, extras = await asyncio.gather(drive_read, extras_read)
        finally:
            # Both tasks belong to this request. A failed read must not leave
            # its companion using the request's session after load has exited.
            pending = [task for task in (drive_read, extras_read) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(drive_read, extras_read, return_exceptions=True)
        preferences, folder_icons, doc_icons, sort_orders = extras
        return BootstrapRead(
            drives=drives,
            preferences=preferences,
            folder_icons=folder_icons,
            doc_icons=doc_icons,
            sort_orders=sort_orders,
            first_drive_tree=await self._peek_first_tree(drives, preferences.hidden_workspace_ids),
            me=UserProfile.from_user(self._user),
        )

    async def _peek_first_tree(
        self, drives: list[DriveSummary], hidden_workspace_ids: list[DriveId]
    ) -> DriveTree | None:
        hidden = set(hidden_workspace_ids)
        first_drive = next((drive for drive in drives if drive.id not in hidden), None)
        if first_drive is None:
            return None
        cached = await self._cache.peek_drive_tree(self._db, first_drive.id)
        return cached.tree if cached is not None else None

    async def _extras_reads(self, *, defer_doc_icons: bool) -> _ExtrasReads:
        sidebar = await self._extras.load_sidebar()
        # Bootstrap owns a read-only request session. Release the overlay read
        # before the independent Drive inventory needs a cache connection.
        await self._db.commit()
        return _ExtrasReads(
            sidebar.preferences,
            sidebar.folder_icons,
            ([] if defer_doc_icons else await list_accessible_doc_icons(self._extras, self._files)),
            sidebar.sort_orders,
        )
