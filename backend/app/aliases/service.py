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
from app.aliases.schemas import (
    DocumentAliasForFile,
    DocumentAliasRead,
    DocumentAliasValue,
    RecentDocumentAlias,
)
from app.document_transform.naming import polish_alias_candidates
from app.drive.schemas import MIME_FOLDER, MIME_SHORTCUT, NAVIGATOR_SUPPORTED_MIME_TYPES
from app.drive.service import DriveService
from app.files.errors import FilesError
from app.files.schemas import FileResponse
from app.files.service import FilesService
from app.identifiers import FileId
from app.models.user import User
from app.models.wiki import DocumentAlias


class AliasService(ABC):
    @abstractmethod
    async def get_for_file(self, file_id: FileId) -> DocumentAliasForFile: ...

    @abstractmethod
    async def resolve(self, alias: DocumentAliasValue) -> DocumentAliasRead: ...

    @abstractmethod
    async def save(self, file_id: FileId, alias: DocumentAliasValue) -> DocumentAliasRead: ...

    @abstractmethod
    async def retire(self, file_id: FileId) -> None: ...

    async def resolve_recent(self, aliases: list[DocumentAliasValue]) -> list[RecentDocumentAlias]:
        raise NotImplementedError

    async def ensure_polish_for_file(self, file_id: FileId) -> DocumentAliasRead:
        raise NotImplementedError


class PostgresAliasService(AliasService):
    def __init__(
        self,
        *,
        db: AsyncSession,
        files: FilesService,
        user: User,
        drive: DriveService | None = None,
    ) -> None:
        self._db = db
        self._files = files
        self._user = user
        self._drive = drive

    @override
    async def get_for_file(self, file_id: FileId) -> DocumentAliasForFile:
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
    async def save(self, file_id: FileId, alias: DocumentAliasValue) -> DocumentAliasRead:
        file = await self._files.get(file_id)
        if file.mime_type not in NAVIGATOR_SUPPORTED_MIME_TYPES or file.mime_type in {
            MIME_FOLDER,
            MIME_SHORTCUT,
        }:
            raise AliasTargetUnsupportedError
        if not file.can_edit:
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

    async def ensure_for_file(
        self, file_id: FileId, candidates: tuple[DocumentAliasValue, ...]
    ) -> DocumentAliasRead:
        # Both alias uniqueness and the active-file unique index arbitrate races.
        # Retired aliases remain reserved. Never change Drive permissions or
        # replace an editor's chosen link.
        file = await self._files.get(file_id)
        return await self._ensure_for_validated_file(file, candidates)

    async def _ensure_for_validated_file(
        self, file: FileResponse, candidates: tuple[DocumentAliasValue, ...]
    ) -> DocumentAliasRead:
        if file.mime_type not in NAVIGATOR_SUPPORTED_MIME_TYPES or file.mime_type in {
            MIME_FOLDER,
            MIME_SHORTCUT,
        }:
            raise AliasTargetUnsupportedError
        if not file.can_edit:
            raise AliasEditForbiddenError
        for alias in candidates:
            existing = await self._active_for_file(file.id)
            if existing is not None:
                return _to_read(existing)
            statement = (
                pg_insert(DocumentAlias)
                .values(alias=alias, file_id=file.id, created_by=self._user.id)
                .on_conflict_do_nothing()
                .returning(DocumentAlias)
            )
            row = (await self._db.execute(statement)).scalar_one_or_none()
            if row is not None:
                await self._db.commit()
                return _to_read(row)
            # Under READ COMMITTED this new query observes a concurrent winner
            # after ON CONFLICT has waited for that transaction to finish.
            existing = await self._active_for_file(file.id)
            if existing is not None:
                return _to_read(existing)
        raise AliasConflictError

    @override
    async def ensure_polish_for_file(self, file_id: FileId) -> DocumentAliasRead:
        # Derive candidates from a fresh, authorization-checked Drive read. This
        # is deliberately separate from transform submission so retrying link
        # creation can never polish the document a second time.
        file = await self._files.get(file_id)
        return await self._ensure_for_validated_file(
            file, polish_alias_candidates(file_id, file.name)
        )

    @override
    async def retire(self, file_id: FileId) -> None:
        file = await self._files.get(file_id)
        if not file.can_edit:
            raise AliasEditForbiddenError
        row = await self._active_for_file(file_id)
        if row is None:
            return
        row.retired_at = datetime.now(UTC)
        row.retired_by = self._user.id
        await self._db.commit()

    @override
    async def resolve_recent(self, aliases: list[DocumentAliasValue]) -> list[RecentDocumentAlias]:
        if not aliases:
            return []
        unique_aliases = list(dict.fromkeys(aliases))
        result = await self._db.execute(
            select(DocumentAlias).where(
                DocumentAlias.alias.in_(unique_aliases),
                DocumentAlias.retired_at.is_(None),
            )
        )
        rows_by_alias = {row.alias: row for row in result.scalars().all()}
        ordered_rows = [rows_by_alias[alias] for alias in unique_aliases if alias in rows_by_alias]
        files_by_id = await self._files.get_accessible_many([row.file_id for row in ordered_rows])

        drive_names = (
            {drive.id: drive.name for drive in await self._drive.list_drives()}
            if files_by_id and self._drive is not None
            else {}
        )
        return [
            RecentDocumentAlias(
                alias=row.alias,
                file_id=file.id,
                name=file.name,
                mime_type=file.mime_type,
                drive_name=(drive_names.get(file.drive_id) if file.drive_id is not None else None),
            )
            for row in ordered_rows
            if (file := files_by_id.get(row.file_id)) is not None
        ]

    async def _active_for_file(self, file_id: FileId) -> DocumentAlias | None:
        result = await self._db.execute(
            select(DocumentAlias).where(
                DocumentAlias.file_id == file_id,
                DocumentAlias.retired_at.is_(None),
            )
        )
        return result.scalar_one_or_none()


def _to_read(row: DocumentAlias) -> DocumentAliasRead:
    return DocumentAliasRead(alias=row.alias, file_id=row.file_id)
