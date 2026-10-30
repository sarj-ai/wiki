from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import NamedTuple, override
import uuid

from pydantic import BaseModel
import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.aliases.schemas import DocumentAliasForFile, DocumentAliasRead, DocumentAliasValue
from app.drive.schemas import MIME_DOC, MIME_FOLDER, DriveNode, DriveTree
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
from app.extras.service import ExtrasService
from app.files.errors import DriveUpstreamError
from app.files.schemas import DeleteResult, FileResponse, MoveResult
from app.files.service import FilesService
from app.identifiers import DriveId, FileId, IdempotencyKey, TaskId
from app.sidebar.cache import CachedDriveTree, SidebarCacheService
from app.smart_create.creation import DriveSmartFileCreationService
from app.smart_create.errors import TemplateChangedError
from app.smart_create.router import invalidate_smart_create_result
from app.smart_create.schemas import (
    BlankSource,
    SmartCreateResult,
    SmartFileCreate,
    TemplateSource,
)
from app.tasks.schemas import (
    SmartCreateTaskRecord,
    TaskClaim,
    TaskKind,
    TaskRead,
    TaskReservation,
)
from app.tasks.service import TaskService
from app.templates.schemas import TemplateCatalogRead, TemplateMimeType, TemplateRead
from app.templates.service import TemplateCatalogService
from tests.alias_support import StrictAliasService


_DOC_MIME: TemplateMimeType = "application/vnd.google-apps.document"


def _file(
    file_id: str,
    *,
    mime_type: str = MIME_DOC,
    parent_id: str | None = "folder",
    can_add_children: bool = False,
) -> FileResponse:
    return FileResponse(
        id=FileId(file_id),
        name=file_id,
        mime_type=mime_type,
        parent_folder_id=FileId(parent_id) if parent_id else None,
        drive_id=DriveId("drive"),
        modified_time="2026-08-02T00:00:00Z",
        can_edit=True,
        can_add_children=can_add_children,
    )


class _Files(FilesService):
    def __init__(self, *, recovered: FileResponse | None = None) -> None:
        self.created = 0
        self.recovered = recovered

    @override
    async def create(self, parent_folder_id: FileId, name: str, mime_type: str) -> FileResponse:
        raise NotImplementedError

    @override
    async def create_tracked(
        self, parent_folder_id: FileId, name: str, mime_type: str, task_id: TaskId
    ) -> FileResponse:
        self.created += 1
        return _file("created")

    @override
    async def copy_template(
        self,
        *,
        template_file_id: FileId,
        parent_folder_id: FileId,
        name: str,
        task_id: TaskId,
        expected_modified_time: str,
    ) -> FileResponse:
        self.created += 1
        return _file("copied")

    @override
    async def find_by_task(self, drive_id: DriveId, task_id: TaskId) -> FileResponse | None:
        return self.recovered

    @override
    async def get(self, file_id: FileId) -> FileResponse:
        if file_id == FileId("folder"):
            return _file(
                "folder",
                mime_type=MIME_FOLDER,
                parent_id=None,
                can_add_children=True,
            )
        return _file(str(file_id))

    @override
    async def can_edit(self, file_id: FileId) -> bool:
        return True

    @override
    async def get_html_content(self, file_id: FileId) -> bytes:
        raise NotImplementedError

    @override
    async def rename(self, file_id: FileId, new_name: str) -> FileResponse:
        raise NotImplementedError

    @override
    async def move(self, file_id: FileId, new_parent_folder_id: FileId) -> MoveResult:
        raise NotImplementedError

    @override
    async def copy(self, file_id: FileId, new_name: str) -> FileResponse:
        raise NotImplementedError

    @override
    async def delete(self, file_id: FileId) -> DeleteResult:
        raise NotImplementedError


class _Aliases(StrictAliasService):
    saved: DocumentAliasValue | None = None

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    @override
    async def get_for_file(self, file_id: FileId) -> DocumentAliasForFile:
        raise NotImplementedError

    @override
    async def resolve(self, alias: DocumentAliasValue) -> DocumentAliasRead:
        raise NotImplementedError

    @override
    async def save(self, file_id: FileId, alias: DocumentAliasValue) -> DocumentAliasRead:
        if self._error is not None:
            raise self._error
        self.saved = alias
        return DocumentAliasRead(alias=alias, file_id=file_id)

    @override
    async def retire(self, file_id: FileId) -> None:
        raise NotImplementedError


class _Extras(ExtrasService):
    icon: str | None = None

    @override
    async def list_doc_icons(self) -> list[DocIconRead]:
        return []

    @override
    async def upsert_doc_icon(self, drive_id: FileId, payload: DocIconWrite) -> DocIconRead:
        self.icon = payload.icon
        return DocIconRead(drive_id=drive_id, icon=payload.icon, cover_url=None)

    @override
    async def list_folder_icons(self) -> list[FolderIconRead]:
        return []

    @override
    async def upsert_folder_icon(
        self, folder_drive_id: FileId, payload: FolderIconWrite
    ) -> FolderIconRead:
        raise NotImplementedError

    @override
    async def list_sort_orders(self) -> list[SortOrderRead]:
        return []

    @override
    async def upsert_sort_orders(self, items: list[SortOrderWrite]) -> None:
        raise NotImplementedError

    @override
    async def get_preferences(self) -> PreferencesRead:
        raise NotImplementedError

    @override
    async def upsert_preferences(self, payload: PreferencesWrite) -> PreferencesRead:
        raise NotImplementedError


class _Tasks(TaskService):
    resource: FileId | None = None
    result_file: FileId | None = None
    result: BaseModel | None = None

    @override
    async def create_or_replay(
        self,
        *,
        kind: TaskKind,
        owner_user_id: uuid.UUID,
        idempotency_key: IdempotencyKey,
        payload: BaseModel,
        admit: Callable[[], Awaitable[None]] | None = None,
        admit_new: Callable[[], None] | None = None,
        admit_terminal_replay: Callable[[TaskRead], Awaitable[bool]] | None = None,
    ) -> TaskReservation:
        del admit_terminal_replay
        raise NotImplementedError

    @override
    async def claim(self, task_id: TaskId, *, lease_duration: timedelta) -> TaskClaim:
        raise NotImplementedError

    @override
    async def defer(self, task_id: TaskId, *, lease_token: uuid.UUID) -> TaskRead:
        raise NotImplementedError

    @override
    async def record_resource(
        self, task_id: TaskId, *, lease_token: uuid.UUID, resource_id: FileId
    ) -> None:
        self.resource = resource_id

    @override
    async def succeed(
        self, task_id: TaskId, *, lease_token: uuid.UUID, result: BaseModel
    ) -> TaskRead:
        self.result = result
        if isinstance(result, SmartCreateResult):
            self.result_file = result.file.id
        raise _SucceededError

    @override
    async def fail(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        error_code: str,
        error_detail: str | None,
    ) -> TaskRead:
        raise NotImplementedError

    @override
    async def discard_pending(self, task_id: TaskId, *, owner_user_id: uuid.UUID) -> bool:
        raise NotImplementedError

    @override
    async def cancel(self, task_id: TaskId, *, owner_user_id: uuid.UUID) -> TaskRead:
        raise NotImplementedError

    @override
    async def get_for_owner(self, task_id: TaskId, owner_user_id: uuid.UUID) -> TaskRead:
        raise NotImplementedError

    @override
    async def load_smart_create(self, task_id: TaskId) -> SmartCreateTaskRecord:
        raise NotImplementedError


class _SucceededError(Exception):
    pass


class _Templates(TemplateCatalogService):
    def __init__(self, templates: list[TemplateRead] | None = None) -> None:
        self.templates = templates or []

    @override
    async def list_templates(self, *, refresh: bool = False) -> TemplateCatalogRead:
        return TemplateCatalogRead(templates=self.templates)


class _Database(AsyncSession):
    def __init__(self) -> None:
        self.rollbacks = 0

    async def rollback(self) -> None:
        self.rollbacks += 1


class _SidebarCache(SidebarCacheService):
    invalidated: DriveId | None = None

    @override
    async def get_drive_tree(self, db: AsyncSession, drive_id: DriveId) -> CachedDriveTree:
        raise NotImplementedError

    @override
    async def peek_drive_tree(self, db: AsyncSession, drive_id: DriveId) -> CachedDriveTree | None:
        raise NotImplementedError

    @override
    async def get_folder_children(self, folder_id: FileId) -> list[DriveNode]:
        raise NotImplementedError

    @override
    async def invalidate_drive(self, db: AsyncSession, drive_id: DriveId) -> None:
        self.invalidated = drive_id

    @override
    async def refresh_drive(self, db: AsyncSession, drive_id: DriveId) -> DriveTree:
        raise NotImplementedError


class _ServiceFixture(NamedTuple):
    service: DriveSmartFileCreationService
    aliases: _Aliases
    extras: _Extras


def _service(
    files: _Files,
    tasks: _Tasks,
    templates: _Templates | None = None,
    *,
    aliases: _Aliases | None = None,
    db: _Database | None = None,
) -> _ServiceFixture:
    alias_service = aliases or _Aliases()
    extras = _Extras()
    return _ServiceFixture(
        DriveSmartFileCreationService(
            files=files,
            aliases=alias_service,
            extras=extras,
            tasks=tasks,
            templates=templates or _Templates(),
            db=db or _Database(),
        ),
        alias_service,
        extras,
    )


async def test_blank_creation_records_resource_and_enriches() -> None:
    files = _Files()
    tasks = _Tasks()
    service, aliases, extras = _service(files, tasks)
    payload = SmartFileCreate(
        source=BlankSource(kind="blank", file_type="document"),
        parent_folder_id=FileId("folder"),
        name="PRD",
        emoji="📝",
        alias="product-requirements",
    )
    with pytest.raises(_SucceededError):
        await service.execute(
            task_id=TaskId(uuid.uuid7()),
            lease_token=uuid.uuid7(),
            payload=payload,
            existing_resource=None,
        )
    assert files.created == 1
    assert tasks.resource == FileId("created")
    assert aliases.saved == "product-requirements"
    assert extras.icon == "📝"


async def test_recovery_does_not_create_second_file() -> None:
    files = _Files(recovered=_file("already-created"))
    tasks = _Tasks()
    service, _, _ = _service(files, tasks)
    payload = SmartFileCreate(
        source=BlankSource(kind="blank", file_type="document"),
        parent_folder_id=FileId("folder"),
        name="PRD",
        emoji="📝",
        alias="product-requirements",
    )
    with pytest.raises(_SucceededError):
        await service.execute(
            task_id=TaskId(uuid.uuid7()),
            lease_token=uuid.uuid7(),
            payload=payload,
            existing_resource=None,
        )
    assert files.created == 0
    assert tasks.resource is None
    assert tasks.result_file == FileId("already-created")


async def test_changed_template_is_rejected_before_copy() -> None:
    files = _Files()
    tasks = _Tasks()
    template = TemplateRead(
        file_id=FileId("template"),
        drive_id=DriveId("drive"),
        drive_name="Product",
        name="PRD",
        description=None,
        mime_type=_DOC_MIME,
        modified_time="new-version",
        category_path=[],
    )
    service, _, _ = _service(files, tasks, _Templates([template]))
    payload = SmartFileCreate(
        source=TemplateSource(
            kind="template",
            template_file_id=template.file_id,
            template_drive_id=template.drive_id,
            template_modified_time="old-version",
            mime_type=_DOC_MIME,
        ),
        parent_folder_id=FileId("folder"),
        name="PRD",
        emoji="📝",
        alias="product-requirements",
    )
    with pytest.raises(TemplateChangedError):
        await service.execute(
            task_id=TaskId(uuid.uuid7()),
            lease_token=uuid.uuid7(),
            payload=payload,
            existing_resource=None,
        )
    assert files.created == 0


async def test_alias_database_failure_rolls_back_and_succeeds_with_warning() -> None:
    files = _Files()
    tasks = _Tasks()
    db = _Database()
    service, _, _ = _service(
        files,
        tasks,
        aliases=_Aliases(SQLAlchemyError("database unavailable")),
        db=db,
    )
    payload = SmartFileCreate(
        source=BlankSource(kind="blank", file_type="document"),
        parent_folder_id=FileId("folder"),
        name="PRD",
        emoji="📝",
        alias="product-requirements",
    )

    with pytest.raises(_SucceededError):
        await service.execute(
            task_id=TaskId(uuid.uuid7()),
            lease_token=uuid.uuid7(),
            payload=payload,
            existing_resource=None,
        )

    assert db.rollbacks == 1
    assert tasks.result_file == FileId("created")
    assert isinstance(tasks.result, SmartCreateResult)
    assert [warning.code for warning in tasks.result.warnings] == ["alias_save_failed"]


async def test_alias_drive_failure_is_a_warning_after_file_creation() -> None:
    files = _Files()
    tasks = _Tasks()
    service, _, _ = _service(
        files,
        tasks,
        aliases=_Aliases(DriveUpstreamError(502)),
    )
    payload = SmartFileCreate(
        source=BlankSource(kind="blank", file_type="document"),
        parent_folder_id=FileId("folder"),
        name="PRD",
        emoji="📝",
        alias="product-requirements",
    )

    with pytest.raises(_SucceededError):
        await service.execute(
            task_id=TaskId(uuid.uuid7()),
            lease_token=uuid.uuid7(),
            payload=payload,
            existing_resource=None,
        )

    assert isinstance(tasks.result, SmartCreateResult)
    assert [warning.code for warning in tasks.result.warnings] == ["drive_upstream"]


async def test_successful_smart_creation_invalidates_sidebar_drive() -> None:
    cache = _SidebarCache()
    result = SmartCreateResult(
        task_id=TaskId(uuid.uuid7()),
        file=_file("created"),
        warnings=[],
    )

    await invalidate_smart_create_result(_Database(), cache, result)

    assert cache.invalidated == DriveId("drive")
