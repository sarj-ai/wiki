from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
from typing import Protocol
import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.google_tokens import (
    GoogleReauthenticationRequiredError,
    GoogleTokenManager,
    GoogleTokenUnavailableError,
)
from app.cache.service import CacheService
from app.document_transform.application import TransformSubmission, submit_document_transform
from app.document_transform.qa_contract import (
    QA_MAX_FOLDER_LIST_PAGES,
    QA_MAX_TRAVERSED_FOLDERS,
)
from app.document_transform.qa_grants import QaAuthority, QaGrantError
from app.document_transform.schemas import (
    DocumentTransformRequest,
    DriveFileSource,
    OutputStrategy,
)
from app.drive.errors import DriveTraversalIncompleteError
from app.drive.schemas import MIME_DOC, MIME_FOLDER, DriveNode
from app.drive.service import GoogleDriveService
from app.files.errors import FilesError
from app.files.schemas import FileResponse
from app.files.service import GoogleFilesService
from app.identifiers import DriveId, FileId, IdempotencyKey, QaPlanId
from app.models.user import User
from app.tasks.errors import TaskError
from app.tasks.queue import TaskEnqueuer
from app.tasks.schemas import TaskRead
from app.tasks.service import TaskService


_MAX_CANARY_PLAN_CHILDREN = 1_000


@dataclass(frozen=True, slots=True)
class QaCanaryPlan:
    plan_id: QaPlanId
    idempotency_key: IdempotencyKey
    source_file_id: FileId
    source_name: str
    source_modified_time: str
    output_strategy: OutputStrategy


@dataclass(frozen=True, slots=True)
class _QaBrokerServices:
    drive: GoogleDriveService
    files: GoogleFilesService


class QaDriveReader(Protocol):
    async def list_folder_children_bounded(
        self,
        folder_id: FileId,
        *,
        max_pages: int,
        max_children: int,
    ) -> list[DriveNode]: ...


class QaFileReader(Protocol):
    async def get(self, file_id: FileId) -> FileResponse: ...


class QaCanaryBroker:
    def __init__(self, *, db: AsyncSession, http: httpx.AsyncClient, cache: CacheService) -> None:
        self._db = db
        self._http = http
        self._cache = cache

    async def plan(self, authority: QaAuthority) -> QaCanaryPlan:
        services = await self._services(authority)
        try:
            plan = await plan_qa_canary(authority, drive=services.drive, files=services.files)
        except (
            DriveTraversalIncompleteError,
            FilesError,
            NotImplementedError,
            ValueError,
        ) as exc:
            raise QaGrantError from exc
        return plan

    async def submit(
        self,
        authority: QaAuthority,
        *,
        plan_id: QaPlanId,
        tasks: TaskService,
        enqueuer: TaskEnqueuer,
    ) -> TransformSubmission:
        services = await self._services(authority)
        try:
            plan = await plan_qa_canary(authority, drive=services.drive, files=services.files)
        except (
            DriveTraversalIncompleteError,
            FilesError,
            NotImplementedError,
            ValueError,
        ) as exc:
            raise QaGrantError from exc
        try:
            return await _submit_qa_canary_plan(
                authority,
                plan=plan,
                plan_id=plan_id,
                files=services.files,
                tasks=tasks,
                enqueuer=enqueuer,
            )
        except (
            FilesError,
            GoogleReauthenticationRequiredError,
            GoogleTokenUnavailableError,
        ) as exc:
            raise QaGrantError from exc

    async def read_task(
        self,
        authority: QaAuthority,
        *,
        plan_id: QaPlanId,
        tasks: TaskService,
    ) -> TaskRead:
        services = await self._services(authority)
        try:
            plan = await plan_qa_canary(authority, drive=services.drive, files=services.files)
            return await read_planned_qa_canary_task(
                authority,
                plan=plan,
                plan_id=plan_id,
                tasks=tasks,
            )
        except (
            DriveTraversalIncompleteError,
            FilesError,
            NotImplementedError,
            TaskError,
            ValueError,
        ) as exc:
            raise QaGrantError from exc

    async def _services(self, authority: QaAuthority) -> _QaBrokerServices:
        user = await self._db.get(User, authority.user_id)
        if user is None or user.email.casefold() != authority.user_email.casefold():
            raise QaGrantError
        try:
            prepared = await GoogleTokenManager(self._db, self._http).prepare(user)
            drive = GoogleDriveService(
                prepared.user,
                self._cache,
                refresh_handler=prepared.refresh_handler,
            )
            files = GoogleFilesService(user=prepared.user, drive=drive)
        except (
            DriveTraversalIncompleteError,
            FilesError,
            GoogleReauthenticationRequiredError,
            GoogleTokenUnavailableError,
            NotImplementedError,
            ValueError,
        ) as exc:
            raise QaGrantError from exc
        return _QaBrokerServices(drive=drive, files=files)


async def submit_planned_qa_canary(
    authority: QaAuthority,
    *,
    plan_id: QaPlanId,
    drive: QaDriveReader,
    files: QaFileReader,
    tasks: TaskService,
    enqueuer: TaskEnqueuer,
) -> TransformSubmission:
    plan = await plan_qa_canary(authority, drive=drive, files=files)
    return await _submit_qa_canary_plan(
        authority,
        plan=plan,
        plan_id=plan_id,
        files=files,
        tasks=tasks,
        enqueuer=enqueuer,
    )


async def read_planned_qa_canary_task(
    authority: QaAuthority,
    *,
    plan: QaCanaryPlan,
    plan_id: QaPlanId,
    tasks: TaskService,
) -> TaskRead:
    if not hmac.compare_digest(plan.plan_id.bytes, plan_id.bytes):
        raise QaGrantError
    admission = await tasks.get_document_transform_admission(
        authority.user_id,
        IdempotencyKey(plan.plan_id),
    )
    return await tasks.get_for_owner(admission.task_id, authority.user_id)


async def _submit_qa_canary_plan(
    authority: QaAuthority,
    *,
    plan: QaCanaryPlan,
    plan_id: QaPlanId,
    files: QaFileReader,
    tasks: TaskService,
    enqueuer: TaskEnqueuer,
) -> TransformSubmission:
    if not hmac.compare_digest(plan.plan_id.bytes, plan_id.bytes):
        raise QaGrantError
    payload = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file",
            file_id=plan.source_file_id,
            expected_modified_time=plan.source_modified_time,
        ),
        mode=authority.mode,
        output_strategy=OutputStrategy.COPY,
        copy_destination_folder_id=authority.destination_folder_id,
    )
    return await submit_document_transform(
        payload=payload,
        owner_user_id=authority.user_id,
        idempotency_key=plan.idempotency_key,
        tasks=tasks,
        enqueuer=enqueuer,
        files=files,
    )


async def plan_qa_canary(
    authority: QaAuthority,
    *,
    drive: QaDriveReader,
    files: QaFileReader,
) -> QaCanaryPlan:
    source_folder = await files.get(authority.source_folder_id)
    destination = await files.get(authority.destination_folder_id)
    if (
        source_folder.id != authority.source_folder_id
        or source_folder.mime_type != MIME_FOLDER
        or destination.id != authority.destination_folder_id
        or destination.mime_type != MIME_FOLDER
        or not destination.can_add_children
    ):
        raise QaGrantError
    documents = await _bounded_native_docs(
        drive,
        root_id=authority.source_folder_id,
        root_drive_id=source_folder.drive_id,
    )
    if len(documents) != 1:
        raise QaGrantError
    selected = documents[0]
    if selected.modified_time is None:
        raise QaGrantError
    operation = _operation_id(authority, selected)
    return QaCanaryPlan(
        plan_id=QaPlanId(operation),
        idempotency_key=IdempotencyKey(operation),
        source_file_id=selected.id,
        source_name=selected.name,
        source_modified_time=selected.modified_time,
        output_strategy=OutputStrategy.COPY,
    )


async def _bounded_native_docs(
    drive: QaDriveReader,
    *,
    root_id: FileId,
    root_drive_id: DriveId | None,
) -> list[DriveNode]:
    pending = [root_id]
    visited: set[FileId] = set()
    documents: list[DriveNode] = []
    traversed_children = 0
    while pending:
        folder_id = pending.pop(0)
        if folder_id in visited or len(visited) >= QA_MAX_TRAVERSED_FOLDERS:
            raise QaGrantError
        visited.add(folder_id)
        children = await drive.list_folder_children_bounded(
            folder_id,
            max_pages=QA_MAX_FOLDER_LIST_PAGES,
            max_children=_MAX_CANARY_PLAN_CHILDREN,
        )
        traversed_children += len(children)
        if traversed_children > _MAX_CANARY_PLAN_CHILDREN:
            raise QaGrantError
        for child in children:
            if child.parent_id != folder_id:
                raise QaGrantError
            child_drive_id = child.shortcut_target_drive_id
            if child_drive_id is not None and child_drive_id != root_drive_id:
                raise QaGrantError
            if child.mime_type == MIME_FOLDER:
                pending.append(child.id)
            elif child.mime_type == MIME_DOC:
                documents.append(child)
                if len(documents) > 1:
                    raise QaGrantError
    return documents


def _operation_id(authority: QaAuthority, source: DriveNode) -> uuid.UUID:
    key = hashlib.sha256(authority.image_digest.encode()).digest()
    message = (
        f"qa-canary-copy\0{authority.grant_id}\0{authority.release_sha}"
        f"\0{authority.source_folder_id}\0{source.id}\0{source.modified_time}"
        f"\0{authority.destination_folder_id}\0{authority.mode.value}"
    ).encode()
    digest = bytearray(hmac.digest(key, message, "sha256")[:16])
    digest[6] = (digest[6] & 0x0F) | 0x50
    digest[8] = (digest[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(digest))
