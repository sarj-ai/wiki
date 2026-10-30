from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import hmac
import time
from typing import Protocol, override
import uuid

from cryptography.fernet import InvalidToken
import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aliases.schemas import DocumentAliasRead, DocumentAliasValue
from app.aliases.service import PostgresAliasService
from app.auth.google_tokens import (
    GoogleReauthenticationRequiredError,
    GoogleTokenManager,
    GoogleTokenUnavailableError,
)
from app.cache.service import CacheService
from app.config import settings
from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.errors import (
    DestinationAccessChangedError,
    DocumentGatewayError,
    DocumentTransformError,
    InPlaceNeedsReviewError,
    ModelUnavailableError,
    UnsupportedSourceError,
)
from app.document_transform.executor import (
    PIPELINE_VERSION,
    QaCreatedCopyPermissionRequest,
)
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.google_presentation_gateway import (
    PPTX_MIME_TYPE,
    GooglePresentationGateway,
)
from app.document_transform.header_provenance import HeaderIdentityEvidence, HeaderIdentityProof
from app.document_transform.models import PatchGenerator
from app.document_transform.naming import polish_alias_candidates
from app.document_transform.presentation_executor import PresentationTransformExecutor
from app.document_transform.qa_broker import (
    QaDestinationPermissionProof,
    QaPermissionProofExpectation,
    read_owner_only_file_permission_proof,
)
from app.document_transform.qa_grants import QaGrantError
from app.document_transform.runner import DocumentTransformRunner as DocumentTransformRunner
from app.document_transform.schemas import (
    DocumentTransformCheckpoint,
    DocumentTransformRequest,
    DocumentTransformResult,
    DriveFileSource,
    InPlaceCheckpoint,
    OutputStrategy,
    QaCopyPermissionEvidence,
    QaDestinationPermissionBinding,
    QaDocumentTransformRequest,
    QaFilePermissionEvidence,
    TransformMode,
)
from app.document_transform.slides_brand import presentation_fingerprint
from app.document_transform.threaded_markdown_executor import ThreadedMarkdownExecutor
from app.document_transform.word_executor import WordTransformExecutor
from app.document_transform.word_gateway import WORD_MIME, GoogleWordGateway
from app.drive.errors import DrivePermissionsError, DriveTraversalIncompleteError
from app.drive.retry import ProviderDeadlineError
from app.drive.schemas import MIME_DOC, MIME_FOLDER, MIME_SLIDE
from app.drive.service import GoogleDriveService
from app.files.errors import FileNotInScopeError, FilesError
from app.files.schemas import FileResponse
from app.files.service import GoogleFilesService
from app.identifiers import FileId, TaskId
from app.models.user import User
from app.models.wiki import DocumentHeaderProvenance
from app.tasks.errors import TaskExecutionDeferredError, TaskNotClaimableError
from app.tasks.schemas import (
    CancelledTaskRead,
    DocumentTransformTaskRecord,
    FailedTaskRead,
    SucceededTaskRead,
    TaskClaim,
    TaskRead,
)
from app.tasks.service import TaskService


_EXECUTION_LEASE = timedelta(minutes=6)
_MAX_LOGO_BYTES = 2 * 1024 * 1024
_MAX_TASK_ATTEMPTS = 10


class DocumentTransformTaskExecutor(ABC):
    @abstractmethod
    async def execute(self, task_id: TaskId) -> TaskRead: ...


class RetryTaskStore(Protocol):
    async def defer(self, task_id: TaskId, *, lease_token: uuid.UUID) -> TaskRead: ...

    async def fail(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        error_code: str,
        error_detail: str | None,
    ) -> TaskRead: ...


class ClaimedTaskStore(RetryTaskStore, Protocol):
    async def record_resource(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        resource_id: FileId,
    ) -> None: ...

    async def succeed(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        result: DocumentTransformResult,
    ) -> TaskRead: ...


class PolishShortLinks(Protocol):
    async def ensure(self, output_id: FileId, title: str) -> DocumentAliasValue | None: ...


class AutomaticAliasStore(Protocol):
    async def ensure_for_file(
        self, file_id: FileId, candidates: tuple[DocumentAliasValue, ...]
    ) -> DocumentAliasRead: ...


class TransactionReset(Protocol):
    async def rollback(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _AutomaticShortLinks:
    aliases: AutomaticAliasStore
    db: TransactionReset

    async def ensure(self, output_id: FileId, title: str) -> DocumentAliasValue | None:
        try:
            link = await self.aliases.ensure_for_file(
                output_id, polish_alias_candidates(output_id, title)
            )
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise
            # A link outage must not replay successful provider mutations. Reset
            # a failed alias transaction before persisting the successful result.
            await self.db.rollback()
            logger.warning("document transform automatic short link unavailable")
            return None
        return link.alias


@dataclass(frozen=True, slots=True)
class DocumentTransformRuntime:
    transform: DocumentTransformRunner
    short_links: PolishShortLinks | None = None


def _required_payload(record: DocumentTransformTaskRecord) -> DocumentTransformRequest:
    if record.payload is None:
        msg = "Active document-transform task is missing its payload"
        raise ValueError(msg)
    return record.payload


class DriveDocumentTransformTaskExecutor(DocumentTransformTaskExecutor):
    def __init__(
        self,
        *,
        db: AsyncSession,
        tasks: TaskService,
        cache: CacheService,
        http: httpx.AsyncClient,
        generator: PatchGenerator,
        logo_uri: str,
    ) -> None:
        self._db = db
        self._tasks = tasks
        self._cache = cache
        self._http = http
        self._generator = generator
        self._logo_uri = logo_uri

    @override
    async def execute(self, task_id: TaskId) -> TaskRead:
        try:
            record = await self._tasks.load_document_transform(task_id)
        except ValueError, TypeError, InvalidToken:
            # A malformed encrypted payload or checkpoint must not prevent the
            # worker from claiming the task and recording a terminal failure.
            record = None
        if record is not None and isinstance(
            record.task, SucceededTaskRead | FailedTaskRead | CancelledTaskRead
        ):
            return record.task
        try:
            claim = await self._tasks.claim(task_id, lease_duration=_EXECUTION_LEASE)
        except TaskNotClaimableError as exc:
            raise TaskExecutionDeferredError from exc
        try:
            claimed_record = await self._tasks.load_document_transform(task_id)
            claimed_payload = _required_payload(claimed_record)
        except ValueError, TypeError, InvalidToken:
            return await self._tasks.fail(
                task_id,
                lease_token=claim.lease_token,
                error_code="invalid_task_payload",
                error_detail="The saved document transformation request could not be read",
            )
        try:
            if claim.task.attempt_count > _MAX_TASK_ATTEMPTS:
                return await self._tasks.fail(
                    task_id,
                    lease_token=claim.lease_token,
                    error_code=(
                        "needs_review"
                        if claimed_payload.output_strategy is OutputStrategy.IN_PLACE
                        else "execution_attempts_exhausted"
                    ),
                    error_detail="Execution retry limit reached; review the document before retrying",
                )
            if (
                claimed_payload.output_strategy is OutputStrategy.IN_PLACE
                and not settings.DOCUMENT_TRANSFORM_IN_PLACE_ENABLED
            ):
                return await self._tasks.fail(
                    task_id,
                    lease_token=claim.lease_token,
                    error_code="in_place_disabled",
                    error_detail="In-place document transformation is disabled",
                )
            runtime = await self._runtime(
                claimed_record.owner_user_id,
                claimed_payload,
                task_id=task_id,
                lease_token=claim.lease_token,
                checkpoint=claimed_record.checkpoint,
                checkpoint_generation=claimed_record.checkpoint_generation,
            )
        except GoogleReauthenticationRequiredError, GoogleTokenUnavailableError:
            return await self._tasks.fail(
                task_id,
                lease_token=claim.lease_token,
                error_code="google_auth_required",
                error_detail="Reconnect Google Drive, then start the document polish again",
            )
        except DocumentGatewayError as exc:
            if not exc.retryable:
                return await self._tasks.fail(
                    task_id,
                    lease_token=claim.lease_token,
                    error_code=exc.code,
                    error_detail=exc.public_detail,
                )
            return await defer_or_fail_retryable_task(
                task_id,
                tasks=self._tasks,
                claim=claim,
                error_code=exc.code,
                error_detail=exc.public_detail,
                cause=exc,
            )
        except DocumentTransformError as exc:
            return await self._tasks.fail(
                task_id,
                lease_token=claim.lease_token,
                error_code=exc.code,
                error_detail=exc.public_detail,
            )
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise
            return await defer_or_fail_retryable_task(
                task_id,
                tasks=self._tasks,
                claim=claim,
                error_code="document_transform_initialization_failed",
                error_detail="The document transformation could not be initialized",
                cause=exc,
            )
        return await _execute_claimed(
            tasks=self._tasks,
            transform=runtime.transform,
            task_id=task_id,
            claim=claim,
            payload=claimed_payload,
            checkpoint=claimed_record.checkpoint,
            short_links=runtime.short_links,
        )

    async def _runtime(
        self,
        owner_user_id: uuid.UUID,
        payload: DocumentTransformRequest,
        *,
        task_id: TaskId,
        lease_token: uuid.UUID,
        checkpoint: DocumentTransformCheckpoint | None,
        checkpoint_generation: int,
    ) -> DocumentTransformRuntime:
        user = await self._db.get(User, owner_user_id)
        if user is None:  # pragma: no cover - owner FK cascades task deletion
            msg = "Task owner no longer exists"
            raise RuntimeError(msg)
        prepared = await GoogleTokenManager(self._db, self._http).prepare(user)
        drive = GoogleDriveService(
            prepared.user,
            self._cache,
            refresh_handler=prepared.refresh_handler,
        )
        files = GoogleFilesService(user=prepared.user, drive=drive)
        short_links = _AutomaticShortLinks(
            aliases=PostgresAliasService(db=self._db, files=files, user=prepared.user),
            db=self._db,
        )
        logo_bytes: bytes | None = None
        logo_uri = self._logo_uri
        if payload.mode is not TransformMode.UNSLOP:
            logo_bytes = await self._load_logo()
            logo_uri = self._logo_uri if logo_bytes is not None else ""
        header_identity: HeaderIdentityProof | None = None
        if isinstance(payload.source, DriveFileSource):
            source_file, header_identity = await self._authorized_drive_source(
                owner_user_id, payload, files
            )
            if source_file.mime_type == WORD_MIME:
                return DocumentTransformRuntime(
                    short_links=short_links,
                    transform=WordTransformExecutor(
                        gateway=GoogleWordGateway(
                            user=prepared.user,
                            files=files,
                            logo_uri="",
                            refresh_handler=prepared.refresh_handler,
                        ),
                        checkpoint_store=self._tasks,
                        task_id=task_id,
                        lease_token=lease_token,
                        checkpoint=checkpoint,
                        checkpoint_generation=checkpoint_generation,
                    ),
                )
            if source_file.mime_type in {MIME_SLIDE, PPTX_MIME_TYPE}:
                presentation_gateway = GooglePresentationGateway(
                    user=prepared.user,
                    files=files,
                    refresh_handler=prepared.refresh_handler,
                    fingerprint=presentation_fingerprint,
                )
                return DocumentTransformRuntime(
                    short_links=short_links,
                    transform=PresentationTransformExecutor(
                        gateway=presentation_gateway,
                        generator=self._generator,
                        logo_uri=logo_uri,
                    ),
                )
        document_gateway = GoogleDocumentGateway(
            user=prepared.user,
            files=files,
            logo_uri=logo_uri,
            refresh_handler=prepared.refresh_handler,
            header_identity=header_identity,
        )

        async def read_qa_destination_permission(
            destination_folder_id: FileId,
            binding: QaDestinationPermissionBinding,
        ) -> QaDestinationPermissionProof:
            try:
                proof = await read_owner_only_file_permission_proof(
                    file_id=destination_folder_id,
                    expectation=QaPermissionProofExpectation(
                        user_email=user.email,
                        mime_type=MIME_FOLDER,
                    ),
                    drive=drive,
                )
            except QaGrantError as exc:
                raise DestinationAccessChangedError from exc
            except DrivePermissionsError as exc:
                if exc.http_status in {403, 404}:
                    raise DestinationAccessChangedError from exc
                raise DocumentGatewayError from exc
            except (DriveTraversalIncompleteError, ValueError) as exc:
                raise DestinationAccessChangedError from exc
            if proof.version != binding.version or not hmac.compare_digest(
                proof.sha256,
                binding.sha256,
            ):
                raise DestinationAccessChangedError
            return proof

        async def verify_qa_destination_permission(
            destination_folder_id: FileId,
            binding: QaDestinationPermissionBinding,
        ) -> None:
            await read_qa_destination_permission(destination_folder_id, binding)

        async def verify_qa_created_copy(
            request: QaCreatedCopyPermissionRequest,
        ) -> QaCopyPermissionEvidence:
            destination_proof = await read_qa_destination_permission(
                request.destination_folder_id, request.binding
            )
            try:
                output_proof = await read_owner_only_file_permission_proof(
                    file_id=request.output_file_id,
                    expectation=QaPermissionProofExpectation(
                        user_email=user.email,
                        mime_type=MIME_DOC,
                        parent_id=request.destination_folder_id,
                        operation_id=request.operation_id,
                        source_sha256=request.source_sha256,
                    ),
                    drive=drive,
                )
            except QaGrantError as exc:
                raise DestinationAccessChangedError from exc
            except DrivePermissionsError as exc:
                if exc.http_status in {403, 404}:
                    raise DestinationAccessChangedError from exc
                raise DocumentGatewayError from exc
            except (DriveTraversalIncompleteError, ValueError) as exc:
                raise DestinationAccessChangedError from exc
            return QaCopyPermissionEvidence(
                operation_id=request.operation_id,
                source_sha256=request.source_sha256,
                output_revision_id=request.output_revision_id,
                destination=QaFilePermissionEvidence(
                    file_id=destination_proof.file_id,
                    mime_type=destination_proof.mime_type,
                    parent_folder_id=destination_proof.parent_folder_id,
                    version=destination_proof.version,
                    permission_sha256=destination_proof.sha256,
                    observed_at=destination_proof.observed_at,
                ),
                output=QaFilePermissionEvidence(
                    file_id=output_proof.file_id,
                    mime_type=output_proof.mime_type,
                    parent_folder_id=output_proof.parent_folder_id,
                    version=output_proof.version,
                    permission_sha256=output_proof.sha256,
                    observed_at=output_proof.observed_at,
                ),
            )

        transform = ThreadedMarkdownExecutor(
            gateway=document_gateway,
            generator=self._generator,
            logo_bytes=logo_bytes,
            checkpoint_store=self._tasks,
            task_id=task_id,
            lease_token=lease_token,
            checkpoint=checkpoint,
            checkpoint_generation=checkpoint_generation,
            qa_destination_permission_verifier=verify_qa_destination_permission,
            qa_created_copy_verifier=verify_qa_created_copy,
        )
        transform.upload_owner = owner_user_id
        return DocumentTransformRuntime(short_links=short_links, transform=transform)

    async def _authorized_drive_source(
        self,
        owner_user_id: uuid.UUID,
        payload: DocumentTransformRequest,
        files: GoogleFilesService,
    ) -> tuple[FileResponse, HeaderIdentityProof | None]:
        if not isinstance(payload.source, DriveFileSource):
            msg = "Drive source authorization requires a Drive file"
            raise TypeError(msg)
        try:
            source_file = await files.get(payload.source.file_id)
        except FileNotInScopeError as exc:
            raise UnsupportedSourceError from exc
        except FilesError as exc:
            if exc.http_status in {401, 403}:
                raise UnsupportedSourceError from exc
            raise DocumentGatewayError from exc
        if isinstance(payload, QaDocumentTransformRequest) and source_file.mime_type != MIME_DOC:
            raise DestinationAccessChangedError
        if payload.output_strategy is not OutputStrategy.IN_PLACE:
            return source_file, None
        provenance = (
            await self._db.execute(
                select(DocumentHeaderProvenance).where(
                    DocumentHeaderProvenance.owner_user_id == owner_user_id,
                    DocumentHeaderProvenance.file_id == payload.source.file_id,
                )
            )
        ).scalar_one_or_none()
        if provenance is None:
            return source_file, None
        header_identity = HeaderIdentityProof(
            evidence=HeaderIdentityEvidence.model_validate(provenance.evidence),
            result_sha256=provenance.result_sha256,
            verified_revision_sha256=provenance.verified_revision_sha256,
        )
        if header_identity.evidence.operation_id != provenance.producer_operation_id:
            raise InPlaceNeedsReviewError
        return source_file, header_identity

    async def _load_logo(self) -> bytes | None:
        if self._logo_uri != CURRENT_BRAND_MANIFEST.assets.logo_uri:
            return None
        try:
            async with self._http.stream("GET", self._logo_uri) as response:
                content = await _bounded_logo_content(response)
        except httpx.HTTPError:
            return None
        if content is None:
            return None
        if (
            not content.startswith(b"\x89PNG\r\n\x1a\n")
            or len(content) > _MAX_LOGO_BYTES
            or hashlib.sha256(content).hexdigest() != CURRENT_BRAND_MANIFEST.assets.logo_sha256
        ):
            return None
        return content


async def _bounded_logo_content(response: httpx.Response) -> bytes | None:
    response.raise_for_status()
    declared_size = response.headers.get("content-length")
    if declared_size is not None and (
        not declared_size.isdigit() or int(declared_size) > _MAX_LOGO_BYTES
    ):
        return None
    collected = bytearray()
    async for chunk in response.aiter_bytes():
        if len(collected) + len(chunk) > _MAX_LOGO_BYTES:
            return None
        collected.extend(chunk)
    return bytes(collected)


async def _execute_claimed(
    task_id: TaskId,
    *,
    tasks: ClaimedTaskStore,
    transform: DocumentTransformRunner,
    claim: TaskClaim,
    payload: DocumentTransformRequest,
    checkpoint: DocumentTransformCheckpoint | None = None,
    short_links: PolishShortLinks | None = None,
) -> TaskRead:
    started_at = time.perf_counter()
    source_kind = payload.source.kind
    logger.info(
        "document transform started task_id={task_id} attempt={attempt} source_kind={source_kind} "
        "mode={mode} output_strategy={output_strategy} checkpoint_stage={checkpoint_stage} "
        "mutation_sha256={mutation_sha256} pipeline={pipeline} brand_profile={brand_profile}",
        task_id=task_id,
        attempt=claim.task.attempt_count,
        source_kind=source_kind,
        mode=payload.mode.value,
        output_strategy=payload.output_strategy.value,
        checkpoint_stage=checkpoint.stage.value if checkpoint is not None else "none",
        mutation_sha256=(
            checkpoint.mutation_sha256
            if isinstance(checkpoint, InPlaceCheckpoint) and checkpoint.mutation_sha256 is not None
            else "none"
        ),
        pipeline=PIPELINE_VERSION,
        brand_profile=CURRENT_BRAND_MANIFEST.profile_id,
    )
    try:
        result = await transform.execute(operation_id=task_id, request=payload)
    except ProviderDeadlineError as exc:
        _log_transform_failure(
            task_id=task_id,
            claim=claim,
            payload=payload,
            error_code=DocumentGatewayError.code,
            started_at=started_at,
        )
        return await defer_or_fail_retryable_task(
            task_id,
            tasks=tasks,
            claim=claim,
            error_code=DocumentGatewayError.code,
            error_detail=DocumentGatewayError.public_detail,
            cause=exc,
        )
    except (DocumentGatewayError, ModelUnavailableError) as exc:
        _log_transform_failure(
            task_id=task_id,
            claim=claim,
            payload=payload,
            error_code=exc.code,
            started_at=started_at,
        )
        if isinstance(exc, DocumentGatewayError) and not exc.retryable:
            return await tasks.fail(
                task_id,
                lease_token=claim.lease_token,
                error_code=exc.code,
                error_detail=exc.public_detail,
            )
        return await defer_or_fail_retryable_task(
            task_id,
            tasks=tasks,
            claim=claim,
            error_code=exc.code,
            error_detail=exc.public_detail,
            cause=exc,
        )
    except DocumentTransformError as exc:
        _log_transform_failure(
            task_id=task_id,
            claim=claim,
            payload=payload,
            error_code=exc.code,
            started_at=started_at,
        )
        return await tasks.fail(
            task_id,
            lease_token=claim.lease_token,
            error_code=exc.code,
            error_detail=exc.public_detail,
        )
    except BaseException as exc:
        if not isinstance(exc, Exception):
            raise
        _log_transform_failure(
            task_id=task_id,
            claim=claim,
            payload=payload,
            error_code="document_transform_internal_error",
            started_at=started_at,
        )
        return await defer_or_fail_retryable_task(
            task_id,
            tasks=tasks,
            claim=claim,
            error_code="document_transform_internal_error",
            error_detail="The document transformation could not be completed",
            cause=exc,
        )
    if (
        short_links is not None
        and result.validation.protected_content_preserved
        and result.validation.structure_preserved
    ):
        # A completed, preserved output can still carry review findings (for
        # example an unsupported date chip). Its private navigation link does
        # not approve those findings or expand the output's permissions.
        alias = await short_links.ensure(result.output.file_id, result.output.name)
        result = result.model_copy(
            update={
                "short_link_alias": alias,
                "short_link_status": "ready" if alias is not None else "unavailable",
            }
        )
    await tasks.record_resource(
        task_id,
        lease_token=claim.lease_token,
        resource_id=result.output.file_id,
    )
    completed = await tasks.succeed(
        task_id,
        lease_token=claim.lease_token,
        result=result,
    )
    logger.info(
        "document transform succeeded task_id={task_id} attempt={attempt} source_kind={source_kind} "
        "mode={mode} output_strategy={output_strategy} checkpoint_stage={checkpoint_stage} "
        "mutation_sha256=none pipeline={pipeline} brand_profile={brand_profile} "
        "llm_invoked={llm_invoked} "
        "deterministic_patches={deterministic_patches} model_patches={model_patches} "
        "findings={findings} duration_ms={duration_ms}",
        task_id=task_id,
        attempt=claim.task.attempt_count,
        source_kind=source_kind,
        mode=payload.mode.value,
        output_strategy=payload.output_strategy.value,
        checkpoint_stage=("complete" if result.output_strategy.value == "in_place" else "none"),
        pipeline=result.pipeline_version,
        brand_profile=result.brand_profile_id,
        llm_invoked=result.llm_invoked,
        deterministic_patches=result.deterministic_patch_count,
        model_patches=result.model_patch_count,
        findings=sorted({finding.code for finding in result.findings}),
        duration_ms=round((time.perf_counter() - started_at) * 1000),
    )
    return completed


def _log_transform_failure(
    *,
    task_id: TaskId,
    claim: TaskClaim,
    payload: DocumentTransformRequest,
    error_code: str,
    started_at: float,
) -> None:
    logger.warning(
        "document transform interrupted task_id={task_id} attempt={attempt} source_kind={source_kind} "
        "mode={mode} output_strategy={output_strategy} brand_profile={brand_profile} "
        "error_code={error_code} duration_ms={duration_ms}",
        task_id=task_id,
        attempt=claim.task.attempt_count,
        source_kind=payload.source.kind,
        mode=payload.mode.value,
        output_strategy=payload.output_strategy.value,
        brand_profile=CURRENT_BRAND_MANIFEST.profile_id,
        error_code=error_code,
        duration_ms=round((time.perf_counter() - started_at) * 1000),
    )


async def defer_or_fail_retryable_task(
    task_id: TaskId,
    *,
    tasks: RetryTaskStore,
    claim: TaskClaim,
    error_code: str,
    error_detail: str,
    cause: Exception,
) -> TaskRead:
    if claim.task.attempt_count >= _MAX_TASK_ATTEMPTS:
        return await tasks.fail(
            task_id,
            lease_token=claim.lease_token,
            error_code=error_code,
            error_detail=error_detail,
        )
    await tasks.defer(task_id, lease_token=claim.lease_token)
    raise TaskExecutionDeferredError from cause
