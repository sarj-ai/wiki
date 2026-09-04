"""Plan or run a resumable, private production transform corpus.

Planning is read-only: it selects a deterministic corpus from private Google Docs
owned by the QA user and writes a local evidence manifest. Execution requires an
explicit confirmation and submits transformations through the live production
HTTP endpoint. The script never changes permissions or edits source documents.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
from typing import TYPE_CHECKING, Annotated, Any, Literal, NamedTuple, NoReturn
import uuid

import httpx
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, TypeAdapter
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select

from app.auth.google_tokens import GoogleTokenManager
from app.cache.dependencies import cache_service_dep
from app.database import async_session
from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.errors import SourceTooLargeError, UnsupportedSourceError
from app.document_transform.executor import PIPELINE_VERSION
from app.document_transform.gateway import SourceSnapshot
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.ir import DocumentIR, parse_google_document
from app.document_transform.safety import validate_patches
from app.document_transform.schemas import (
    DocumentTransformResult,
    DriveFileSource,
    TextPatch,
    TransformMode,
    TransformPhase,
)
from app.drive.retry import execute_with_retry
from app.drive.schemas import MIME_DOC, MIME_FOLDER
from app.drive.service import GoogleDriveService
from app.files.service import GoogleFilesService
from app.identifiers import FileId, TaskId
from app.models.user import User
from app.tasks.schemas import (
    FailedTaskRead,
    PendingTaskRead,
    RunningTaskRead,
    SucceededTaskRead,
    TaskRead,
)


if TYPE_CHECKING:
    from collections.abc import Iterable

    from googleapiclient._apis.drive.v3.resources import DriveResource

_API_BASE_URL = "https://api-wiki.sarj.ai"
_ACCESS_TOKEN_ENV = "WIKI_QA_ACCESS_TOKEN"
_CONFIRMATION = "CREATE_20_PRIVATE_SIBLINGS"
_CANARY_CONFIRMATION = "CREATE_1_PRIVATE_CANARY"
_DEFAULT_COUNT = 20
_MAX_LIST_PAGES = 100
_POLL_INTERVAL = timedelta(seconds=1)
_TASK_TIMEOUT = timedelta(minutes=15)
_COMMIT_SHA_LENGTH = 40
_QA_NAMESPACE = uuid.UUID("ce3e8475-23f8-5b24-bef2-5369c7b0c45f")
_TRANSFORM_OPERATION_PROPERTY = "sarjTransformOperation"
_TASK_ADAPTER: TypeAdapter[TaskRead] = TypeAdapter(TaskRead)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class _ExternalModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class HealthResponse(_ExternalModel):
    status: Literal["ok"]
    database: Literal["ok"]
    revision: str


class IdentityResponse(_ExternalModel):
    email: EmailStr


class QaSecrets(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    access_token: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=_ACCESS_TOKEN_ENV,
    )


class PermissionEntry(_StrictModel):
    id: str
    principal_type: str
    role: str
    email_address: str | None = None
    domain: str | None = None
    allow_file_discovery: bool | None = None
    expiration_time: str | None = None
    deleted: bool = False
    pending_owner: bool = False


class FileMetadata(_StrictModel):
    id: FileId
    name: str
    mime_type: str = Field(alias="mimeType")
    parents: tuple[FileId, ...] = ()
    modified_time: str | None = Field(default=None, alias="modifiedTime")
    drive_id: str | None = Field(default=None, alias="driveId")
    owned_by_me: bool = Field(default=False, alias="ownedByMe")
    shared: bool = True
    can_edit: bool = False
    can_add_children: bool = False
    owner_emails: tuple[str, ...] = ()
    app_properties: dict[str, str] = Field(default_factory=dict, alias="appProperties")


class SourceEvidence(_StrictModel):
    ordinal: Annotated[int, Field(ge=1)]
    source_id: FileId
    parent_id: FileId
    modified_time: str
    revision_id: str
    semantic_sha256: str
    permission_inventory: tuple[PermissionEntry, ...]
    permission_sha256: str
    parent_permission_inventory: tuple[PermissionEntry, ...]
    parent_permission_sha256: str
    operation_key: uuid.UUID
    output_name: str


class EvidenceResult(_StrictModel):
    source_id: FileId
    source_modified_time_after: str
    source_revision_id_after: str
    source_semantic_sha256_after: str
    source_permission_inventory_after: tuple[PermissionEntry, ...]
    source_permission_sha256_after: str
    task_id: TaskId
    output_id: FileId
    output_parent_id: FileId
    output_owned_by_me: Literal[True]
    output_shared: Literal[False]
    output_revision_id: str
    output_permission_inventory: tuple[PermissionEntry, ...]
    output_permission_sha256: str
    applied_patch_count: int
    deterministic_patch_count: int
    model_patch_count: int
    llm_invoked: bool
    verified_at: datetime


class EvidenceManifest(_StrictModel):
    schema_version: Literal[1] = 1
    created_at: datetime
    api_base_url: Literal["https://api-wiki.sarj.ai"] = "https://api-wiki.sarj.ai"
    expected_release_sha: str
    seed: str
    seed_sha256: str
    mode: TransformMode
    count: Annotated[int, Field(ge=1, le=_DEFAULT_COUNT)]
    user_email: EmailStr
    pipeline_version: str
    brand_profile_id: str
    brand_profile_sha256: str
    selection_rejections: dict[str, int]
    sources: tuple[SourceEvidence, ...]
    results: tuple[EvidenceResult, ...] = ()


class LedgerRecord(_StrictModel):
    schema_version: Literal[1] = 1
    recorded_at: datetime
    event: Literal["manifest", "submitted", "terminal", "verified", "failed"]
    operation_key: uuid.UUID | None = None
    source_id: FileId | None = None
    task_id: TaskId | None = None
    output_id: FileId | None = None
    detail: str | None = None
    result: EvidenceResult | None = None


class VerifiedSourceState(NamedTuple):
    snapshot: SourceSnapshot
    document: DocumentIR
    permissions: tuple[PermissionEntry, ...]
    parent_permissions: tuple[PermissionEntry, ...]


class SelectionResult(NamedTuple):
    sources: list[SourceEvidence]
    rejections: Counter[str]


class SubmissionResult(NamedTuple):
    task: TaskRead
    replayed: bool


class QaError(RuntimeError):
    """A deterministic QA invariant failed."""


def _fail(message: str) -> NoReturn:
    raise QaError(message)


def main() -> None:
    manifest = asyncio.run(_run(_arguments()))
    print(manifest.model_dump_json(indent=2))


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--expected-release-sha", required=True)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--user-email", default="nasr@sarj.ai")
    parser.add_argument("--count", type=int, default=_DEFAULT_COUNT)
    parser.add_argument("--mode", choices=[item.value for item in TransformMode], default="both")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--canary", action="store_true")
    parser.add_argument("--confirm-live-copy")
    return parser.parse_args()


async def _run(arguments: argparse.Namespace) -> EvidenceManifest:
    _validate_arguments(arguments)
    manifest_path = arguments.manifest.resolve()
    ledger_path = arguments.ledger.resolve()
    if manifest_path == ledger_path:
        _fail("manifest and ledger paths must be different")
    mode = TransformMode(arguments.mode)
    async with async_session() as db, httpx.AsyncClient(timeout=30) as http:
        user = await db.scalar(select(User).where(User.email == arguments.user_email))
        if user is None:
            _fail("QA user is not present in the configured database")
        prepared = await GoogleTokenManager(db, http).prepare(user)
        drive = GoogleDriveService(
            prepared.user,
            cache_service_dep(),
            refresh_handler=prepared.refresh_handler,
        )
        files = GoogleFilesService(user=prepared.user, drive=drive)
        drive_client = GoogleDriveService.build_drive_client(
            prepared.user,
            prepared.refresh_handler,
        )
        gateway = GoogleDocumentGateway(
            user=prepared.user,
            files=files,
            logo_uri=CURRENT_BRAND_MANIFEST.assets.logo_uri,
            refresh_handler=prepared.refresh_handler,
        )
        await _verify_release(http, arguments.expected_release_sha)
        manifest = await _load_or_plan_manifest(
            path=manifest_path,
            ledger_path=ledger_path,
            drive_client=drive_client,
            gateway=gateway,
            expected_release_sha=arguments.expected_release_sha,
            seed=arguments.seed,
            count=arguments.count,
            mode=mode,
            user_email=arguments.user_email,
        )
        ledger = await asyncio.to_thread(_ensure_manifest_ledger, ledger_path, manifest)
        if not arguments.execute:
            return manifest
        token = QaSecrets().access_token.get_secret_value()
        if not token:
            _fail(f"{_ACCESS_TOKEN_ENV} is required only with --execute")
        await _verify_api_identity(
            http,
            token=token,
            expected_email=arguments.user_email,
        )
        results = await _execute_corpus(
            manifest=manifest,
            ledger=ledger,
            ledger_path=ledger_path,
            manifest_path=manifest_path,
            http=http,
            token=token,
            drive_client=drive_client,
            gateway=gateway,
            user_email=arguments.user_email,
        )
        completed_manifest = manifest.model_copy(update={"results": tuple(results)})
        await asyncio.to_thread(_write_manifest, manifest_path, completed_manifest)
        return completed_manifest


def _validate_arguments(arguments: argparse.Namespace) -> None:
    if not 1 <= arguments.count <= _DEFAULT_COUNT:
        _fail(f"--count must be between 1 and {_DEFAULT_COUNT}")
    if arguments.canary and not arguments.execute:
        _fail("--canary is only valid with --execute")
    if arguments.execute:
        required_count = 1 if arguments.canary else _DEFAULT_COUNT
        required_confirmation = _CANARY_CONFIRMATION if arguments.canary else _CONFIRMATION
        if arguments.confirm_live_copy != required_confirmation:
            _fail(f"--confirm-live-copy must equal {required_confirmation}")
        if arguments.count != required_count:
            _fail(f"--execute requires --count {required_count}")
        if arguments.mode != TransformMode.BOTH.value:
            _fail("--execute requires --mode both")
    if not arguments.execute and arguments.confirm_live_copy is not None:
        _fail("--confirm-live-copy is only valid with --execute")
    if (
        not arguments.expected_release_sha
        or len(arguments.expected_release_sha) != _COMMIT_SHA_LENGTH
    ):
        _fail("--expected-release-sha must be a full 40-character commit SHA")


async def _verify_release(http: httpx.AsyncClient, expected_release_sha: str) -> None:
    response = await http.get(f"{_API_BASE_URL}/health")
    if response.status_code != httpx.codes.OK:
        _fail(f"production health returned HTTP {response.status_code}")
    body = HealthResponse.model_validate_json(response.content)
    if body.revision != expected_release_sha:
        _fail("production health does not report the expected release SHA")


async def _verify_api_identity(
    http: httpx.AsyncClient,
    *,
    token: str,
    expected_email: str,
) -> None:
    response = await http.get(
        f"{_API_BASE_URL}/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    if response.status_code != httpx.codes.OK:
        _fail(f"production identity check returned HTTP {response.status_code}")
    body = IdentityResponse.model_validate_json(response.content)
    if str(body.email).casefold() != expected_email.casefold():
        _fail("production API token does not belong to the requested QA user")


async def _load_or_plan_manifest(
    *,
    path: Path,
    ledger_path: Path,
    drive_client: DriveResource,
    gateway: GoogleDocumentGateway,
    expected_release_sha: str,
    seed: str,
    count: int,
    mode: TransformMode,
    user_email: str,
) -> EvidenceManifest:
    manifest = await asyncio.to_thread(_read_manifest, path)
    if manifest is not None:
        _require_matching_manifest(
            manifest,
            expected_release_sha=expected_release_sha,
            seed=seed,
            count=count,
            mode=mode,
            user_email=user_email,
        )
        return manifest
    if await asyncio.to_thread(_nonempty_file_exists, ledger_path):
        _fail("ledger exists without its evidence manifest")
    selection = await _select_sources(
        drive_client=drive_client,
        gateway=gateway,
        expected_release_sha=expected_release_sha,
        seed=seed,
        count=count,
        mode=mode,
        user_email=user_email,
    )
    manifest = EvidenceManifest(
        created_at=datetime.now(UTC),
        expected_release_sha=expected_release_sha,
        seed=seed,
        seed_sha256=_sha256_text(seed),
        mode=mode,
        count=count,
        user_email=user_email,
        pipeline_version=PIPELINE_VERSION,
        brand_profile_id=CURRENT_BRAND_MANIFEST.profile_id,
        brand_profile_sha256=CURRENT_BRAND_MANIFEST.sha256,
        selection_rejections=dict(sorted(selection.rejections.items())),
        sources=tuple(selection.sources),
    )
    _write_manifest(path, manifest)
    _append_ledger(
        ledger_path,
        LedgerRecord(
            recorded_at=datetime.now(UTC),
            event="manifest",
            detail=_manifest_plan_sha256(manifest),
        ),
    )
    return manifest


def _require_matching_manifest(
    manifest: EvidenceManifest,
    *,
    expected_release_sha: str,
    seed: str,
    count: int,
    mode: TransformMode,
    user_email: str,
) -> None:
    expected = (
        manifest.expected_release_sha == expected_release_sha,
        manifest.seed == seed,
        manifest.count == count,
        manifest.mode is mode,
        str(manifest.user_email).casefold() == user_email.casefold(),
        manifest.pipeline_version == PIPELINE_VERSION,
        manifest.brand_profile_id == CURRENT_BRAND_MANIFEST.profile_id,
        manifest.brand_profile_sha256 == CURRENT_BRAND_MANIFEST.sha256,
    )
    if not all(expected):
        _fail("existing evidence manifest does not match this invocation")


async def _select_sources(
    *,
    drive_client: DriveResource,
    gateway: GoogleDocumentGateway,
    expected_release_sha: str,
    seed: str,
    count: int,
    mode: TransformMode,
    user_email: str,
) -> SelectionResult:
    candidates = await asyncio.to_thread(_list_owned_doc_metadata, drive_client)
    ordered = seeded_order(candidates, seed)
    selected: list[SourceEvidence] = []
    rejections: Counter[str] = Counter()
    for raw in ordered:
        if len(selected) == count:
            break
        try:
            evidence = await _inspect_candidate(
                raw=raw,
                ordinal=len(selected) + 1,
                drive_client=drive_client,
                gateway=gateway,
                expected_release_sha=expected_release_sha,
                seed=seed,
                mode=mode,
                user_email=user_email,
            )
        except (QaError, SourceTooLargeError, UnsupportedSourceError) as exc:
            rejections[_safe_rejection(exc)] += 1
            continue
        selected.append(evidence)
    if len(selected) != count:
        _fail(f"only {len(selected)} private, eligible Google Docs were available")
    return SelectionResult(selected, rejections)


async def _inspect_candidate(
    *,
    raw: dict[str, Any],
    ordinal: int,
    drive_client: DriveResource,
    gateway: GoogleDocumentGateway,
    expected_release_sha: str,
    seed: str,
    mode: TransformMode,
    user_email: str,
) -> SourceEvidence:
    metadata = _parse_file_metadata(raw)
    _require_private_file(metadata, user_email, expected_mime=MIME_DOC)
    if _TRANSFORM_OPERATION_PROPERTY in metadata.app_properties:
        _fail("prior_transform_output")
    if len(metadata.parents) != 1 or metadata.modified_time is None:
        _fail("missing_parent_or_modified_time")
    parent = _parse_file_metadata(
        await asyncio.to_thread(_get_file_metadata, drive_client, metadata.parents[0])
    )
    _require_private_file(parent, user_email, expected_mime=MIME_FOLDER)
    if not parent.can_add_children:
        _fail("parent_not_writable")
    source_permissions = await asyncio.to_thread(
        _permission_inventory,
        drive_client,
        metadata.id,
    )
    parent_permissions = await asyncio.to_thread(
        _permission_inventory,
        drive_client,
        parent.id,
    )
    _require_private_permissions(source_permissions, user_email)
    _require_private_permissions(parent_permissions, user_email)
    snapshot = await gateway.load_drive_source(
        DriveFileSource(
            kind="drive_file",
            file_id=metadata.id,
            expected_modified_time=metadata.modified_time,
        )
    )
    document = _parse_snapshot(snapshot)
    operation_key = corpus_operation_key(
        expected_release_sha=expected_release_sha,
        seed=seed,
        source_id=metadata.id,
        mode=mode,
    )
    return SourceEvidence(
        ordinal=ordinal,
        source_id=metadata.id,
        parent_id=metadata.parents[0],
        modified_time=metadata.modified_time,
        revision_id=str(snapshot.revision_id),
        semantic_sha256=document.semantic_sha256,
        permission_inventory=source_permissions,
        permission_sha256=permission_inventory_sha256(source_permissions),
        parent_permission_inventory=parent_permissions,
        parent_permission_sha256=permission_inventory_sha256(parent_permissions),
        operation_key=operation_key,
        output_name=_output_name(expected_release_sha, seed, ordinal),
    )


def seeded_order(candidates: Iterable[dict[str, Any]], seed: str) -> list[dict[str, Any]]:
    return sorted(
        candidates,
        key=lambda candidate: hashlib.sha256(
            f"{seed}\0{candidate.get('id', '')}".encode()
        ).digest(),
    )


def corpus_operation_key(
    *,
    expected_release_sha: str,
    seed: str,
    source_id: str,
    mode: TransformMode,
) -> uuid.UUID:
    return uuid.uuid5(
        _QA_NAMESPACE,
        f"{expected_release_sha}\0{seed}\0{source_id}\0{mode.value}",
    )


def _list_owned_doc_metadata(drive_client: DriveResource) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    page_token: str | None = None
    for _page in range(_MAX_LIST_PAGES):
        request = drive_client.files().list(
            q=f"mimeType='{MIME_DOC}' and trashed=false and 'me' in owners",
            corpora="user",
            spaces="drive",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            pageSize=1000,
            pageToken=page_token,
            fields=f"nextPageToken,incompleteSearch,files({_file_fields()})",
        )
        raw = execute_with_retry(request)
        if raw.get("incompleteSearch") is True:
            _fail("Drive returned an incomplete corpus search")
        page_files = raw.get("files", [])
        if not isinstance(page_files, list) or not all(
            isinstance(item, dict) for item in page_files
        ):
            _fail("Drive returned invalid corpus metadata")
        files.extend(page_files)
        page_token = raw.get("nextPageToken")
        if not page_token:
            return files
    _fail("Drive corpus exceeded the bounded page limit")


def _get_file_metadata(drive_client: DriveResource, file_id: FileId) -> dict[str, Any]:
    raw = execute_with_retry(
        drive_client.files().get(
            fileId=file_id,
            supportsAllDrives=True,
            fields=_file_fields(),
        )
    )
    if not isinstance(raw, dict):
        _fail("Drive returned invalid file metadata")
    return raw


def _file_fields() -> str:
    return (
        "id,name,mimeType,parents,modifiedTime,driveId,ownedByMe,shared,"
        "capabilities(canEdit,canAddChildren),owners(emailAddress),appProperties"
    )


def _parse_file_metadata(raw: dict[str, Any]) -> FileMetadata:
    normalized = dict(raw)
    capabilities = normalized.pop("capabilities", {})
    owners = normalized.pop("owners", [])
    normalized["can_edit"] = (
        capabilities.get("canEdit", False) if isinstance(capabilities, dict) else False
    )
    normalized["can_add_children"] = (
        capabilities.get("canAddChildren", False) if isinstance(capabilities, dict) else False
    )
    normalized["owner_emails"] = tuple(
        owner["emailAddress"]
        for owner in owners
        if isinstance(owner, dict) and isinstance(owner.get("emailAddress"), str)
    )
    return FileMetadata.model_validate(normalized)


def _permission_inventory(
    drive_client: DriveResource,
    file_id: FileId,
) -> tuple[PermissionEntry, ...]:
    permissions: list[PermissionEntry] = []
    page_token: str | None = None
    for _page in range(_MAX_LIST_PAGES):
        raw = execute_with_retry(
            drive_client.permissions().list(
                fileId=file_id,
                supportsAllDrives=True,
                pageSize=100,
                pageToken=page_token,
                fields=(
                    "nextPageToken,permissions(id,type,role,emailAddress,domain,"
                    "allowFileDiscovery,expirationTime,deleted,pendingOwner)"
                ),
            )
        )
        page_permissions = raw.get("permissions", [])
        if not isinstance(page_permissions, list):
            _fail("Drive returned invalid permissions")
        for item in page_permissions:
            if not isinstance(item, dict):
                _fail("Drive returned invalid permission entry")
            permissions.append(
                PermissionEntry(
                    id=str(item.get("id", "")),
                    principal_type=str(item.get("type", "")),
                    role=str(item.get("role", "")),
                    email_address=(
                        item.get("emailAddress")
                        if isinstance(item.get("emailAddress"), str)
                        else None
                    ),
                    domain=item.get("domain") if isinstance(item.get("domain"), str) else None,
                    allow_file_discovery=(
                        item.get("allowFileDiscovery")
                        if isinstance(item.get("allowFileDiscovery"), bool)
                        else None
                    ),
                    expiration_time=(
                        item.get("expirationTime")
                        if isinstance(item.get("expirationTime"), str)
                        else None
                    ),
                    deleted=item.get("deleted") is True,
                    pending_owner=item.get("pendingOwner") is True,
                )
            )
        page_token = raw.get("nextPageToken")
        if not page_token:
            return tuple(sorted(permissions, key=lambda item: item.id))
    _fail("permission inventory exceeded the bounded page limit")


def permission_inventory_sha256(permissions: tuple[PermissionEntry, ...]) -> str:
    payload = [item.model_dump(mode="json") for item in permissions]
    return _sha256_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())


def _require_private_file(metadata: FileMetadata, user_email: str, *, expected_mime: str) -> None:
    if metadata.mime_type != expected_mime:
        _fail("wrong_mime_type")
    if metadata.drive_id is not None or not metadata.owned_by_me or metadata.shared:
        _fail("not_private_my_drive")
    if expected_mime == MIME_DOC and not metadata.can_edit:
        _fail("source_not_editable")
    if tuple(email.casefold() for email in metadata.owner_emails) != (user_email.casefold(),):
        _fail("unexpected_owner")


def _require_private_permissions(
    permissions: tuple[PermissionEntry, ...],
    user_email: str,
) -> None:
    active = tuple(permission for permission in permissions if not permission.deleted)
    if len(active) != 1:
        _fail("not_owner_only_permissions")
    owner = active[0]
    if (
        owner.principal_type != "user"
        or owner.role != "owner"
        or owner.pending_owner
        or owner.email_address is None
        or owner.email_address.casefold() != user_email.casefold()
    ):
        _fail("not_owner_only_permissions")


def _parse_snapshot(snapshot: SourceSnapshot) -> DocumentIR:
    if snapshot.google_document is None or snapshot.revision_id is None:
        _fail("Google Doc snapshot was incomplete")
    return parse_google_document(snapshot.google_document, title=snapshot.title)


async def _execute_corpus(
    *,
    manifest: EvidenceManifest,
    ledger: tuple[LedgerRecord, ...],
    ledger_path: Path,
    manifest_path: Path,
    http: httpx.AsyncClient,
    token: str,
    drive_client: DriveResource,
    gateway: GoogleDocumentGateway,
    user_email: str,
) -> list[EvidenceResult]:
    results = _verified_results(ledger)
    tasks = _submitted_tasks(ledger)
    for source in manifest.sources:
        if source.operation_key in results:
            continue
        try:
            result = await _execute_source(
                source,
                mode=manifest.mode,
                tasks=tasks,
                ledger_path=ledger_path,
                http=http,
                token=token,
                drive_client=drive_client,
                gateway=gateway,
                user_email=user_email,
            )
        except Exception as exc:
            _append_ledger(
                ledger_path,
                LedgerRecord(
                    recorded_at=datetime.now(UTC),
                    event="failed",
                    operation_key=source.operation_key,
                    source_id=source.source_id,
                    task_id=tasks.get(source.operation_key),
                    detail=_safe_rejection(exc),
                ),
            )
            raise
        results[source.operation_key] = result
        _append_ledger(
            ledger_path,
            LedgerRecord(
                recorded_at=datetime.now(UTC),
                event="verified",
                operation_key=source.operation_key,
                source_id=source.source_id,
                task_id=result.task_id,
                output_id=result.output_id,
                result=result,
            ),
        )
        _write_manifest(
            manifest_path,
            manifest.model_copy(update={"results": tuple(_ordered_results(manifest, results))}),
        )
    return _ordered_results(manifest, results)


async def _execute_source(
    source: SourceEvidence,
    *,
    mode: TransformMode,
    tasks: dict[uuid.UUID, TaskId],
    ledger_path: Path,
    http: httpx.AsyncClient,
    token: str,
    drive_client: DriveResource,
    gateway: GoogleDocumentGateway,
    user_email: str,
) -> EvidenceResult:
    before_state = await _assert_source_unchanged(
        source,
        drive_client=drive_client,
        gateway=gateway,
        user_email=user_email,
    )
    task_id = tasks.get(source.operation_key)
    if task_id is None:
        submission = await _submit_transform(http, token, mode, source)
        task_id = submission.task.id
        tasks[source.operation_key] = task_id
        _record_submission(ledger_path, source, task_id, replayed=submission.replayed)
        if source.ordinal == 1:
            await _require_idempotent_replay(http, token, mode, source, task_id)
    terminal = await _poll_task(http, token, task_id)
    _append_ledger(
        ledger_path,
        LedgerRecord(
            recorded_at=datetime.now(UTC),
            event="terminal",
            operation_key=source.operation_key,
            source_id=source.source_id,
            task_id=task_id,
            detail=terminal.status,
        ),
    )
    transform = _require_succeeded_transform(terminal)
    return await _verify_result_and_drive(
        source=source,
        before_snapshot=before_state.snapshot,
        transform=transform,
        task_id=task_id,
        mode=mode,
        drive_client=drive_client,
        gateway=gateway,
        user_email=user_email,
    )


def _record_submission(
    ledger_path: Path,
    source: SourceEvidence,
    task_id: TaskId,
    *,
    replayed: bool,
) -> None:
    _append_ledger(
        ledger_path,
        LedgerRecord(
            recorded_at=datetime.now(UTC),
            event="submitted",
            operation_key=source.operation_key,
            source_id=source.source_id,
            task_id=task_id,
            detail=f"idempotency_replayed={str(replayed).lower()}",
        ),
    )


async def _require_idempotent_replay(
    http: httpx.AsyncClient,
    token: str,
    mode: TransformMode,
    source: SourceEvidence,
    task_id: TaskId,
) -> None:
    replay = await _submit_transform(http, token, mode, source)
    if replay.task.id != task_id or not replay.replayed:
        _fail("production idempotency replay did not return the same task")


def _require_succeeded_transform(task: TaskRead) -> DocumentTransformResult:
    if isinstance(task, SucceededTaskRead) and isinstance(task.result, DocumentTransformResult):
        return task.result
    detail = (
        task.error_code if isinstance(task, FailedTaskRead) else f"terminal_status={task.status}"
    )
    _fail(f"production transform did not succeed: {detail}")


async def _submit_transform(
    http: httpx.AsyncClient,
    token: str,
    mode: TransformMode,
    source: SourceEvidence,
) -> SubmissionResult:
    response = await http.post(
        f"{_API_BASE_URL}/document-transforms",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": str(source.operation_key),
        },
        json={
            "source": {
                "kind": "drive_file",
                "file_id": source.source_id,
                "expected_modified_time": source.modified_time,
            },
            "mode": mode.value,
            "output_name": source.output_name,
        },
    )
    if response.status_code != httpx.codes.ACCEPTED:
        _fail(f"transform submission returned HTTP {response.status_code}")
    task = _TASK_ADAPTER.validate_json(response.text)
    replayed = response.headers.get("Idempotency-Replayed", "false").casefold() == "true"
    return SubmissionResult(task, replayed)


async def _poll_task(
    http: httpx.AsyncClient,
    token: str,
    task_id: TaskId,
) -> TaskRead:
    deadline = asyncio.get_running_loop().time() + _TASK_TIMEOUT.total_seconds()
    while True:
        response = await http.get(
            f"{_API_BASE_URL}/tasks/{task_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code != httpx.codes.OK:
            _fail(f"task read returned HTTP {response.status_code}")
        task = _TASK_ADAPTER.validate_json(response.text)
        if not isinstance(task, PendingTaskRead | RunningTaskRead):
            return task
        if asyncio.get_running_loop().time() >= deadline:
            _fail("production transform task timed out")
        await asyncio.sleep(_POLL_INTERVAL.total_seconds())


async def _assert_source_unchanged(
    source: SourceEvidence,
    *,
    drive_client: DriveResource,
    gateway: GoogleDocumentGateway,
    user_email: str,
) -> VerifiedSourceState:
    metadata = _parse_file_metadata(
        await asyncio.to_thread(_get_file_metadata, drive_client, source.source_id)
    )
    _require_private_file(metadata, user_email, expected_mime=MIME_DOC)
    permissions = await asyncio.to_thread(_permission_inventory, drive_client, source.source_id)
    parent_permissions = await asyncio.to_thread(
        _permission_inventory,
        drive_client,
        source.parent_id,
    )
    snapshot = await gateway.load_drive_source(
        DriveFileSource(
            kind="drive_file",
            file_id=source.source_id,
            expected_modified_time=source.modified_time,
        )
    )
    document = _parse_snapshot(snapshot)
    checks = (
        metadata.parents == (source.parent_id,),
        metadata.modified_time == source.modified_time,
        snapshot.modified_time == source.modified_time,
        str(snapshot.revision_id) == source.revision_id,
        document.semantic_sha256 == source.semantic_sha256,
        permissions == source.permission_inventory,
        permission_inventory_sha256(permissions) == source.permission_sha256,
        parent_permissions == source.parent_permission_inventory,
        permission_inventory_sha256(parent_permissions) == source.parent_permission_sha256,
    )
    if not all(checks):
        _fail("source or private parent changed since corpus planning")
    return VerifiedSourceState(snapshot, document, permissions, parent_permissions)


async def _verify_result_and_drive(
    *,
    source: SourceEvidence,
    before_snapshot: SourceSnapshot,
    transform: DocumentTransformResult,
    task_id: TaskId,
    mode: TransformMode,
    drive_client: DriveResource,
    gateway: GoogleDocumentGateway,
    user_email: str,
) -> EvidenceResult:
    _require_transform_result(transform, task_id=task_id, mode=mode)
    after_state = await _assert_source_unchanged(
        source,
        drive_client=drive_client,
        gateway=gateway,
        user_email=user_email,
    )
    output_id = transform.output.file_id
    metadata = _parse_file_metadata(
        await asyncio.to_thread(_get_file_metadata, drive_client, output_id)
    )
    _require_private_file(metadata, user_email, expected_mime=MIME_DOC)
    if metadata.parents != (source.parent_id,) or metadata.name != source.output_name:
        _fail("output is not the exact named sibling from the evidence manifest")
    permissions = await asyncio.to_thread(_permission_inventory, drive_client, output_id)
    _require_private_permissions(permissions, user_email)
    if metadata.modified_time is None:
        _fail("output modifiedTime is missing")
    output_snapshot = await gateway.load_drive_source(
        DriveFileSource(
            kind="drive_file",
            file_id=output_id,
            expected_modified_time=metadata.modified_time,
        )
    )
    before = _parse_snapshot(before_snapshot)
    output = _parse_snapshot(output_snapshot)
    _require_safe_semantic_delta(before, output, mode=mode)
    recovered = await gateway.recover_output(transform.operation_id)
    if recovered is None or recovered.output.file_id != output_id:
        _fail("completed output could not be recovered by operation ID")
    verification = await gateway.verify_output(
        draft=recovered,
        source=before_snapshot,
        expected=output,
        verify_brand=True,
    )
    if not (
        verification.content_preserved
        and verification.structure_preserved
        and verification.brand_applied
    ):
        _fail("fresh deterministic semantic, structure, or brand verification failed")
    return EvidenceResult(
        source_id=source.source_id,
        source_modified_time_after=source.modified_time,
        source_revision_id_after=str(after_state.snapshot.revision_id),
        source_semantic_sha256_after=after_state.document.semantic_sha256,
        source_permission_inventory_after=after_state.permissions,
        source_permission_sha256_after=permission_inventory_sha256(after_state.permissions),
        task_id=task_id,
        output_id=output_id,
        output_parent_id=metadata.parents[0],
        output_owned_by_me=True,
        output_shared=False,
        output_revision_id=str(output_snapshot.revision_id),
        output_permission_inventory=permissions,
        output_permission_sha256=permission_inventory_sha256(permissions),
        applied_patch_count=transform.applied_patch_count,
        deterministic_patch_count=transform.deterministic_patch_count,
        model_patch_count=transform.model_patch_count,
        llm_invoked=transform.llm_invoked,
        verified_at=datetime.now(UTC),
    )


def _require_transform_result(
    result: DocumentTransformResult,
    *,
    task_id: TaskId,
    mode: TransformMode,
) -> None:
    validation = result.validation
    checks = (
        result.operation_id == task_id,
        result.mode is mode,
        result.pipeline_version == PIPELINE_VERSION,
        result.brand_profile_id == CURRENT_BRAND_MANIFEST.profile_id,
        result.brand_profile_sha256 == CURRENT_BRAND_MANIFEST.sha256,
        not result.needs_review,
        not result.findings,
        validation.source_unchanged,
        validation.protected_content_preserved,
        validation.structure_preserved,
        validation.unsafe_patch_count == 0,
        result.applied_patch_count == result.deterministic_patch_count + result.model_patch_count,
        result.llm_invoked == bool(result.model_ids),
    )
    if not all(checks):
        _fail("production result failed the exact v2 telemetry contract")


def _require_safe_semantic_delta(
    before: DocumentIR,
    output: DocumentIR,
    *,
    mode: TransformMode,
) -> None:
    before_shape = tuple(
        (block.id, block.kind, block.level, block.editable) for block in before.blocks
    )
    output_shape = tuple(
        (block.id, block.kind, block.level, block.editable) for block in output.blocks
    )
    if before_shape != output_shape:
        _fail("output body block shape changed")
    patches = [
        TextPatch(
            block_id=source_block.id,
            expected_sha256=source_block.sha256,
            replacement=output_block.text,
            rationale="production corpus postcondition",
        )
        for source_block, output_block in zip(before.blocks, output.blocks, strict=True)
        if source_block.text != output_block.text
    ]
    if mode is TransformMode.FORMAT and patches:
        _fail("format-only output changed body text")
    decision = validate_patches(before, patches, phase=TransformPhase.UNSLOP)
    expected = {patch.block_id: patch.replacement for patch in patches}
    if decision.findings or decision.accepted != expected:
        _fail("output text delta is not a validated unslop-only deletion")


def _ensure_manifest_ledger(
    path: Path,
    manifest: EvidenceManifest,
) -> tuple[LedgerRecord, ...]:
    records = _load_ledger(path)
    manifests = tuple(record for record in records if record.event == "manifest")
    if not manifests and not records:
        _append_ledger(
            path,
            LedgerRecord(
                recorded_at=datetime.now(UTC),
                event="manifest",
                detail=_manifest_plan_sha256(manifest),
            ),
        )
        return _load_ledger(path)
    if len(manifests) != 1 or manifests[0].detail != _manifest_plan_sha256(manifest):
        _fail("ledger does not belong to this evidence manifest")
    return records


def _load_ledger(path: Path) -> tuple[LedgerRecord, ...]:
    if not path.exists():
        return ()
    records: list[LedgerRecord] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            _fail(f"blank ledger record at line {line_number}")
        try:
            records.append(LedgerRecord.model_validate_json(line))
        except ValueError:
            _fail(f"invalid ledger record at line {line_number}")
    return tuple(records)


def _manifest_plan_sha256(manifest: EvidenceManifest) -> str:
    plan = manifest.model_copy(update={"results": ()})
    return _sha256_bytes(plan.model_dump_json().encode())


def _read_manifest(path: Path) -> EvidenceManifest | None:
    if not path.exists():
        return None
    return EvidenceManifest.model_validate_json(path.read_text(encoding="utf-8"))


def _nonempty_file_exists(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def _append_ledger(path: Path, record: LedgerRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(record.model_dump_json())
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _submitted_tasks(records: tuple[LedgerRecord, ...]) -> dict[uuid.UUID, TaskId]:
    tasks: dict[uuid.UUID, TaskId] = {}
    for record in records:
        if record.event == "submitted" and record.operation_key is not None and record.task_id:
            prior = tasks.setdefault(record.operation_key, record.task_id)
            if prior != record.task_id:
                _fail("ledger maps one operation key to multiple tasks")
    return tasks


def _verified_results(records: tuple[LedgerRecord, ...]) -> dict[uuid.UUID, EvidenceResult]:
    results: dict[uuid.UUID, EvidenceResult] = {}
    for record in records:
        if record.event == "verified" and record.operation_key is not None and record.result:
            prior = results.setdefault(record.operation_key, record.result)
            if prior != record.result:
                _fail("ledger contains conflicting verified results")
    return results


def _ordered_results(
    manifest: EvidenceManifest,
    results: dict[uuid.UUID, EvidenceResult],
) -> list[EvidenceResult]:
    return [
        results[source.operation_key]
        for source in manifest.sources
        if source.operation_key in results
    ]


def _write_manifest(path: Path, manifest: EvidenceManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(f"{manifest.model_dump_json(indent=2)}\n", encoding="utf-8")
    temporary.replace(path)


def _output_name(expected_release_sha: str, seed: str, ordinal: int) -> str:
    return f"[QA {expected_release_sha[:12]} {_sha256_text(seed)[:8]} {ordinal:02d}] Sarj polished"


def _safe_rejection(exc: Exception) -> str:
    if isinstance(exc, QaError):
        detail = re.sub(r"[^A-Za-z0-9_-]+", "_", str(exc)).strip("_")
        if detail:
            return detail[:120]
    return type(exc).__name__


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode())


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


if __name__ == "__main__":
    main()
