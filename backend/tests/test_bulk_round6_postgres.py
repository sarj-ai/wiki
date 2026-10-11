from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import NamedTuple, override

from pydantic import JsonValue
from pydantic_settings import BaseSettings
import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.aliases.schemas import RecentDocumentAliasesRequest
from app.aliases.service import PostgresAliasService
from app.drive.schemas import MIME_DOC
from app.files.schemas import FileResponse
from app.identifiers import FileId, GoogleUserId
from app.models.user import User
from app.models.wiki import DocumentAlias
from tests.bulk_round6_support import BulkDrive, BulkFiles, BulkProvider, bulk_http_error, bulk_user


class BulkDatabaseSettings(BaseSettings):
    wiki_round6_bulk_database_url: str | None = None


class BulkDatabase(NamedTuple):
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    user: User


@pytest.fixture
async def bulk_database() -> AsyncGenerator[BulkDatabase]:
    configured = BulkDatabaseSettings().wiki_round6_bulk_database_url
    if configured is None:
        pytest.skip("Requires the owned round6 loopback bulk benchmark database")
    url = make_url(configured)
    assert (url.host, url.port, url.database) == ("127.0.0.1", 15488, "wiki_round6_bulk_bench")
    engine = create_async_engine(url, pool_size=1, max_overflow=0, pool_timeout=1)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user = bulk_user()
    user.email = f"bulk-test-{user.id}@example.com"
    user.google_id = GoogleUserId(f"bulk-test-{user.id}")
    async with sessions() as db:
        db.add(user)
        await db.commit()
    try:
        yield BulkDatabase(engine, sessions, user)
    finally:
        async with sessions() as db:
            await db.execute(delete(DocumentAlias).where(DocumentAlias.created_by == user.id))
            await db.execute(delete(User).where(User.id == user.id))
            await db.commit()
        await engine.dispose()


class ReleasedAliasFiles(BulkFiles):
    def __init__(
        self, provider: BulkProvider, drive: BulkDrive, database: BulkDatabase, db: AsyncSession
    ) -> None:
        super().__init__(provider, drive)
        self.database = database
        self.db = db

    @override
    async def get_search_metadata_many(self, file_ids: list[FileId]) -> dict[FileId, FileResponse]:
        assert not self.db.in_transaction()
        # The single pool connection must be available before provider work.
        async with self.database.sessions() as other:
            assert await other.scalar(select(User.id).where(User.id == self.database.user.id))
        return await super().get_search_metadata_many(file_ids)


async def test_recent_alias_array_read_preserves_order_retirement_scope_and_fresh_access(
    bulk_database: BulkDatabase,
) -> None:
    labels = ["visible", "denied", "missing", "outside", "wrong", "trashed", "retired"]
    rows = [
        DocumentAlias(
            alias=f"bulk-{label}",
            file_id=FileId(f"{bulk_database.user.id}-{label}"),
            created_by=bulk_database.user.id,
            retired_at=datetime.now(UTC) if label == "retired" else None,
        )
        for label in labels
    ]
    async with bulk_database.sessions() as db:
        db.add_all(rows)
        await db.commit()
    records: dict[FileId, JsonValue | BaseException] = {
        row.file_id: {
            "id": row.file_id,
            "name": "Current 日本語",
            "mimeType": MIME_DOC,
            "driveId": "shared",
            "description": "Unused description",
            "capabilities": {"canEdit": True},
        }
        for row in rows
    }
    records[rows[1].file_id] = bulk_http_error(403)
    records[rows[2].file_id] = bulk_http_error(404)
    records[rows[3].file_id] = {
        "id": rows[3].file_id,
        "name": "Outside",
        "mimeType": MIME_DOC,
        "driveId": "foreign",
    }
    records[rows[4].file_id] = {"id": "different", "name": "Wrong", "mimeType": MIME_DOC}
    records[rows[5].file_id] = {
        "id": rows[5].file_id,
        "name": "Trashed",
        "mimeType": MIME_DOC,
        "trashed": True,
    }
    provider = BulkProvider(records)
    drive = BulkDrive()
    statements: list[str] = []

    def record(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    request = RecentDocumentAliasesRequest(
        aliases=[
            rows[1].alias,
            rows[0].alias,
            "bulk-absent",
            *[row.alias for row in rows[2:]],
            rows[0].alias,
        ]
    )
    async with bulk_database.sessions() as db:
        service = PostgresAliasService(
            db=db,
            files=ReleasedAliasFiles(provider, drive, bulk_database, db),
            user=bulk_database.user,
            drive=drive,
        )
        event.listen(bulk_database.engine.sync_engine, "before_cursor_execute", record)
        try:
            result = await service.resolve_recent(request.aliases)
        finally:
            event.remove(bulk_database.engine.sync_engine, "before_cursor_execute", record)
        assert [item.model_dump() for item in result] == [
            {
                "alias": "bulk-visible",
                "file_id": rows[0].file_id,
                "name": "Current 日本語",
                "mime_type": MIME_DOC,
                "drive_name": "Shared",
            }
        ]
        assert (
            len([statement for statement in statements if "FROM document_aliases" in statement])
            == 1
        )
        assert len(provider.requests) == 6
        assert all(
            "description" not in request.fields and "capabilities" not in request.fields
            for request in provider.requests
        )
        provider.records[rows[0].file_id] = bulk_http_error(403)
        assert await service.resolve_recent(request.aliases) == []
        assert len(provider.requests) == 12
        assert not db.in_transaction()
