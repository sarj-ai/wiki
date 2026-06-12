"""Aggregates the app shell's first-paint reads into one response."""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from typing import override

from app.auth.schemas import UserProfile
from app.bootstrap.schemas import BootstrapRead
from app.drive.service import DriveService
from app.extras.schemas import DocIconRead, FolderIconRead, PreferencesRead, SortOrderRead
from app.extras.service import ExtrasService
from app.models.user import User


class BootstrapService(ABC):
    """Contract for the one-shot app-shell payload."""

    @abstractmethod
    async def load(self) -> BootstrapRead: ...


class AggregatingBootstrapService(BootstrapService):
    """Fans the six per-resource reads back out to the owning services."""

    def __init__(self, *, drive: DriveService, extras: ExtrasService, user: User) -> None:
        self._drive = drive
        self._extras = extras
        self._user = user

    @override
    async def load(self) -> BootstrapRead:
        # The Drive round-trip (network, possibly cached) overlaps the
        # Postgres reads. The extras reads themselves run sequentially:
        # they share the request-bound AsyncSession, and SQLAlchemy
        # forbids concurrent queries on one session — at sub-ms each
        # that costs nothing next to the Drive call.
        drives, extras = await asyncio.gather(self._drive.list_drives(), self._extras_reads())
        preferences, folder_icons, doc_icons, sort_orders = extras
        return BootstrapRead(
            drives=drives,
            preferences=preferences,
            folder_icons=folder_icons,
            doc_icons=doc_icons,
            sort_orders=sort_orders,
            me=UserProfile.from_user(self._user),
        )

    async def _extras_reads(
        self,
    ) -> tuple[PreferencesRead, list[FolderIconRead], list[DocIconRead], list[SortOrderRead]]:
        return (
            await self._extras.get_preferences(),
            await self._extras.list_folder_icons(),
            await self._extras.list_doc_icons(),
            await self._extras.list_sort_orders(),
        )
