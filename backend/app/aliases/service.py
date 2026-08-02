from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import override

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.aliases.errors import (
    AliasConflictError,
    AliasEditForbiddenError,
    AliasNotFoundError,
    AliasTargetUnsupportedError,
)
from app.aliases.schemas import DocumentAliasForFile, DocumentAliasRead, DocumentAliasValue
from app.drive.schemas import MIME_FOLDER, MIME_SHORTCUT, NAVIGATOR_SUPPORTED_MIME_TYPES
from app.files.errors import FilesError
from app.files.service import FilesService
from app.models.user import User
from app.models.wiki import DocumentAlias


class AliasService(ABC):
    @abstractmethod
    async def get_for_file(self, file_id: str) -> DocumentAliasForFile: ...

    @abstractmethod
    async def resolve(self, alias: DocumentAliasValue) -> DocumentAliasRead: ...

    @abstractmethod
    async def save(self, file_id: str, alias: DocumentAliasValue) -> DocumentAliasRead: ...

    @abstractmethod
    async def retire(self, file_id: str) -> None: ...


class PostgresAliasService(AliasService):
    def __init__(self, *, db: AsyncSession, files: FilesService, user: User) -> None:
        self._db = db
        self._files = files
        self._user = user

    @override
    async def get_for_file(self, file_id: str) -> DocumentAliasForFile:
        await self._files.get(file_id)
        row = await self._active_for_file(file_id)
        return DocumentAliasForFile(alias=row.alias if row else None)

    @override
    async def resolve(self, alias: DocumentAliasValue) -> DocumentAliasRead:
        row = await self._db.get(DocumentAlias, alias)
        if row is None or row.retired_at is not None:
            raise AliasNotFoundError
        try:
            await self._files.get(row.file_id)
        except FilesError as exc:
            if exc.http_status in {403, 404}:
                raise AliasNotFoundError from exc
            raise
        return _to_read(row)

    @override
    async def save(self, file_id: str, alias: DocumentAliasValue) -> DocumentAliasRead:
        file = await self._files.get(file_id)
        if file.mime_type not in NAVIGATOR_SUPPORTED_MIME_TYPES or file.mime_type in {
            MIME_FOLDER,
            MIME_SHORTCUT,
        }:
            raise AliasTargetUnsupportedError
        if not await self._files.can_edit(file_id):
            raise AliasEditForbiddenError

        existing = await self._active_for_file(file_id)
        if existing is not None and existing.alias == alias:
            return _to_read(existing)

        now = datetime.now(UTC)
        if existing is not None:
            existing.retired_at = now
            existing.retired_by = self._user.id
            await self._db.flush()
        statement = (
            pg_insert(DocumentAlias)
            .values(alias=alias, file_id=file_id, created_by=self._user.id)
            .on_conflict_do_nothing()
            .returning(DocumentAlias)
        )
        row = (await self._db.execute(statement)).scalar_one_or_none()
        if row is None:
            await self._db.rollback()
            raise AliasConflictError
        await self._db.commit()
        return _to_read(row)

    @override
    async def retire(self, file_id: str) -> None:
        await self._files.get(file_id)
        if not await self._files.can_edit(file_id):
            raise AliasEditForbiddenError
        row = await self._active_for_file(file_id)
        if row is None:
            return
        row.retired_at = datetime.now(UTC)
        row.retired_by = self._user.id
        await self._db.commit()

    async def _active_for_file(self, file_id: str) -> DocumentAlias | None:
        result = await self._db.execute(
            select(DocumentAlias).where(
                DocumentAlias.file_id == file_id,
                DocumentAlias.retired_at.is_(None),
            )
        )
        return result.scalar_one_or_none()


def _to_read(row: DocumentAlias) -> DocumentAliasRead:
    return DocumentAliasRead(alias=row.alias, file_id=row.file_id)
