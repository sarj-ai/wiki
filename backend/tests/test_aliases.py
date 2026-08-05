from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
import uuid

from pydantic import ValidationError
import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.aliases.errors import (
    AliasConflictError,
    AliasEditForbiddenError,
    AliasNotFoundError,
    AliasTargetUnsupportedError,
)
from app.aliases.schemas import DocumentAliasRead, DocumentAliasWrite
from app.aliases.service import PostgresAliasService
from app.drive.schemas import DriveSummary
from app.drive.service import DriveService
from app.files.errors import FileNotInScopeError
from app.files.schemas import FileResponse
from app.files.service import FilesService
from app.identifiers import DriveId, FileId, GoogleUserId
from app.models.user import User
from app.models.wiki import DocumentAlias, DocumentAliasVisit


@pytest.mark.parametrize(
    "alias",
    ["eng-onboarding", "org-chart", "people-org-chart"],
)
def test_alias_schema_accepts_exact_canonical_format(alias: str) -> None:
    assert DocumentAliasWrite(alias=alias).alias == alias


@pytest.mark.parametrize(
    "alias",
    [
        "onboarding",
        "ENG-onboarding",
        "eng-Onboarding",
        "eng_onboarding",
        "eng--onboarding",
        "eng-onboarding-",
        " eng-onboarding",
        "eng-onboarding ",
        "eng-onboarding-guide-extra",
        "q3-roadmap",
        "éng-onboarding",
        "%65ng-onboarding",
    ],
)
def test_alias_schema_rejects_noncanonical_and_google_unsafe_shapes(alias: str) -> None:
    with pytest.raises(ValidationError):
        DocumentAliasWrite(alias=alias)


class _Files:
    def __init__(
        self,
        *,
        accessible: bool = True,
        editable: bool = True,
        mime_type: str = "application/vnd.google-apps.document",
        inaccessible_ids: set[str] | None = None,
    ) -> None:
        self.accessible = accessible
        self.editable = editable
        self.mime_type = mime_type
        self.inaccessible_ids = inaccessible_ids or set()
        self.get_calls: list[str] = []

    async def get(self, file_id: str) -> FileResponse:
        self.get_calls.append(file_id)
        if not self.accessible or file_id in self.inaccessible_ids:
            msg = "File not available in Sarj Wiki"
            raise FileNotInScopeError(msg)
        return FileResponse(
            can_edit=self.editable,
            drive_id=DriveId("drive-1"),
            id=FileId(file_id),
            mime_type=self.mime_type,
            modified_time=None,
            name="Document",
            parent_folder_id=None,
        )

    async def can_edit(self, _file_id: str) -> bool:
        return self.editable


class _Result:
    def __init__(
        self,
        row: DocumentAlias | None = None,
        rows: list[tuple[DocumentAliasVisit, DocumentAlias]] | None = None,
    ) -> None:
        self.row = row
        self.rows = rows or []

    def scalar_one_or_none(self) -> DocumentAlias | None:
        return self.row

    def all(self) -> list[tuple[DocumentAliasVisit, DocumentAlias]]:
        return self.rows


class _Session:
    def __init__(
        self,
        *,
        active: DocumentAlias | None = None,
        by_alias: DocumentAlias | None = None,
        inserted: DocumentAlias | None = None,
        recent_rows: list[tuple[DocumentAliasVisit, DocumentAlias]] | None = None,
        execute_error: SQLAlchemyError | None = None,
    ) -> None:
        self.active = active
        self.by_alias = by_alias
        self.inserted = inserted
        self.recent_rows = recent_rows
        self.execute_error = execute_error
        self.execute_calls = 0
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0

    async def get(self, _model: type[DocumentAlias], _key: str) -> DocumentAlias | None:
        return self.by_alias

    async def execute(self, _statement: object) -> _Result:
        self.execute_calls += 1
        if self.execute_error is not None:
            raise self.execute_error
        if self.recent_rows is not None:
            return _Result(rows=self.recent_rows)
        if self.execute_calls == 1:
            return _Result(self.active)
        return _Result(self.inserted)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def flush(self) -> None:
        self.flushes += 1


def _user() -> User:
    user = User()
    user.id = uuid.uuid4()
    user.email = "editor@sarj.ai"
    user.google_id = GoogleUserId("google-user")
    user.token_version = 0
    return user


class _Drive:
    async def list_drives(self) -> list[DriveSummary]:
        return [DriveSummary(id=DriveId("drive-1"), name="Engineering")]


def _service(session: _Session, files: _Files) -> PostgresAliasService:
    return PostgresAliasService(
        db=cast("AsyncSession", session),
        files=cast("FilesService", files),
        drive=cast("DriveService", _Drive()),
        user=_user(),
    )


async def test_resolve_rechecks_current_drive_access() -> None:
    row = DocumentAlias(alias="eng-onboarding", file_id="file-1", created_by=_user().id)
    service = _service(_Session(by_alias=row), _Files(accessible=False))

    with pytest.raises(AliasNotFoundError):
        await service.resolve("eng-onboarding")


async def test_rename_retires_old_alias_and_creates_new_claim_atomically() -> None:
    old = DocumentAlias(alias="eng-onboarding", file_id="file-1", created_by=_user().id)
    new = DocumentAlias(alias="people-org-chart", file_id="file-1", created_by=_user().id)
    session = _Session(active=old, inserted=new)
    service = _service(session, _Files())

    result = await service.save(FileId("file-1"), "people-org-chart")

    assert result.alias == "people-org-chart"
    assert old.retired_at is not None
    assert old.retired_by is not None
    assert session.commits == 1
    assert session.flushes == 1


async def test_unique_constraint_conflict_rolls_back_without_losing_old_alias() -> None:
    old = DocumentAlias(alias="eng-onboarding", file_id="file-1", created_by=_user().id)
    session = _Session(active=old)
    service = _service(session, _Files())

    with pytest.raises(AliasConflictError):
        await service.save(FileId("file-1"), "people-org-chart")

    assert session.rollbacks == 1


async def test_saving_same_alias_is_idempotent() -> None:
    old = DocumentAlias(alias="eng-onboarding", file_id="file-1", created_by=_user().id)
    session = _Session(active=old)

    result = await _service(session, _Files()).save(FileId("file-1"), "eng-onboarding")

    assert result.alias == "eng-onboarding"
    assert session.commits == 0


async def test_read_only_user_cannot_claim_an_alias() -> None:
    service = _service(_Session(), _Files(editable=False))

    with pytest.raises(AliasEditForbiddenError):
        await service.save(FileId("file-1"), "eng-onboarding")


async def test_folder_cannot_receive_a_document_alias() -> None:
    service = _service(
        _Session(),
        _Files(mime_type="application/vnd.google-apps.folder"),
    )

    with pytest.raises(AliasTargetUnsupportedError):
        await service.save(FileId("folder-1"), "eng-onboarding")


async def test_retire_is_idempotent_and_preserves_tombstone() -> None:
    row = DocumentAlias(alias="eng-onboarding", file_id="file-1", created_by=_user().id)
    session = _Session(active=row)
    service = _service(session, _Files())

    await service.retire(FileId("file-1"))

    assert row.retired_at is not None
    assert row.retired_by is not None
    assert session.commits == 1

    empty_session = _Session()
    await _service(empty_session, _Files()).retire(FileId("file-1"))
    assert empty_session.commits == 0


async def test_record_visit_rechecks_access_before_writing() -> None:
    row = DocumentAlias(alias="eng-onboarding", file_id="file-1", created_by=_user().id)
    session = _Session(by_alias=row)
    service = _service(session, _Files(accessible=False))

    with pytest.raises(AliasNotFoundError):
        await service.record_visit("eng-onboarding")

    assert session.execute_calls == 0
    assert session.commits == 0


async def test_record_visit_is_best_effort_and_returns_resolved_alias() -> None:
    row = DocumentAlias(alias="eng-onboarding", file_id="file-1", created_by=_user().id)
    session = _Session(by_alias=row, execute_error=SQLAlchemyError("database unavailable"))

    result = await _service(session, _Files()).record_visit("eng-onboarding")

    assert result == DocumentAliasRead(alias="eng-onboarding", file_id=FileId("file-1"))
    assert session.rollbacks == 1


async def test_record_visit_upserts_prunes_and_commits() -> None:
    row = DocumentAlias(alias="eng-onboarding", file_id="file-1", created_by=_user().id)
    session = _Session(by_alias=row)

    await _service(session, _Files()).record_visit("eng-onboarding")

    assert session.execute_calls == 3
    assert session.commits == 1


async def test_recent_rechecks_each_file_and_omits_inaccessible_items() -> None:
    now = datetime.now(UTC)
    accessible_alias = DocumentAlias(
        alias="eng-onboarding", file_id="file-1", created_by=_user().id
    )
    inaccessible_alias = DocumentAlias(
        alias="people-org-chart", file_id="file-2", created_by=_user().id
    )
    visits = [
        (
            DocumentAliasVisit(user_id=_user().id, file_id="file-2", visited_at=now),
            inaccessible_alias,
        ),
        (
            DocumentAliasVisit(
                user_id=_user().id,
                file_id="file-1",
                visited_at=now - timedelta(minutes=1),
            ),
            accessible_alias,
        ),
    ]
    files = _Files(inaccessible_ids={"file-2"})

    result = await _service(_Session(recent_rows=visits), files).recent()

    assert [item.model_dump() for item in result] == [
        {
            "alias": "eng-onboarding",
            "file_id": "file-1",
            "name": "Document",
            "mime_type": "application/vnd.google-apps.document",
            "drive_name": "Engineering",
            "last_visited_at": now - timedelta(minutes=1),
        }
    ]
    assert files.get_calls == ["file-2", "file-1"]
