from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from typing import override

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.schemas import UserProfile
from app.bootstrap.schemas import BootstrapRead
from app.drive.schemas import DriveSummary, DriveTree
from app.drive.service import DriveService
from app.extras.schemas import DocIconRead, FolderIconRead, PreferencesRead, SortOrderRead
from app.extras.service import ExtrasService
from app.models.user import User
from app.sidebar.cache import SidebarCacheService


class BootstrapService(ABC):
    @abstractmethod
    async def load(self) -> BootstrapRead: ...


class AggregatingBootstrapService(BootstrapService):
    def __init__(
        self,
        *,
        drive: DriveService,
        extras: ExtrasService,
        cache: SidebarCacheService,
        db: AsyncSession,
        user: User,
    ) -> None:
        self._drive = drive
        self._extras = extras
        self._cache = cache
        self._db = db
        self._user = user

    @override
    async def load(self) -> BootstrapRead:
        drives, extras = await asyncio.gather(self._drive.list_drives(), self._extras_reads())
        preferences, folder_icons, doc_icons, sort_orders = extras
        return BootstrapRead(
            drives=drives,
            preferences=preferences,
            folder_icons=folder_icons,
            doc_icons=doc_icons,
            sort_orders=sort_orders,
            first_drive_tree=await self._peek_first_tree(drives),
            me=UserProfile.from_user(self._user),
        )

    async def _peek_first_tree(self, drives: list[DriveSummary]) -> DriveTree | None:
        if not drives:
            return None
        cached = await self._cache.peek_drive_tree(self._db, drives[0].id)
        return cached.tree if cached is not None else None

    async def _extras_reads(
        self,
    ) -> tuple[PreferencesRead, list[FolderIconRead], list[DocIconRead], list[SortOrderRead]]:
        return (
            await self._extras.get_preferences(),
            await self._extras.list_folder_icons(),
            await self._extras.list_doc_icons(),
            await self._extras.list_sort_orders(),
        )
