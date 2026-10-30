from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import hmac
import json
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
    QA_MAX_PERMISSION_EVIDENCE_BYTES,
    QA_MAX_PERMISSION_LIST_PAGES,
    QA_MAX_STORED_FINDINGS,
    QA_MAX_STORED_MODEL_IDS,
    QA_MAX_STORED_RESULT_BYTES,
    QA_MAX_STORED_TOOL_VERSIONS,
    QA_MAX_TRAVERSED_FOLDERS,
)
from app.document_transform.qa_corpus import (
    QaCorpusDriveReader,
    QaCorpusFileReader,
    QaCorpusPlan,
    QaCorpusPlanError,
    plan_authorized_qa_corpus,
)
from app.document_transform.qa_grants import QaAuthority, QaGrantError, QaSubmittedCanary
from app.document_transform.schemas import (
    DocumentOutput,
    DriveFileSource,
    OutputStrategy,
    QaCopyPermissionEvidence,
    QaDestinationPermissionBinding,
    QaDocumentTransformRequest,
)
from app.drive.errors import DrivePermissionsError, DriveTraversalIncompleteError
from app.drive.schemas import MIME_DOC, MIME_FOLDER, DriveNode, FilePermissionSnapshot
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
_MAX_CANARY_DESTINATION_PERMISSIONS = 1_000
_MAX_DRIVE_VERSION_LENGTH = 64
_QA_CORPUS_DOCUMENT_COUNT = 20
_OPERATION_PROPERTY = "sarjTransformOperation"
_SOURCE_HASH_PROPERTY = "sarjTransformSourceHash"


@dataclass(frozen=True, slots=True)
class QaCanaryPlan:
    plan_id: QaPlanId
    idempotency_key: IdempotencyKey
    source_file_id: FileId
    source_name: str
    source_modified_time: str
    destination_version: str
    destination_permission_sha256: str
    output_strategy: OutputStrategy


@dataclass(frozen=True, slots=True)
class QaCorpusCampaignCandidate:
    plan: QaCorpusPlan
    destination: QaDestinationPermissionProof
    corpus_plan_sha256: str
    source_root_sha256: str
    destination_identity_sha256: str


@dataclass(frozen=True, slots=True)
class _QaBrokerServices:
    drive: GoogleDriveService
    files: GoogleFilesService


class QaPermissionReader(Protocol):
    async def read_file_permissions_bounded(
        self,
        file_id: FileId,
        *,
        max_pages: int,
        max_permissions: int,
    ) -> FilePermissionSnapshot: ...


class QaDriveReader(QaPermissionReader, Protocol):
    async def list_folder_children_bounded(
        self,
        folder_id: FileId,
        *,
        max_pages: int,
        max_children: int,
    ) -> list[DriveNode]: ...


class QaFileReader(Protocol):
    async def get(self, file_id: FileId) -> FileResponse: ...


class QaCorpusCampaignDriveReader(QaCorpusDriveReader, QaPermissionReader, Protocol):
    pass


class QaCorpusCampaignFileReader(QaCorpusFileReader, Protocol):
    pass


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
            DrivePermissionsError,
            DriveTraversalIncompleteError,
            FilesError,
            NotImplementedError,
            ValueError,
        ) as exc:
            raise QaGrantError from exc
        return plan

    async def plan_corpus(self, authority: QaAuthority) -> QaCorpusPlan:
        services = await self._services(authority)
        try:
            return await plan_authorized_qa_corpus(
                authority,
                drive=services.drive,
                files=services.files,
            )
        except (
            DriveTraversalIncompleteError,
            FilesError,
            NotImplementedError,
            QaCorpusPlanError,
            ValueError,
        ) as exc:
            raise QaGrantError from exc

    async def plan_corpus_campaign(self, authority: QaAuthority) -> QaCorpusCampaignCandidate:
        services = await self._services(authority)
        try:
            return await plan_qa_corpus_campaign(
                authority,
                drive=services.drive,
                files=services.files,
            )
        except (
            DrivePermissionsError,
            DriveTraversalIncompleteError,
            FilesError,
            NotImplementedError,
            QaCorpusPlanError,
            ValueError,
        ) as exc:
            raise QaGrantError from exc

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
            DrivePermissionsError,
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
                drive=services.drive,
                files=services.files,
                tasks=tasks,
                enqueuer=enqueuer,
            )
        except (
            DrivePermissionsError,
            DriveTraversalIncompleteError,
            FilesError,
            GoogleReauthenticationRequiredError,
            GoogleTokenUnavailableError,
        ) as exc:
            raise QaGrantError from exc

    async def read_task(
        self,
        authority: QaAuthority,
        *,
        submission: QaSubmittedCanary,
        tasks: TaskService,
    ) -> TaskRead:
        services = await self._services(authority)
        try:
            await authorize_qa_canary_scope(authority, files=services.files)
            return await read_submitted_qa_canary_task(
                authority,
                submission=submission,
                tasks=tasks,
            )
        except (
            FilesError,
            NotImplementedError,
            TaskError,
            ValueError,
        ) as exc:
            raise QaGrantError from exc

    async def authorize_output(
        self,
        authority: QaAuthority,
        *,
        output: DocumentOutput,
    ) -> FileResponse:
        services = await self._services(authority)
        try:
            return await authorize_qa_canary_output(
                authority,
                output=output,
                files=services.files,
            )
        except (FilesError, NotImplementedError, ValueError) as exc:
            raise QaGrantError from exc

    @staticmethod
    async def read_permission_evidence(
        authority: QaAuthority,
        *,
        submission: QaSubmittedCanary,
        tasks: TaskService,
    ) -> QaCopyPermissionEvidence:
        try:
            return await tasks.get_qa_permission_evidence(
                submission.task_id,
                authority.user_id,
                max_evidence_bytes=QA_MAX_PERMISSION_EVIDENCE_BYTES,
            )
        except (TaskError, ValueError) as exc:
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
        drive=drive,
        files=files,
        tasks=tasks,
        enqueuer=enqueuer,
    )


async def read_submitted_qa_canary_task(
    authority: QaAuthority,
    *,
    submission: QaSubmittedCanary,
    tasks: TaskService,
) -> TaskRead:
    admission = await tasks.get_document_transform_admission(
        authority.user_id,
        IdempotencyKey(submission.plan_id),
    )
    if not hmac.compare_digest(admission.task_id.bytes, submission.task_id.bytes):
        raise QaGrantError
    task = await tasks.get_for_owner_bounded_result(
        submission.task_id,
        authority.user_id,
        max_result_bytes=QA_MAX_STORED_RESULT_BYTES,
        max_findings=QA_MAX_STORED_FINDINGS,
        max_model_entries=QA_MAX_STORED_MODEL_IDS,
        max_tool_entries=QA_MAX_STORED_TOOL_VERSIONS,
    )
    if not hmac.compare_digest(task.id.bytes, submission.task_id.bytes):
        raise QaGrantError
    return task


async def authorize_qa_canary_scope(
    authority: QaAuthority,
    *,
    files: QaFileReader,
) -> None:
    source = await files.get(authority.source_folder_id)
    destination = await files.get(authority.destination_folder_id)
    if (
        source.id != authority.source_folder_id
        or source.mime_type != MIME_FOLDER
        or destination.id != authority.destination_folder_id
        or destination.mime_type != MIME_FOLDER
        or not destination.can_add_children
    ):
        raise QaGrantError


async def authorize_qa_canary_output(
    authority: QaAuthority,
    *,
    output: DocumentOutput,
    files: QaFileReader,
) -> FileResponse:
    current = await files.get(output.file_id)
    if (
        current.id != output.file_id
        or current.mime_type != MIME_DOC
        or current.parent_folder_id != authority.destination_folder_id
        or output.parent_folder_id != authority.destination_folder_id
    ):
        raise QaGrantError
    return current


async def _submit_qa_canary_plan(
    authority: QaAuthority,
    *,
    plan: QaCanaryPlan,
    plan_id: QaPlanId,
    drive: QaDriveReader,
    files: QaFileReader,
    tasks: TaskService,
    enqueuer: TaskEnqueuer,
) -> TransformSubmission:
    if not hmac.compare_digest(plan.plan_id.bytes, plan_id.bytes):
        raise QaGrantError
    permission_readback = await read_owner_only_file_permission_proof(
        file_id=authority.destination_folder_id,
        expectation=QaPermissionProofExpectation(
            user_email=authority.user_email,
            mime_type=MIME_FOLDER,
        ),
        drive=drive,
    )
    if permission_readback.version != plan.destination_version or not hmac.compare_digest(
        permission_readback.sha256,
        plan.destination_permission_sha256,
    ):
        raise QaGrantError
    payload = QaDocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file",
            file_id=plan.source_file_id,
            expected_modified_time=plan.source_modified_time,
        ),
        mode=authority.mode,
        output_strategy=OutputStrategy.COPY,
        copy_destination_folder_id=authority.destination_folder_id,
        qa_destination_permission=QaDestinationPermissionBinding(
            version=plan.destination_version,
            sha256=plan.destination_permission_sha256,
        ),
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
    destination_proof = await read_owner_only_file_permission_proof(
        file_id=authority.destination_folder_id,
        expectation=QaPermissionProofExpectation(
            user_email=authority.user_email,
            mime_type=MIME_FOLDER,
        ),
        drive=drive,
    )
    operation = _operation_id(authority, selected, destination_proof)
    return QaCanaryPlan(
        plan_id=QaPlanId(operation),
        idempotency_key=IdempotencyKey(operation),
        source_file_id=selected.id,
        source_name=selected.name,
        source_modified_time=selected.modified_time,
        destination_version=destination_proof.version,
        destination_permission_sha256=destination_proof.sha256,
        output_strategy=OutputStrategy.COPY,
    )


async def plan_qa_corpus_campaign(
    authority: QaAuthority,
    *,
    drive: QaCorpusCampaignDriveReader,
    files: QaCorpusCampaignFileReader,
) -> QaCorpusCampaignCandidate:
    plan = await plan_authorized_qa_corpus(authority, drive=drive, files=files)
    plan_is_eligible = all(
        (
            plan.source_folder_id == authority.source_folder_id,
            plan.source_folder_id != authority.destination_folder_id,
            all(
                member.file_id != authority.destination_folder_id for member in plan.member_versions
            ),
            len(plan.documents) == _QA_CORPUS_DOCUMENT_COUNT,
            not plan.unsupported_files,
            all(authority.mode.value in document.supported_modes for document in plan.documents),
        )
    )
    if not plan_is_eligible:
        raise QaGrantError
    destination = await files.get(authority.destination_folder_id)
    if (
        destination.id != authority.destination_folder_id
        or destination.mime_type != MIME_FOLDER
        or destination.drive_id is not None
    ):
        raise QaGrantError
    if not destination.can_add_children or not destination.version:
        raise QaGrantError
    destination_proof = await read_owner_only_file_permission_proof(
        file_id=authority.destination_folder_id,
        expectation=QaPermissionProofExpectation(
            user_email=authority.user_email,
            mime_type=MIME_FOLDER,
        ),
        drive=drive,
    )
    if (
        destination_proof.file_id != destination.id
        or destination_proof.mime_type != destination.mime_type
        or destination_proof.parent_folder_id != destination.parent_folder_id
        or destination_proof.version != destination.version
    ):
        raise QaGrantError
    return QaCorpusCampaignCandidate(
        plan=plan,
        destination=destination_proof,
        corpus_plan_sha256=_canonical_sha256(plan.model_dump(mode="json")),
        source_root_sha256=_canonical_sha256(plan.source_folder.model_dump(mode="json")),
        destination_identity_sha256=_canonical_sha256(
            {
                "file_id": destination.id,
                "mime_type": destination.mime_type,
                "parent_folder_id": destination.parent_folder_id,
                "drive_id": destination.drive_id,
            }
        ),
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


@dataclass(frozen=True, slots=True)
class QaDestinationPermissionProof:
    file_id: FileId
    mime_type: str
    parent_folder_id: FileId | None
    version: str
    sha256: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class QaPermissionProofExpectation:
    user_email: str
    mime_type: str
    parent_id: FileId | None = None
    operation_id: uuid.UUID | None = None
    source_sha256: str | None = None


async def read_owner_only_file_permission_proof(
    *,
    file_id: FileId,
    expectation: QaPermissionProofExpectation,
    drive: QaPermissionReader,
) -> QaDestinationPermissionProof:
    snapshot = await drive.read_file_permissions_bounded(
        file_id,
        max_pages=QA_MAX_PERMISSION_LIST_PAGES,
        max_permissions=_MAX_CANARY_DESTINATION_PERMISSIONS,
    )
    active_permissions = tuple(
        permission for permission in snapshot.permissions if not permission.deleted
    )
    owner_emails = tuple(email.casefold() for email in snapshot.owner_emails)
    expected_email = expectation.user_email.casefold()
    if (
        snapshot.file_id != file_id
        or snapshot.mime_type != expectation.mime_type
        or snapshot.drive_id is not None
        or not snapshot.version.isdigit()
        or len(snapshot.version) > _MAX_DRIVE_VERSION_LENGTH
    ):
        raise QaGrantError
    if expectation.parent_id is not None and snapshot.parent_ids != (expectation.parent_id,):
        raise QaGrantError
    if (
        not snapshot.owned_by_me
        or snapshot.shared
        or (expectation.mime_type == MIME_FOLDER and not snapshot.can_add_children)
    ):
        raise QaGrantError
    if owner_emails != (expected_email,) or len(active_permissions) != 1:
        raise QaGrantError
    owner = active_permissions[0]
    if (
        owner.inherited
        or len(owner.permission_details) != 1
        or owner.permission_details[0].permission_type != "file"
        or owner.permission_details[0].role != "owner"
        or owner.permission_details[0].inherited
    ):
        raise QaGrantError
    if (
        owner.principal_type != "user"
        or owner.role != "owner"
        or owner.pending_owner
        or owner.email_address is None
        or owner.email_address.casefold() != expected_email
    ):
        raise QaGrantError
    if (expectation.operation_id is None) != (expectation.source_sha256 is None):
        raise ValueError
    if expectation.operation_id is not None and (
        snapshot.app_properties.get(_OPERATION_PROPERTY) != str(expectation.operation_id)
        or snapshot.app_properties.get(_SOURCE_HASH_PROPERTY) != expectation.source_sha256
    ):
        raise QaGrantError
    payload = snapshot.model_dump(mode="json")
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return QaDestinationPermissionProof(
        file_id=snapshot.file_id,
        mime_type=snapshot.mime_type,
        parent_folder_id=snapshot.parent_ids[0] if snapshot.parent_ids else None,
        version=snapshot.version,
        sha256=digest,
        observed_at=datetime.now(UTC),
    )


def _operation_id(
    authority: QaAuthority,
    source: DriveNode,
    destination: QaDestinationPermissionProof,
) -> uuid.UUID:
    key = hashlib.sha256(authority.image_digest.encode()).digest()
    message = (
        f"qa-canary-copy\0{authority.grant_id}\0{authority.release_sha}"
        f"\0{authority.source_folder_id}\0{source.id}\0{source.modified_time}"
        f"\0{authority.destination_folder_id}\0{destination.version}"
        f"\0{destination.sha256}\0{authority.mode.value}"
    ).encode()
    digest = bytearray(hmac.digest(key, message, "sha256")[:16])
    digest[6] = (digest[6] & 0x0F) | 0x50
    digest[8] = (digest[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(digest))


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()
