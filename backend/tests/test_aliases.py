from __future__ import annotations

from typing import Self, override
import uuid

from pydantic import ValidationError
import pytest

from app.aliases.errors import (
    AliasConflictError,
    AliasEditForbiddenError,
    AliasNotFoundError,
    AliasTargetUnsupportedError,
)
from app.aliases.schemas import DocumentAliasWrite
from app.aliases.service import PostgresAliasService
from app.document_transform.naming import polish_alias_candidates
from app.drive.schemas import DriveSummary
from app.files.errors import FileNotInScopeError
from app.files.schemas import FileResponse
from app.identifiers import DriveId, FileId, GoogleUserId, UserId
from app.models.user import User
from app.models.wiki import DocumentAlias
from tests.support import dependency


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
        inaccessible_ids: set[FileId] | None = None,
    ) -> None:
        self.accessible = accessible
        self.editable = editable
        self.mime_type = mime_type
        self.inaccessible_ids = inaccessible_ids or set()
        self.get_calls: list[FileId] = []
        self.bulk_calls: list[list[FileId]] = []
        self.can_edit_calls = 0

    async def get(self, file_id: FileId) -> FileResponse:
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

    async def can_edit(self, _file_id: FileId) -> bool:
        self.can_edit_calls += 1
        return self.editable

    async def require_access(self, file_id: FileId) -> None:
        await self.get(file_id)

    async def get_accessible_many(self, file_ids: list[FileId]) -> dict[FileId, FileResponse]:
        self.bulk_calls.append(file_ids)
        accessible: dict[FileId, FileResponse] = {}
        for file_id in file_ids:
            try:
                accessible[file_id] = await self.get(file_id)
            except FileNotInScopeError:
                continue
        return accessible

    async def get_search_metadata_many(self, file_ids: list[FileId]) -> dict[FileId, FileResponse]:
        return await self.get_accessible_many(file_ids)


class _Result:
    def __init__(
        self,
        row: DocumentAlias | None = None,
        rows: list[DocumentAlias] | None = None,
    ) -> None:
        self.row = row
        self.rows = rows or []

    def scalar_one_or_none(self) -> DocumentAlias | None:
        return self.row

    def one_or_none(self) -> DocumentAlias | None:
        return self.row

    def all(self) -> list[tuple[str, FileId]]:
        return [(row.alias, row.file_id) for row in self.rows]

    def scalars(self) -> Self:
        return self

    def tuples(self) -> Self:
        return self


class _Session:
    def __init__(
        self,
        *,
        active: DocumentAlias | None = None,
        by_alias: DocumentAlias | None = None,
        inserted: DocumentAlias | None = None,
        recent_rows: list[DocumentAlias] | None = None,
    ) -> None:
        self.active = active
        self.by_alias = by_alias
        self.inserted = inserted
        self.recent_rows = recent_rows
        self.execute_calls = 0
        self.statements: list[object] = []
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0

    async def get(self, _model: type[DocumentAlias], _key: str) -> DocumentAlias | None:
        return self.by_alias

    async def execute(self, _statement: object, _parameters: object | None = None) -> _Result:
        self.execute_calls += 1
        self.statements.append(_statement)
        if isinstance(_parameters, dict) and "alias" in _parameters:
            row = self.by_alias
            return _Result(row if row is not None and row.retired_at is None else None)
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
    user.id = UserId(uuid.uuid7())
    user.email = "editor@sarj.ai"
    user.google_id = GoogleUserId("google-user")
    user.token_version = 0
    return user


class _Drive:
    async def list_drives(self) -> list[DriveSummary]:
        return [DriveSummary(id=DriveId("drive-1"), name="Engineering")]


class _EnsureSession(_Session):
    def __init__(self, responses: list[DocumentAlias | None]) -> None:
        super().__init__()
        self.responses = iter(responses)

    @override
    async def execute(self, _statement: object, _parameters: object | None = None) -> _Result:
        self.execute_calls += 1
        return _Result(next(self.responses))


def _alias_row(alias: str = "chosen-link") -> DocumentAlias:
    row = DocumentAlias()
    row.alias = alias
    row.file_id = FileId("file-1")
    return row


@pytest.mark.asyncio
async def test_ensure_alias_preserves_existing_choice() -> None:
    row = _alias_row()
    session = _EnsureSession([row])
    result = await _service(session, _Files()).ensure_for_file(FileId("file-1"), ("auto-link",))
    assert result.alias == "chosen-link"
    assert session.execute_calls == 1
    assert session.commits == session.flushes == session.rollbacks == 0
    assert row.retired_at is None


@pytest.mark.asyncio
async def test_ensure_polish_alias_derives_deterministic_candidates_from_fresh_file() -> None:
    expected = polish_alias_candidates(FileId("file-1"), "Document")[0]
    session = _EnsureSession([None, _alias_row(expected)])
    files = _Files()
    service = _service(session, files)

    result = await service.ensure_polish_for_file(FileId("file-1"))

    assert result.file_id == "file-1"
    assert result.alias == expected
    assert files.get_calls == ["file-1"]


@pytest.mark.asyncio
async def test_ensure_alias_reuses_concurrent_winner_without_retiring_it() -> None:
    row = _alias_row()
    session = _EnsureSession([None, None, row])
    result = await _service(session, _Files()).ensure_for_file(FileId("file-1"), ("auto-link",))
    assert result.alias == "chosen-link"
    assert session.commits == session.flushes == session.rollbacks == 0
    assert row.retired_at is None


@pytest.mark.asyncio
async def test_ensure_alias_tries_next_candidate_after_reserved_alias_collision() -> None:
    session = _EnsureSession([None, None, None, _alias_row("second-link")])
    result = await _service(session, _Files()).ensure_for_file(
        FileId("file-1"), ("first-link", "second-link")
    )
    assert result.alias == "second-link"
    assert session.execute_calls == 4
    assert session.commits == 1
    assert session.flushes == session.rollbacks == 0


@pytest.mark.asyncio
async def test_ensure_alias_exhaustion_does_not_retire_anything() -> None:
    session = _EnsureSession([None, None, None])
    with pytest.raises(AliasConflictError):
        await _service(session, _Files()).ensure_for_file(FileId("file-1"), ("auto-link",))
    assert session.commits == session.flushes == session.rollbacks == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(("editable", "accessible"), [(False, True), (True, False)])
async def test_ensure_alias_checks_edit_and_scope_before_database_access(
    *, editable: bool, accessible: bool
) -> None:
    session = _EnsureSession([])
    with pytest.raises((AliasEditForbiddenError, FileNotInScopeError)):
        await _service(session, _Files(editable=editable, accessible=accessible)).ensure_for_file(
            FileId("file-1"), ("auto-link",)
        )
    assert session.execute_calls == 0


def _service(session: _Session, files: _Files) -> PostgresAliasService:
    return PostgresAliasService(
        db=dependency(session),
        files=dependency(files),
        drive=dependency(_Drive()),
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


async def test_save_reuses_edit_capability_from_initial_file_fetch() -> None:
    files = _Files()
    service = _service(
        _Session(inserted=DocumentAlias(alias="eng-onboarding", file_id="file-1")),
        files,
    )

    await service.save(FileId("file-1"), "eng-onboarding")

    assert files.get_calls == ["file-1"]
    assert files.can_edit_calls == 0


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

    sql = str(session.statements[0])
    assert "UPDATE document_aliases" in sql
    assert "document_aliases.file_id" in sql
    assert "document_aliases.retired_at IS NULL" in sql
    assert session.commits == 1

    empty_session = _Session()
    await _service(empty_session, _Files()).retire(FileId("file-1"))
    assert empty_session.execute_calls == 1
    assert empty_session.commits == 0


async def test_recent_bulk_resolves_once_and_omits_inaccessible_items() -> None:
    accessible_alias = DocumentAlias(
        alias="eng-onboarding", file_id="file-1", created_by=_user().id
    )
    inaccessible_alias = DocumentAlias(
        alias="people-org-chart", file_id="file-2", created_by=_user().id
    )
    files = _Files(inaccessible_ids={FileId("file-2")})

    result = await _service(
        _Session(recent_rows=[accessible_alias, inaccessible_alias]), files
    ).resolve_recent(["people-org-chart", "eng-onboarding", "eng-onboarding"])

    assert [item.model_dump() for item in result] == [
        {
            "alias": "eng-onboarding",
            "file_id": "file-1",
            "name": "Document",
            "mime_type": "application/vnd.google-apps.document",
            "drive_name": "Engineering",
        }
    ]
    assert files.bulk_calls == [["file-2", "file-1"]]
    assert files.get_calls == ["file-2", "file-1"]
