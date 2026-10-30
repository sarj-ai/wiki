"""Plan or run a resumable, private production transform corpus.

Planning is read-only: it enumerates every native Google Doc below an explicitly
bound private Drive folder and writes a local evidence manifest. Execution requires
an explicit confirmation and submits transformations through the live production
HTTP endpoint. The script never changes permissions or edits source documents.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] - validated local PDF tools only
import sys
import tempfile
from typing import TYPE_CHECKING, Annotated, Any, Literal, NamedTuple, NoReturn, Protocol
import uuid

from google import genai
from google.genai import types as genai_types
import httpx
from pydantic import BaseModel, ConfigDict, EmailStr, Field, RootModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select
import typer

from app.auth.google_tokens import GoogleTokenManager
from app.cache.dependencies import cache_service_dep
from app.config import settings
from app.database import async_session
from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.errors import (
    SourceTooLargeError,
    UnsupportedSourceError,
)
from app.document_transform.executor import PIPELINE_VERSION
from app.document_transform.gateway import SourceSnapshot
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.ir import DocumentIR, parse_google_document
from app.document_transform.qa_contract import (
    PRODUCTION_WIKI_API_ORIGIN,
    QA_MAX_FOLDER_LIST_PAGES,
    QA_MAX_PERMISSION_LIST_PAGES,
    QA_MAX_TRAVERSAL_METADATA_BYTES,
    QA_MAX_TRAVERSED_FOLDERS,
    QaCohort,
    required_document_count,
)
from app.document_transform.qa_resources import prepare_qa_drive
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
from app.identifiers import DriveId, FileId, TaskId
from app.models.user import User
from app.tasks.schemas import (
    FailedTaskRead,
    PendingTaskRead,
    RunningTaskRead,
    SucceededTaskRead,
    TaskRead,
)


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from googleapiclient._apis.drive.v3.resources import DriveResource
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.cache.service import CacheService

_API_BASE_URL = PRODUCTION_WIKI_API_ORIGIN
_ACCESS_TOKEN_ENV = "WIKI_QA_ACCESS_TOKEN"
_CONFIRMATION = "CREATE_ALL_OWNER_ONLY_DESTINATION_COPIES"
_CANARY_CONFIRMATION = "CREATE_1_PRIVATE_CANARY"
_DEFAULT_COUNT = required_document_count("corpus")
_POLL_INTERVAL = timedelta(seconds=1)
_TASK_TIMEOUT = timedelta(minutes=15)
_COMMIT_SHA_LENGTH = 40
_QA_NAMESPACE = uuid.UUID("ce3e8475-23f8-5b24-bef2-5369c7b0c45f")
_TRANSFORM_OPERATION_PROPERTY = "sarjTransformOperation"

_PDF_MIME_TYPE = "application/pdf"
_MAX_EXPORTED_PDF_BYTES = 50 * 1024 * 1024
_SCREENSHOT_DPI: Literal[144] = 144
_MAX_JUDGE_SCREENSHOTS = 12


class CopySourceGateway(Protocol):
    async def load_drive_source(self, source: DriveFileSource) -> SourceSnapshot: ...


_MIN_VISUAL_SCORE = 4
_MAX_PDF_PAGES = 100
_MAX_PAGE_POINTS = 36 * 72
_MAX_SCREENSHOT_BYTES = 25 * 1024 * 1024
_MAX_RENDERED_BYTES = 250 * 1024 * 1024
_CLOUD_RUN_JOB_ENV = "CLOUD_RUN_JOB"
_DURABLE_ARTIFACT_ROOT_ENV = "WIKI_QA_DURABLE_ARTIFACT_ROOT"
_APPROVED_JUDGE_MODELS = frozenset({"gemini-3.8-flash"})


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
    cloud_run_job: str | None = Field(default=None, validation_alias=_CLOUD_RUN_JOB_ENV)
    durable_artifact_root: Path | None = Field(
        default=None,
        validation_alias=_DURABLE_ARTIFACT_ROOT_ENV,
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
    drive_id: DriveId | None = Field(default=None, alias="driveId")
    owned_by_me: bool = Field(default=False, alias="ownedByMe")
    shared: bool = True
    can_edit: bool = False
    can_copy: bool = False
    can_add_children: bool = False
    owner_emails: tuple[str, ...] = ()
    app_properties: dict[str, str] = Field(default_factory=dict, alias="appProperties")


class SourceEvidence(_StrictModel):
    ordinal: Annotated[int, Field(ge=1)]
    source_id: FileId
    drive_id: DriveId | None
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
    release_sha_before: str
    release_sha_after: str
    visual_evidence: VisualEvidence
    visual_judge: VisualJudgeEvidence | None = None
    verified_at: datetime


class ArtifactFile(_StrictModel):
    relative_path: str
    sha256: str
    byte_size: Annotated[int, Field(gt=0)]


class VisualEvidence(_StrictModel):
    source_pdf: ArtifactFile
    output_pdf: ArtifactFile
    source_screenshots: tuple[ArtifactFile, ...]
    output_screenshots: tuple[ArtifactFile, ...]
    renderer: Literal["pdftoppm"] = "pdftoppm"
    renderer_version: str
    dpi: Literal[144] = _SCREENSHOT_DPI


class VisualJudgeEvidence(_StrictModel):
    model_id: str
    verdict: Literal["pass", "review"]
    comparison: Literal["improved", "equivalent", "regressed"]
    typography_score: Annotated[int, Field(ge=1, le=5)]
    spacing_score: Annotated[int, Field(ge=1, le=5)]
    hierarchy_score: Annotated[int, Field(ge=1, le=5)]
    issue_codes: tuple[
        Literal[
            "clipping",
            "collision",
            "crowding",
            "logo",
            "spacing",
            "typography",
            "weak_hierarchy",
        ],
        ...,
    ] = ()
    judged_screenshot_sha256: tuple[str, ...]


class EvidenceManifest(_StrictModel):
    schema_version: Literal[4] = 4
    created_at: datetime
    api_base_url: Literal["https://api-wiki.sarj.ai"] = "https://api-wiki.sarj.ai"
    expected_release_sha: str
    cohort: QaCohort
    canary_attestation_sha256: str | None = None
    judge_model: str | None = None
    seed: str
    seed_sha256: str
    mode: TransformMode
    source_folder_id: FileId
    source_drive_id: DriveId | None
    destination_folder_id: FileId
    destination_permission_inventory: tuple[PermissionEntry, ...]
    destination_permission_sha256: str
    folder_source_ids: tuple[FileId, ...]
    folder_source_count: Annotated[int, Field(ge=1, le=required_document_count("corpus"))]
    count: Annotated[int, Field(ge=1, le=required_document_count("corpus"))]
    user_email: EmailStr
    pipeline_version: str
    brand_profile_id: str
    brand_profile_sha256: str
    selection_rejections: dict[str, int]
    sources: tuple[SourceEvidence, ...]
    results: tuple[EvidenceResult, ...] = ()


class LedgerRecord(_StrictModel):
    schema_version: Literal[2] = 2
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
    folder_source_ids: tuple[FileId, ...]
    source_drive_id: DriveId | None


class SubmissionResult(NamedTuple):
    task: TaskRead
    replayed: bool


class CanaryAttestation(NamedTuple):
    sha256: str
    excluded_source_ids: frozenset[FileId]


class RunPaths(NamedTuple):
    manifest: Path
    ledger: Path
    artifacts: Path | None


class PdfInfo(NamedTuple):
    pages: int
    page_sizes_points: tuple[tuple[float, float], ...]


class VisualCaptureResult(NamedTuple):
    evidence: VisualEvidence
    judge: VisualJudgeEvidence | None


class JudgeResponse(_StrictModel):
    verdict: Literal["pass", "review"]
    comparison: Literal["improved", "equivalent", "regressed"]
    typography_score: Annotated[int, Field(ge=1, le=5)]
    spacing_score: Annotated[int, Field(ge=1, le=5)]
    hierarchy_score: Annotated[int, Field(ge=1, le=5)]
    issue_codes: tuple[
        Literal[
            "clipping",
            "collision",
            "crowding",
            "logo",
            "spacing",
            "typography",
            "weak_hierarchy",
        ],
        ...,
    ] = ()


class QaError(RuntimeError):
    pass


@dataclass(slots=True)
class _DriveTraversalBudget:
    folders: int = 0
    pages: int = 0
    metadata_bytes: int = 0

    def record_folder(self) -> None:
        self.folders += 1
        if self.folders > QA_MAX_TRAVERSED_FOLDERS:
            _fail("Drive folder traversal exceeded the bounded folder limit")

    def reserve_page(self) -> None:
        if self.pages >= QA_MAX_FOLDER_LIST_PAGES:
            _fail("Drive folder traversal exceeded the aggregate page limit")
        self.pages += 1

    def record_page_metadata(self, raw: dict[str, Any]) -> None:
        self.record_metadata(raw)

    def record_metadata(self, raw: dict[str, Any]) -> None:
        encoded = json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode()
        self.metadata_bytes += len(encoded)
        if self.metadata_bytes > QA_MAX_TRAVERSAL_METADATA_BYTES:
            _fail("Drive folder traversal exceeded the aggregate metadata limit")


def _fail(message: str) -> NoReturn:
    raise QaError(message)


CLI = typer.Typer(
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_enable=False,
    help=__doc__,
)


@dataclass(frozen=True, kw_only=True)
class _Arguments:
    seed: str
    expected_release_sha: str
    manifest: Path
    ledger: Path
    source_folder_id: str
    destination_folder_id: str
    user_email: str
    count: int = _DEFAULT_COUNT
    mode: TransformMode = TransformMode.BOTH
    execute: bool = False
    canary: bool = False
    canary_manifest: Path | None = None
    canary_ledger: Path | None = None
    canary_artifacts_dir: Path | None = None
    artifacts_dir: Path | None = None
    pdftoppm_version: str | None = None
    judge_model: str | None = None
    confirm_live_copy: str | None = None


@CLI.command()
def main(
    *,
    seed: Annotated[str, typer.Option("--seed")],
    expected_release_sha: Annotated[str, typer.Option("--expected-release-sha")],
    manifest: Annotated[Path, typer.Option("--manifest")],
    ledger: Annotated[Path, typer.Option("--ledger")],
    source_folder_id: Annotated[str, typer.Option("--source-folder-id")],
    destination_folder_id: Annotated[str, typer.Option("--destination-folder-id")],
    user_email: Annotated[str, typer.Option("--user-email")],
    count: Annotated[int, typer.Option("--count", hidden=True)] = _DEFAULT_COUNT,
    mode: Annotated[TransformMode, typer.Option("--mode")] = TransformMode.BOTH,
    execute: Annotated[bool, typer.Option("--execute")] = False,
    canary: Annotated[bool, typer.Option("--canary")] = False,
    canary_manifest: Annotated[Path | None, typer.Option("--canary-manifest")] = None,
    canary_ledger: Annotated[Path | None, typer.Option("--canary-ledger")] = None,
    canary_artifacts_dir: Annotated[Path | None, typer.Option("--canary-artifacts-dir")] = None,
    artifacts_dir: Annotated[Path | None, typer.Option("--artifacts-dir")] = None,
    pdftoppm_version: Annotated[str | None, typer.Option("--pdftoppm-version")] = None,
    judge_model: Annotated[str | None, typer.Option("--judge-model")] = None,
    confirm_live_copy: Annotated[str | None, typer.Option("--confirm-live-copy")] = None,
) -> None:
    arguments = _Arguments(
        seed=seed,
        expected_release_sha=expected_release_sha,
        manifest=manifest,
        ledger=ledger,
        source_folder_id=source_folder_id,
        destination_folder_id=destination_folder_id,
        user_email=user_email,
        count=count,
        mode=mode,
        execute=execute,
        canary=canary,
        canary_manifest=canary_manifest,
        canary_ledger=canary_ledger,
        canary_artifacts_dir=canary_artifacts_dir,
        artifacts_dir=artifacts_dir,
        pdftoppm_version=pdftoppm_version,
        judge_model=judge_model,
        confirm_live_copy=confirm_live_copy,
    )
    _report(arguments)


def _report(arguments: _Arguments) -> None:
    try:
        manifest = asyncio.run(_run(arguments))
    except QaError as exc:
        print(json.dumps({"error": _safe_rejection(exc), "status": "failed"}), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        print(json.dumps({"error": "internal_error", "status": "failed"}), file=sys.stderr)
        raise SystemExit(1) from None
    print(
        json.dumps(
            {
                "cohort": manifest.cohort,
                "expected_release_sha": manifest.expected_release_sha,
                "planned": len(manifest.sources),
                "verified": len(manifest.results),
                "manifest_plan_sha256": _manifest_plan_sha256(manifest),
            },
            sort_keys=True,
        )
    )


class _QaHttpClientFactory(Protocol):
    def __call__(self, *, timeout: float) -> httpx.AsyncClient: ...


async def _qa_user_by_email(db: AsyncSession, user_email: str) -> User | None:
    return await db.scalar(select(User).where(User.email == user_email))


async def _run(
    arguments: _Arguments,
    *,
    cache_factory: Callable[[], Awaitable[CacheService]] = cache_service_dep,
    token_manager_factory: Callable[
        [AsyncSession, httpx.AsyncClient], GoogleTokenManager
    ] = GoogleTokenManager,
    user_reader: Callable[[AsyncSession, str], Awaitable[User | None]] = _qa_user_by_email,
    http_client_factory: _QaHttpClientFactory = httpx.AsyncClient,
) -> EvidenceManifest:
    _validate_arguments(arguments)
    paths = _resolve_run_paths(arguments)
    mode = arguments.mode
    cohort: QaCohort = "canary" if arguments.canary else "corpus"
    async with async_session() as db, http_client_factory(timeout=30) as http:
        environment = QaSecrets()
        access_token = _qa_access_token(environment)
        user = await user_reader(db, arguments.user_email)
        if user is None:
            _fail("QA user is not present in the configured database")
        prepared = await token_manager_factory(db, http).prepare(user)
        drive = await prepare_qa_drive(prepared, cache_factory=cache_factory)
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
        if cohort == "canary":
            canary = CanaryAttestation(sha256="", excluded_source_ids=frozenset())
        else:
            if (
                arguments.canary_manifest is None
                or arguments.canary_ledger is None
                or arguments.canary_artifacts_dir is None
            ):
                _fail("the corpus requires verified canary paths")
            canary = await asyncio.to_thread(
                _load_verified_canary,
                arguments.canary_manifest.resolve(),
                arguments.canary_ledger.resolve(),
                arguments.canary_artifacts_dir.resolve(),
                expected_release_sha=arguments.expected_release_sha,
                user_email=arguments.user_email,
                judge_model=arguments.judge_model,
            )
        manifest = await _load_or_plan_manifest(
            path=paths.manifest,
            ledger_path=paths.ledger,
            drive_client=drive_client,
            gateway=gateway,
            expected_release_sha=arguments.expected_release_sha,
            seed=arguments.seed,
            count=arguments.count,
            source_folder_id=FileId(arguments.source_folder_id),
            destination_folder_id=FileId(arguments.destination_folder_id),
            mode=mode,
            user_email=arguments.user_email,
            cohort=cohort,
            canary_attestation_sha256=canary.sha256 or None,
            excluded_source_ids=canary.excluded_source_ids,
            judge_model=arguments.judge_model,
        )
        ledger = await asyncio.to_thread(_ensure_manifest_ledger, paths.ledger, manifest)
        if not arguments.execute:
            return manifest
        return await _execute_planned_manifest(
            arguments=arguments,
            paths=paths,
            manifest=manifest,
            ledger=ledger,
            http=http,
            drive_client=drive_client,
            gateway=gateway,
            access_token=access_token,
        )


def _qa_access_token(environment: QaSecrets) -> str:
    access_token = environment.access_token.get_secret_value()
    if environment.cloud_run_job is None:
        return access_token
    _fail(
        "Cloud Run corpus execution remains disabled until the backend-mediated "
        "Drive and transform capability data plane is implemented"
    )


def _resolve_run_paths(arguments: _Arguments) -> RunPaths:
    paths = RunPaths(
        manifest=arguments.manifest.resolve(),
        ledger=arguments.ledger.resolve(),
        artifacts=(
            arguments.artifacts_dir.resolve() if arguments.artifacts_dir is not None else None
        ),
    )
    if paths.manifest == paths.ledger:
        _fail("manifest and ledger paths must be different")
    _require_durable_cloud_run_paths(
        manifest_path=paths.manifest,
        ledger_path=paths.ledger,
        artifacts_dir=paths.artifacts,
    )
    return paths


async def _execute_planned_manifest(
    *,
    arguments: _Arguments,
    paths: RunPaths,
    manifest: EvidenceManifest,
    ledger: tuple[LedgerRecord, ...],
    http: httpx.AsyncClient,
    drive_client: DriveResource,
    gateway: GoogleDocumentGateway,
    access_token: str,
) -> EvidenceManifest:
    if paths.artifacts is None:  # pragma: no cover - argument invariant
        _fail("--artifacts-dir is required with --execute")
    expected_renderer_version = arguments.pdftoppm_version
    if not isinstance(expected_renderer_version, str):
        _fail("--pdftoppm-version is required with --execute")
    renderer_version = await asyncio.to_thread(_require_pdftoppm, expected_renderer_version)
    token = access_token
    if not token:
        _fail(f"{_ACCESS_TOKEN_ENV} is required only with --execute")
    await _verify_api_identity(http, token=token, expected_email=arguments.user_email)
    await asyncio.to_thread(_require_current_source_set, drive_client, manifest)
    await _assert_private_destination(
        drive_client,
        manifest.destination_folder_id,
        arguments.user_email,
        expected_permissions=manifest.destination_permission_inventory,
    )
    results = await _execute_corpus(
        manifest=manifest,
        ledger=ledger,
        ledger_path=paths.ledger,
        manifest_path=paths.manifest,
        http=http,
        token=token,
        drive_client=drive_client,
        gateway=gateway,
        user_email=arguments.user_email,
        expected_release_sha=arguments.expected_release_sha,
        artifacts_dir=paths.artifacts,
        renderer_version=renderer_version,
        judge_model=arguments.judge_model,
    )
    completed = manifest.model_copy(update={"results": tuple(results)})
    await asyncio.to_thread(_write_manifest, paths.manifest, completed)
    return completed


def _validate_arguments(arguments: _Arguments) -> None:
    canary_manifest = arguments.canary_manifest
    canary_ledger = arguments.canary_ledger
    canary_artifacts_dir = arguments.canary_artifacts_dir
    artifacts_dir = arguments.artifacts_dir
    pdftoppm_version = arguments.pdftoppm_version
    judge_model = arguments.judge_model
    if not arguments.source_folder_id:
        _fail("--source-folder-id is required")
    if not arguments.destination_folder_id:
        _fail("--destination-folder-id is required")
    cohort: QaCohort = "canary" if arguments.canary else "corpus"
    required_count = required_document_count(cohort)
    if arguments.count != required_count:
        _fail(f"--count must equal {required_count} for {cohort} QA")
    if arguments.execute:
        required_confirmation = _CANARY_CONFIRMATION if arguments.canary else _CONFIRMATION
        if arguments.confirm_live_copy != required_confirmation:
            _fail(f"--confirm-live-copy must equal {required_confirmation}")
        if arguments.mode != TransformMode.BOTH:
            _fail("--execute requires --mode both")
        if artifacts_dir is None:
            _fail("--execute requires --artifacts-dir")
        if not pdftoppm_version:
            _fail("--execute requires --pdftoppm-version")
        if not judge_model:
            _fail("--execute requires --judge-model")
    if not arguments.execute and arguments.confirm_live_copy is not None:
        _fail("--confirm-live-copy is only valid with --execute")
    canary_paths = (canary_manifest, canary_ledger, canary_artifacts_dir)
    if arguments.canary and any(path is not None for path in canary_paths):
        _fail("canary execution cannot depend on another canary")
    if not arguments.canary and any(path is None for path in canary_paths):
        _fail(
            "the 20-document corpus requires --canary-manifest, --canary-ledger, "
            "and --canary-artifacts-dir"
        )
    if judge_model and not _is_pinned_judge_model(judge_model):
        _fail("--judge-model must be an exact pinned model ID")
    if (
        not arguments.expected_release_sha
        or len(arguments.expected_release_sha) != _COMMIT_SHA_LENGTH
    ):
        _fail("--expected-release-sha must be a full 40-character commit SHA")


def _require_durable_cloud_run_paths(
    *,
    manifest_path: Path,
    ledger_path: Path,
    artifacts_dir: Path | None,
) -> None:
    environment = QaSecrets()
    if environment.cloud_run_job is None:
        return
    if environment.durable_artifact_root is None:
        _fail(f"{_DURABLE_ARTIFACT_ROOT_ENV} is required in Cloud Run")
    root = environment.durable_artifact_root.resolve()
    paths = [manifest_path, ledger_path]
    if artifacts_dir is not None:
        paths.append(artifacts_dir)
    if any(not path.is_relative_to(root) for path in paths):
        _fail("Cloud Run evidence paths must stay inside the durable artifact root")


async def _verify_release(http: httpx.AsyncClient, expected_release_sha: str) -> str:
    response = await http.get(f"{_API_BASE_URL}/health")
    if response.status_code != httpx.codes.OK:
        _fail(f"production health returned HTTP {response.status_code}")
    body = HealthResponse.model_validate_json(response.content)
    if body.revision != expected_release_sha:
        _fail("production health does not report the expected release SHA")
    return body.revision


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
    source_folder_id: FileId,
    destination_folder_id: FileId,
    mode: TransformMode,
    user_email: str,
    cohort: QaCohort,
    canary_attestation_sha256: str | None,
    excluded_source_ids: frozenset[FileId],
    judge_model: str | None,
) -> EvidenceManifest:
    manifest = await asyncio.to_thread(_read_manifest, path)
    if manifest is not None:
        _require_matching_manifest(
            manifest,
            expected_release_sha=expected_release_sha,
            seed=seed,
            count=count,
            source_folder_id=source_folder_id,
            destination_folder_id=destination_folder_id,
            mode=mode,
            user_email=user_email,
            cohort=cohort,
            canary_attestation_sha256=canary_attestation_sha256,
            judge_model=judge_model,
        )
        return manifest
    if await asyncio.to_thread(_nonempty_file_exists, ledger_path):
        _fail("ledger exists without its evidence manifest")
    destination_permissions = await _assert_private_destination(
        drive_client,
        destination_folder_id,
        user_email,
    )
    selection = await _select_sources(
        drive_client=drive_client,
        gateway=gateway,
        expected_release_sha=expected_release_sha,
        seed=seed,
        count=count,
        source_folder_id=source_folder_id,
        destination_folder_id=destination_folder_id,
        mode=mode,
        user_email=user_email,
        cohort=cohort,
        excluded_source_ids=excluded_source_ids,
    )
    manifest = EvidenceManifest(
        created_at=datetime.now(UTC),
        expected_release_sha=expected_release_sha,
        cohort=cohort,
        canary_attestation_sha256=canary_attestation_sha256,
        judge_model=judge_model,
        seed=seed,
        seed_sha256=_sha256_text(seed),
        mode=mode,
        count=len(selection.sources),
        source_folder_id=source_folder_id,
        source_drive_id=selection.source_drive_id,
        destination_folder_id=destination_folder_id,
        destination_permission_inventory=destination_permissions,
        destination_permission_sha256=permission_inventory_sha256(destination_permissions),
        folder_source_ids=selection.folder_source_ids,
        folder_source_count=len(selection.folder_source_ids),
        user_email=user_email,
        pipeline_version=PIPELINE_VERSION,
        brand_profile_id=CURRENT_BRAND_MANIFEST.profile_id,
        brand_profile_sha256=CURRENT_BRAND_MANIFEST.sha256,
        selection_rejections=dict(sorted(selection.rejections.items())),
        sources=tuple(selection.sources),
    )
    _require_manifest_invariants(manifest)
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
    source_folder_id: FileId,
    destination_folder_id: FileId,
    mode: TransformMode,
    user_email: str,
    cohort: QaCohort,
    canary_attestation_sha256: str | None,
    judge_model: str | None,
) -> None:
    expected = (
        manifest.expected_release_sha == expected_release_sha,
        manifest.seed == seed,
        manifest.count == count,
        manifest.source_folder_id == source_folder_id,
        manifest.destination_folder_id == destination_folder_id,
        manifest.mode is mode,
        str(manifest.user_email).casefold() == user_email.casefold(),
        manifest.pipeline_version == PIPELINE_VERSION,
        manifest.brand_profile_id == CURRENT_BRAND_MANIFEST.profile_id,
        manifest.brand_profile_sha256 == CURRENT_BRAND_MANIFEST.sha256,
        manifest.cohort == cohort,
        manifest.canary_attestation_sha256 == canary_attestation_sha256,
        manifest.judge_model == judge_model,
    )
    if not all(expected):
        _fail("existing evidence manifest does not match this invocation")
    _require_manifest_invariants(manifest)


def _require_manifest_invariants(manifest: EvidenceManifest) -> None:
    sources = manifest.sources
    source_ids = tuple(source.source_id for source in sources)
    operation_keys = tuple(source.operation_key for source in sources)
    checks = (
        manifest.seed_sha256 == _sha256_text(manifest.seed),
        manifest.destination_permission_sha256
        == permission_inventory_sha256(manifest.destination_permission_inventory),
        len(sources) == manifest.count,
        manifest.folder_source_count == len(manifest.folder_source_ids),
        manifest.count == required_document_count(manifest.cohort),
        manifest.folder_source_count == required_document_count(manifest.cohort),
        len(set(manifest.folder_source_ids)) == manifest.folder_source_count,
        set(source_ids) == set(manifest.folder_source_ids),
        len(set(source_ids)) == len(source_ids),
        len(set(operation_keys)) == len(operation_keys),
        tuple(source.ordinal for source in sources) == tuple(range(1, manifest.count + 1)),
        manifest.canary_attestation_sha256 is None
        if manifest.cohort == "canary"
        else _is_sha256(manifest.canary_attestation_sha256),
        len(manifest.results) <= len(sources),
    )
    if not all(checks):
        _fail("evidence manifest structural invariants failed")
    for source in sources:
        expected_operation = corpus_operation_key(
            expected_release_sha=manifest.expected_release_sha,
            seed=manifest.seed,
            source_id=source.source_id,
            destination_folder_id=manifest.destination_folder_id,
            mode=manifest.mode,
            cohort=manifest.cohort,
        )
        if (
            source.operation_key != expected_operation
            or source.output_name
            != _output_name(
                manifest.expected_release_sha,
                manifest.seed,
                source.ordinal,
                cohort=manifest.cohort,
            )
            or source.permission_sha256 != permission_inventory_sha256(source.permission_inventory)
            or source.drive_id != manifest.source_drive_id
            or source.parent_permission_sha256
            != permission_inventory_sha256(source.parent_permission_inventory)
        ):
            _fail("evidence manifest source binding failed")
    for source, result in zip(sources, manifest.results, strict=False):
        _require_result_binding(
            source,
            result,
            destination_folder_id=manifest.destination_folder_id,
            expected_release_sha=manifest.expected_release_sha,
            artifacts_dir=None,
            renderer_version=None,
            judge_model=manifest.judge_model,
            user_email=str(manifest.user_email),
        )


def _load_verified_canary(
    manifest_path: Path,
    ledger_path: Path,
    artifacts_dir: Path,
    *,
    expected_release_sha: str,
    user_email: str,
    judge_model: str | None,
) -> CanaryAttestation:
    manifest = _read_manifest(manifest_path)
    if manifest is None:
        _fail("verified canary manifest is missing")
    records = _load_ledger(ledger_path)
    manifest_records = tuple(record for record in records if record.event == "manifest")
    verified = _verified_results(records)
    checks = (
        manifest.cohort == "canary",
        manifest.count == 1,
        manifest.mode is TransformMode.BOTH,
        manifest.expected_release_sha == expected_release_sha,
        str(manifest.user_email).casefold() == user_email.casefold(),
        manifest.judge_model == judge_model,
        manifest.pipeline_version == PIPELINE_VERSION,
        manifest.brand_profile_id == CURRENT_BRAND_MANIFEST.profile_id,
        manifest.brand_profile_sha256 == CURRENT_BRAND_MANIFEST.sha256,
        len(manifest.sources) == 1,
        len(manifest.results) == 1,
        len(manifest_records) == 1,
        manifest_records[0].detail == _manifest_plan_sha256(manifest),
    )
    if not all(checks):
        _fail("canary manifest does not prove this exact release")
    _require_manifest_invariants(manifest)
    source = manifest.sources[0]
    result = manifest.results[0]
    ledger_result = verified.get(source.operation_key)
    if ledger_result != result or len(verified) != 1:
        _fail("canary ledger does not contain one fully verified visual result")
    _require_result_binding(
        source,
        result,
        destination_folder_id=manifest.destination_folder_id,
        expected_release_sha=expected_release_sha,
        artifacts_dir=artifacts_dir,
        renderer_version=result.visual_evidence.renderer_version,
        judge_model=judge_model,
        user_email=user_email,
    )
    payload = json.dumps(
        {
            "manifest_plan_sha256": _manifest_plan_sha256(manifest),
            "result": result.model_dump(mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return CanaryAttestation(
        sha256=_sha256_bytes(payload),
        excluded_source_ids=frozenset({source.source_id}),
    )


async def _select_sources(
    *,
    drive_client: DriveResource,
    gateway: GoogleDocumentGateway,
    expected_release_sha: str,
    seed: str,
    count: int,
    source_folder_id: FileId,
    destination_folder_id: FileId,
    mode: TransformMode,
    user_email: str,
    cohort: QaCohort,
    excluded_source_ids: frozenset[FileId],
) -> SelectionResult:
    root = _parse_file_metadata(
        await asyncio.to_thread(_get_file_metadata, drive_client, source_folder_id)
    )
    if root.mime_type != MIME_FOLDER:
        _fail("source folder is not a Google Drive folder")
    candidates = await asyncio.to_thread(_list_folder_doc_metadata, drive_client, source_folder_id)
    ordered = seeded_order(candidates, seed)
    if len(ordered) != count:
        _fail(f"{cohort} folder must contain exactly {count} native Google Docs")
    if not ordered:
        _fail("source folder contains no native Google Docs")
    selected: list[SourceEvidence] = []
    rejections: Counter[str] = Counter()
    for raw in ordered:
        raw_id = raw.get("id")
        if isinstance(raw_id, str) and FileId(raw_id) in excluded_source_ids:
            _fail("source folder overlaps the verified canary corpus")
        try:
            evidence = await _inspect_candidate(
                raw=raw,
                ordinal=len(selected) + 1,
                drive_client=drive_client,
                gateway=gateway,
                expected_release_sha=expected_release_sha,
                seed=seed,
                destination_folder_id=destination_folder_id,
                source_drive_id=(
                    None if (source_drive_id := root.drive_id) is None else DriveId(source_drive_id)
                ),
                mode=mode,
                user_email=user_email,
                cohort=cohort,
            )
        except (QaError, SourceTooLargeError, UnsupportedSourceError) as exc:
            rejections[_safe_rejection(exc)] += 1
            _fail(f"folder source failed copy preflight: {_safe_rejection(exc)}")
        selected.append(evidence)
    folder_source_ids = tuple(
        FileId(raw["id"]) for raw in ordered if isinstance(raw.get("id"), str)
    )
    if len(folder_source_ids) != len(ordered):
        _fail("Drive returned folder content without a stable file ID")
    return SelectionResult(selected, rejections, folder_source_ids, root.drive_id)


async def _inspect_candidate(
    *,
    raw: dict[str, Any],
    ordinal: int,
    drive_client: DriveResource,
    gateway: CopySourceGateway,
    expected_release_sha: str,
    seed: str,
    destination_folder_id: FileId,
    source_drive_id: DriveId | None,
    mode: TransformMode,
    user_email: str,
    cohort: QaCohort,
) -> SourceEvidence:
    metadata = _parse_file_metadata(raw)
    del user_email
    _require_copyable_source(metadata, expected_drive_id=source_drive_id)
    if _TRANSFORM_OPERATION_PROPERTY in metadata.app_properties:
        _fail("prior_transform_output")
    if len(metadata.parents) != 1 or metadata.modified_time is None:
        _fail("missing_parent_or_modified_time")
    parent = _parse_file_metadata(
        await asyncio.to_thread(_get_file_metadata, drive_client, metadata.parents[0])
    )
    _require_source_parent(parent, expected_drive_id=source_drive_id)
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
    snapshot = await gateway.load_drive_source(
        DriveFileSource(
            kind="drive_file",
            file_id=metadata.id,
            expected_modified_time=metadata.modified_time,
        )
    )
    # Copy mode works on a provider-created sibling and preserves unsupported
    # source furniture. In-place ownership/header eligibility is irrelevant here.
    document = _parse_snapshot(snapshot)
    operation_key = corpus_operation_key(
        expected_release_sha=expected_release_sha,
        seed=seed,
        source_id=metadata.id,
        destination_folder_id=destination_folder_id,
        mode=mode,
        cohort=cohort,
    )
    return SourceEvidence(
        ordinal=ordinal,
        source_id=metadata.id,
        drive_id=metadata.drive_id,
        parent_id=metadata.parents[0],
        modified_time=metadata.modified_time,
        revision_id=str(snapshot.revision_id),
        semantic_sha256=document.semantic_sha256,
        permission_inventory=source_permissions,
        permission_sha256=permission_inventory_sha256(source_permissions),
        parent_permission_inventory=parent_permissions,
        parent_permission_sha256=permission_inventory_sha256(parent_permissions),
        operation_key=operation_key,
        output_name=_output_name(expected_release_sha, seed, ordinal, cohort=cohort),
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
    source_id: FileId,
    destination_folder_id: FileId,
    mode: TransformMode,
    cohort: QaCohort,
) -> uuid.UUID:
    return uuid.uuid5(
        _QA_NAMESPACE,
        f"{expected_release_sha}\0{seed}\0{source_id}\0{destination_folder_id}"
        f"\0{mode.value}\0{cohort}",
    )


def _list_folder_doc_metadata(
    drive_client: DriveResource, source_folder_id: FileId
) -> list[dict[str, Any]]:
    budget = _DriveTraversalBudget()
    raw_root = _get_file_metadata(drive_client, source_folder_id)
    budget.record_metadata(raw_root)
    root = _parse_file_metadata(raw_root)
    if root.mime_type != MIME_FOLDER:
        _fail("source folder is not a Google Drive folder")
    pending = [root.id]
    visited: set[FileId] = set()
    documents: list[dict[str, Any]] = []
    while pending:
        folder_id = pending.pop(0)
        if folder_id in visited:
            _fail("Drive folder traversal contained a cycle")
        budget.record_folder()
        visited.add(folder_id)
        children = _list_folder_children(
            drive_client,
            folder_id,
            shared_drive_id=(
                None if (shared_drive_id := root.drive_id) is None else DriveId(shared_drive_id)
            ),
            budget=budget,
        )
        for child in children:
            metadata = _parse_file_metadata(child)
            if metadata.drive_id != root.drive_id:
                _fail("source folder traversal crossed Drive boundaries")
            if metadata.mime_type == MIME_FOLDER:
                pending.append(metadata.id)
            elif metadata.mime_type == MIME_DOC:
                documents.append(child)
                if len(documents) > required_document_count("corpus"):
                    _fail("Drive folder traversal exceeded the 20-document corpus limit")
    return documents


def _require_current_source_set(
    drive_client: DriveResource,
    manifest: EvidenceManifest,
) -> None:
    root = _parse_file_metadata(_get_file_metadata(drive_client, manifest.source_folder_id))
    if root.mime_type != MIME_FOLDER or root.drive_id != manifest.source_drive_id:
        _fail("source folder Drive binding changed since corpus planning")
    current = _list_folder_doc_metadata(drive_client, manifest.source_folder_id)
    current_ids = tuple(FileId(str(item.get("id", ""))) for item in current)
    if (
        any(not file_id for file_id in current_ids)
        or len(current_ids) != len(set(current_ids))
        or set(current_ids) != set(manifest.folder_source_ids)
    ):
        _fail("complete source set changed since corpus planning")


def _list_folder_children(
    drive_client: DriveResource,
    folder_id: FileId,
    *,
    shared_drive_id: DriveId | None,
    budget: _DriveTraversalBudget | None = None,
) -> list[dict[str, Any]]:
    budget = budget or _DriveTraversalBudget()
    files: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        budget.reserve_page()
        request = drive_client.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            spaces="drive",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            pageSize=100,
            pageToken=page_token,
            fields=f"nextPageToken,incompleteSearch,files({_file_fields()})",
            corpora="drive" if shared_drive_id is not None else "user",
            driveId=shared_drive_id,
        )
        raw = execute_with_retry(request)
        if not isinstance(raw, dict):
            _fail("Drive returned invalid folder metadata")
        budget.record_page_metadata(raw)
        if raw.get("incompleteSearch") is True:
            _fail("Drive returned an incomplete folder search")
        page_files = raw.get("files", [])
        if not isinstance(page_files, list) or not all(
            isinstance(item, dict) for item in page_files
        ):
            _fail("Drive returned invalid folder metadata")
        files.extend(page_files)
        next_token = raw.get("nextPageToken")
        if not next_token:
            return files
        if not isinstance(next_token, str):
            _fail("Drive returned an invalid folder page token")
        page_token = next_token


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
        "capabilities(canEdit,canCopy,canAddChildren),owners(emailAddress),appProperties"
    )


def _parse_file_metadata(raw: dict[str, Any]) -> FileMetadata:
    normalized = dict(raw)
    capabilities = normalized.pop("capabilities", {})
    owners = normalized.pop("owners", [])
    normalized["can_edit"] = (
        capabilities.get("canEdit", False) if isinstance(capabilities, dict) else False
    )
    normalized["can_copy"] = (
        capabilities.get("canCopy", False) if isinstance(capabilities, dict) else False
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
    for _page in range(QA_MAX_PERMISSION_LIST_PAGES):
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


def _require_copyable_source(
    metadata: FileMetadata,
    *,
    expected_drive_id: DriveId | None,
) -> None:
    if metadata.mime_type != MIME_DOC:
        _fail("wrong_mime_type")
    if metadata.drive_id != expected_drive_id:
        _fail("source_drive_changed")
    if not metadata.can_copy:
        _fail("source_not_copyable")


def _require_source_parent(
    metadata: FileMetadata,
    *,
    expected_drive_id: DriveId | None,
) -> None:
    if metadata.mime_type != MIME_FOLDER:
        _fail("wrong_parent_mime_type")
    if metadata.drive_id != expected_drive_id:
        _fail("source_parent_drive_changed")


async def _assert_private_destination(
    drive_client: DriveResource,
    destination_folder_id: FileId,
    user_email: str,
    *,
    expected_permissions: tuple[PermissionEntry, ...] | None = None,
) -> tuple[PermissionEntry, ...]:
    metadata = _parse_file_metadata(
        await asyncio.to_thread(_get_file_metadata, drive_client, destination_folder_id)
    )
    _require_private_file(metadata, user_email, expected_mime=MIME_FOLDER)
    if not metadata.can_add_children:
        _fail("destination_not_writable")
    permissions = await asyncio.to_thread(
        _permission_inventory,
        drive_client,
        destination_folder_id,
    )
    _require_private_permissions(permissions, user_email)
    if expected_permissions is not None and permissions != expected_permissions:
        _fail("destination permissions changed since corpus planning")
    return permissions


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
    expected_release_sha: str,
    artifacts_dir: Path,
    renderer_version: str,
    judge_model: str | None,
) -> list[EvidenceResult]:
    results = _verified_results(ledger)
    tasks = _submitted_tasks(ledger)
    for source in manifest.sources:
        resumed = results.get(source.operation_key)
        if resumed is not None:
            _require_result_binding(
                source,
                resumed,
                destination_folder_id=manifest.destination_folder_id,
                expected_release_sha=expected_release_sha,
                artifacts_dir=artifacts_dir,
                renderer_version=renderer_version,
                judge_model=judge_model,
                user_email=user_email,
            )
            continue
        try:
            result = await _execute_source(
                source,
                destination_folder_id=manifest.destination_folder_id,
                destination_permissions=manifest.destination_permission_inventory,
                mode=manifest.mode,
                tasks=tasks,
                ledger_path=ledger_path,
                http=http,
                token=token,
                drive_client=drive_client,
                gateway=gateway,
                user_email=user_email,
                expected_release_sha=expected_release_sha,
                artifacts_dir=artifacts_dir,
                renderer_version=renderer_version,
                judge_model=judge_model,
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
    destination_folder_id: FileId,
    destination_permissions: tuple[PermissionEntry, ...],
    mode: TransformMode,
    tasks: dict[uuid.UUID, TaskId],
    ledger_path: Path,
    http: httpx.AsyncClient,
    token: str,
    drive_client: DriveResource,
    gateway: GoogleDocumentGateway,
    user_email: str,
    expected_release_sha: str,
    artifacts_dir: Path,
    renderer_version: str,
    judge_model: str | None,
) -> EvidenceResult:
    release_sha_before = await _verify_release(http, expected_release_sha)
    before_state = await _assert_source_unchanged(
        source,
        drive_client=drive_client,
        gateway=gateway,
        user_email=user_email,
    )
    await _assert_private_destination(
        drive_client,
        destination_folder_id,
        user_email,
        expected_permissions=destination_permissions,
    )
    task_id = tasks.get(source.operation_key)
    if task_id is None:
        submission = await _submit_transform(
            http,
            token,
            mode,
            source,
            destination_folder_id=destination_folder_id,
        )
        await _verify_release(http, expected_release_sha)
        task_id = submission.task.id
        tasks[source.operation_key] = task_id
        _record_submission(ledger_path, source, task_id, replayed=submission.replayed)
        if source.ordinal == 1:
            await _require_idempotent_replay(
                http,
                token,
                mode,
                source,
                task_id,
                destination_folder_id=destination_folder_id,
            )
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
    result = await _verify_result_and_drive(
        source=source,
        destination_folder_id=destination_folder_id,
        destination_permissions=destination_permissions,
        before_snapshot=before_state.snapshot,
        transform=transform,
        task_id=task_id,
        mode=mode,
        drive_client=drive_client,
        gateway=gateway,
        user_email=user_email,
        release_sha_before=release_sha_before,
        artifacts_dir=artifacts_dir,
        renderer_version=renderer_version,
        judge_model=judge_model,
    )
    release_sha_after = await _verify_release(http, expected_release_sha)
    return result.model_copy(update={"release_sha_after": release_sha_after})


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
    *,
    destination_folder_id: FileId,
) -> None:
    replay = await _submit_transform(
        http,
        token,
        mode,
        source,
        destination_folder_id=destination_folder_id,
    )
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
    *,
    destination_folder_id: FileId,
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
            "output_strategy": "copy",
            "copy_destination_folder_id": destination_folder_id,
        },
    )
    if response.status_code != httpx.codes.ACCEPTED:
        _fail(f"transform submission returned HTTP {response.status_code}")
    task = _TaskReadPayload.model_validate_json(response.text).root
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
        task = _TaskReadPayload.model_validate_json(response.text).root
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
    del user_email
    _require_copyable_source(metadata, expected_drive_id=source.drive_id)
    parent = _parse_file_metadata(
        await asyncio.to_thread(_get_file_metadata, drive_client, source.parent_id)
    )
    _require_source_parent(parent, expected_drive_id=source.drive_id)
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
        _fail("source or source parent changed since corpus planning")
    return VerifiedSourceState(snapshot, document, permissions, parent_permissions)


async def _verify_result_and_drive(
    *,
    source: SourceEvidence,
    destination_folder_id: FileId,
    destination_permissions: tuple[PermissionEntry, ...],
    before_snapshot: SourceSnapshot,
    transform: DocumentTransformResult,
    task_id: TaskId,
    mode: TransformMode,
    drive_client: DriveResource,
    gateway: GoogleDocumentGateway,
    user_email: str,
    release_sha_before: str,
    artifacts_dir: Path,
    renderer_version: str,
    judge_model: str | None,
) -> EvidenceResult:
    _require_transform_result(transform, task_id=task_id, mode=mode)
    after_state = await _assert_source_unchanged(
        source,
        drive_client=drive_client,
        gateway=gateway,
        user_email=user_email,
    )
    await _assert_private_destination(
        drive_client,
        destination_folder_id,
        user_email,
        expected_permissions=destination_permissions,
    )
    output_id = transform.output.file_id
    metadata = _parse_file_metadata(
        await asyncio.to_thread(_get_file_metadata, drive_client, output_id)
    )
    _require_private_file(metadata, user_email, expected_mime=MIME_DOC)
    if metadata.parents != (destination_folder_id,) or metadata.name != source.output_name:
        _fail("output is not in the exact private destination from the evidence manifest")
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
    visual_evidence, visual_judge = await _capture_visual_evidence(
        source=source,
        source_id=source.source_id,
        output_id=output_id,
        drive_client=drive_client,
        artifacts_dir=artifacts_dir,
        renderer_version=renderer_version,
        judge_model=judge_model,
    )
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
        release_sha_before=release_sha_before,
        release_sha_after=release_sha_before,
        visual_evidence=visual_evidence,
        visual_judge=visual_judge,
        verified_at=datetime.now(UTC),
    )


async def _capture_visual_evidence(
    *,
    source: SourceEvidence,
    source_id: FileId,
    output_id: FileId,
    drive_client: DriveResource,
    artifacts_dir: Path,
    renderer_version: str,
    judge_model: str | None,
) -> VisualCaptureResult:
    evidence = await asyncio.to_thread(
        _capture_visual_evidence_sync,
        source=source,
        source_id=source_id,
        output_id=output_id,
        drive_client=drive_client,
        artifacts_dir=artifacts_dir,
        renderer_version=renderer_version,
    )
    judge = (
        await _judge_visual_evidence(evidence, artifacts_dir=artifacts_dir, model=judge_model)
        if judge_model is not None
        else None
    )
    _require_visual_judge(evidence, judge, judge_model=judge_model)
    return VisualCaptureResult(evidence=evidence, judge=judge)


def _require_result_binding(
    source: SourceEvidence,
    result: EvidenceResult,
    *,
    destination_folder_id: FileId,
    expected_release_sha: str,
    artifacts_dir: Path | None,
    renderer_version: str | None,
    judge_model: str | None,
    user_email: str,
) -> None:
    evidence = result.visual_evidence
    checks = (
        result.source_id == source.source_id,
        result.source_modified_time_after == source.modified_time,
        result.source_revision_id_after == source.revision_id,
        result.source_semantic_sha256_after == source.semantic_sha256,
        result.source_permission_inventory_after == source.permission_inventory,
        result.source_permission_sha256_after == source.permission_sha256,
        result.output_parent_id == destination_folder_id,
        result.output_id != source.source_id,
        result.release_sha_before == expected_release_sha,
        result.release_sha_after == expected_release_sha,
        result.applied_patch_count == result.deterministic_patch_count + result.model_patch_count,
        min(
            result.applied_patch_count,
            result.deterministic_patch_count,
            result.model_patch_count,
        )
        >= 0,
        permission_inventory_sha256(result.output_permission_inventory)
        == result.output_permission_sha256,
        bool(evidence.source_screenshots),
        bool(evidence.output_screenshots),
        renderer_version is None or evidence.renderer_version == renderer_version,
    )
    if not all(checks):
        _fail("verified result is not bound to its source, release, and visual evidence")
    _require_private_permissions(result.output_permission_inventory, user_email)
    _require_visual_artifact_layout(source, evidence)
    if artifacts_dir is not None:
        for artifact in _all_visual_artifacts(evidence):
            _require_artifact_file(artifact, artifacts_dir=artifacts_dir)
    _require_visual_judge(evidence, result.visual_judge, judge_model=judge_model)


def _require_visual_artifact_layout(source: SourceEvidence, evidence: VisualEvidence) -> None:
    prefix = f"{source.ordinal:02d}-{source.operation_key}"
    expected_paths = (
        f"{prefix}/source.pdf",
        f"{prefix}/output.pdf",
        *(
            f"{prefix}/source-{ordinal:03d}.png"
            for ordinal in range(1, len(evidence.source_screenshots) + 1)
        ),
        *(
            f"{prefix}/output-{ordinal:03d}.png"
            for ordinal in range(1, len(evidence.output_screenshots) + 1)
        ),
    )
    artifacts = _all_visual_artifacts(evidence)
    if (
        tuple(artifact.relative_path for artifact in artifacts) != expected_paths
        or len({artifact.relative_path for artifact in artifacts}) != len(artifacts)
        or not all(_is_sha256(artifact.sha256) for artifact in artifacts)
    ):
        _fail("visual artifact layout is incomplete or unsafe")


def _all_visual_artifacts(evidence: VisualEvidence) -> tuple[ArtifactFile, ...]:
    return (
        evidence.source_pdf,
        evidence.output_pdf,
        *evidence.source_screenshots,
        *evidence.output_screenshots,
    )


def _require_artifact_file(artifact: ArtifactFile, *, artifacts_dir: Path) -> None:
    if artifacts_dir.is_symlink() or artifacts_dir.stat().st_mode & 0o077:
        _fail("visual artifact root permissions are not private")
    root = artifacts_dir.resolve()
    candidate = root / artifact.relative_path
    if candidate.is_symlink():
        _fail("visual artifact cannot be a symbolic link")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        _fail("visual artifact is missing or outside its private root")
    if resolved.stat().st_mode & 0o077:
        _fail("visual artifact permissions are not private")
    content = resolved.read_bytes()
    if len(content) != artifact.byte_size or _sha256_bytes(content) != artifact.sha256:
        _fail("visual artifact size or digest changed")


def _require_visual_judge(
    evidence: VisualEvidence,
    judge: VisualJudgeEvidence | None,
    *,
    judge_model: str | None,
) -> None:
    if judge_model is None:
        if judge is not None:
            _fail("visual judge evidence is not bound to this manifest")
        return
    selected_source = _evenly_sample(
        evidence.source_screenshots,
        _MAX_JUDGE_SCREENSHOTS // 2,
    )
    selected_output = _evenly_sample(
        evidence.output_screenshots,
        _MAX_JUDGE_SCREENSHOTS // 2,
    )
    expected_hashes = tuple(artifact.sha256 for artifact in (*selected_source, *selected_output))
    judge_checks = (
        judge is not None,
        judge is not None and judge.model_id == judge_model,
        judge is not None and judge.verdict == "pass",
        judge is not None and judge.comparison != "regressed",
        judge is not None and not judge.issue_codes,
        judge is not None
        and min(judge.typography_score, judge.spacing_score, judge.hierarchy_score)
        >= _MIN_VISUAL_SCORE,
        judge is not None and judge.judged_screenshot_sha256 == expected_hashes,
    )
    if not all(judge_checks):
        _fail("visual judge evidence is absent, unbound, or requested review")


def _capture_visual_evidence_sync(
    *,
    source: SourceEvidence,
    source_id: FileId,
    output_id: FileId,
    drive_client: DriveResource,
    artifacts_dir: Path,
    renderer_version: str,
) -> VisualEvidence:
    _ensure_private_directory(artifacts_dir)
    capture_dir = artifacts_dir / f"{source.ordinal:02d}-{source.operation_key}"
    _ensure_private_directory(capture_dir)
    source_pdf = capture_dir / "source.pdf"
    output_pdf = capture_dir / "output.pdf"
    _write_private_bytes(source_pdf, _export_pdf(drive_client, source_id))
    _write_private_bytes(output_pdf, _export_pdf(drive_client, output_id))
    source_images = _render_pdf(source_pdf, capture_dir=capture_dir, stem="source")
    output_images = _render_pdf(output_pdf, capture_dir=capture_dir, stem="output")
    return VisualEvidence(
        source_pdf=_artifact_file(source_pdf, root=artifacts_dir),
        output_pdf=_artifact_file(output_pdf, root=artifacts_dir),
        source_screenshots=tuple(
            _artifact_file(path, root=artifacts_dir) for path in source_images
        ),
        output_screenshots=tuple(
            _artifact_file(path, root=artifacts_dir) for path in output_images
        ),
        renderer_version=renderer_version,
    )


def _export_pdf(drive_client: DriveResource, file_id: FileId) -> bytes:
    raw = execute_with_retry(drive_client.files().export(fileId=file_id, mimeType=_PDF_MIME_TYPE))
    if not isinstance(raw, bytes) or not raw or len(raw) > _MAX_EXPORTED_PDF_BYTES:
        _fail("Drive PDF export was empty or exceeded the evidence size limit")
    return raw


def _write_private_bytes(path: Path, content: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def _render_pdf(pdf: Path, *, capture_dir: Path, stem: str) -> tuple[Path, ...]:
    executable = shutil.which("pdftoppm")
    if executable is None:  # pragma: no cover - checked before execution
        _fail("pdftoppm is unavailable")
    pdf_info = _read_pdf_info(pdf)
    with tempfile.TemporaryDirectory(dir=capture_dir) as temporary_name:
        prefix = Path(temporary_name) / stem
        try:
            completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - validated executable
                [
                    executable,
                    "-png",
                    "-r",
                    str(_SCREENSHOT_DPI),
                    str(pdf),
                    str(prefix),
                ],
                check=False,
                capture_output=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            message = "pdftoppm exceeded the bounded render timeout"
            raise QaError(message) from None
        if completed.returncode != 0:
            _fail("pdftoppm could not render a private PDF artifact")
        rendered = sorted(Path(temporary_name).glob(f"{stem}-*.png"))
        if len(rendered) != pdf_info.pages:
            _fail("pdftoppm did not produce exactly one screenshot per PDF page")
        sizes = tuple(path.stat().st_size for path in rendered)
        if any(size <= 0 or size > _MAX_SCREENSHOT_BYTES for size in sizes):
            _fail("a rendered screenshot exceeded the evidence size limit")
        if sum(sizes) > _MAX_RENDERED_BYTES:
            _fail("rendered screenshots exceeded the aggregate evidence size limit")
        final: list[Path] = []
        for ordinal, path in enumerate(rendered, start=1):
            destination = capture_dir / f"{stem}-{ordinal:03d}.png"
            _write_private_bytes(destination, path.read_bytes())
            final.append(destination)
    return tuple(final)


def _read_pdf_info(pdf: Path) -> PdfInfo:
    executable = shutil.which("pdfinfo")
    if executable is None:
        _fail("pdfinfo is required to bound screenshot rendering")
    try:
        completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - validated executable
            [executable, "-box", str(pdf)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        message = "pdfinfo exceeded the bounded inspection timeout"
        raise QaError(message) from None
    if completed.returncode != 0:
        _fail("pdfinfo could not inspect a private PDF artifact")
    page_match = re.search(r"(?m)^Pages:\s+(\d+)\s*$", completed.stdout)
    size_matches = re.findall(
        r"(?m)^Page(?:\s+\d+)? size:\s+([0-9.]+)\s+x\s+([0-9.]+)\s+pts",
        completed.stdout,
    )
    if page_match is None or not size_matches:
        _fail("pdfinfo returned incomplete page bounds")
    pages = int(page_match.group(1))
    sizes = tuple((float(width), float(height)) for width, height in size_matches)
    if not 1 <= pages <= _MAX_PDF_PAGES:
        _fail("PDF page count exceeded the screenshot evidence limit")
    if any(
        width <= 0 or height <= 0 or width > _MAX_PAGE_POINTS or height > _MAX_PAGE_POINTS
        for width, height in sizes
    ):
        _fail("PDF page dimensions exceeded the screenshot evidence limit")
    return PdfInfo(pages=pages, page_sizes_points=sizes)


def _artifact_file(path: Path, *, root: Path) -> ArtifactFile:
    content = path.read_bytes()
    return ArtifactFile(
        relative_path=str(path.relative_to(root)),
        sha256=_sha256_bytes(content),
        byte_size=len(content),
    )


async def _judge_visual_evidence(
    evidence: VisualEvidence,
    *,
    artifacts_dir: Path,
    model: str,
) -> VisualJudgeEvidence:
    project = settings.GOOGLE_CLOUD_PROJECT
    if project is None:
        _fail("GOOGLE_CLOUD_PROJECT is required for the optional visual judge")
    per_document_limit = _MAX_JUDGE_SCREENSHOTS // 2
    selected_source = _evenly_sample(evidence.source_screenshots, per_document_limit)
    selected_output = _evenly_sample(evidence.output_screenshots, per_document_limit)
    parts: list[genai_types.Part | str] = [
        (
            "Compare the SOURCE pages with the SARJ OUTPUT pages for visual document quality. "
            "Do not quote or transcribe document text. Mark comparison improved, equivalent, or "
            "regressed. Score the output typography, spacing, and hierarchy from 1 to 5. Return "
            "review for visible clipping, collision, crowding, logo, spacing, typography, weak "
            "hierarchy, any regression, or any score below 4. Output the exact JSON schema."
        )
    ]
    parts.append("SOURCE PAGES")
    for artifact in selected_source:
        path = artifacts_dir / artifact.relative_path
        parts.append(genai_types.Part.from_bytes(data=path.read_bytes(), mime_type="image/png"))
    parts.append("SARJ OUTPUT PAGES")
    for artifact in selected_output:
        path = artifacts_dir / artifact.relative_path
        parts.append(genai_types.Part.from_bytes(data=path.read_bytes(), mime_type="image/png"))
    try:
        client = genai.Client(vertexai=True, project=project, location="global")
        async with asyncio.timeout(60):
            response = await client.aio.models.generate_content(
                model=model,
                contents=parts,
                config=genai_types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                    response_schema=JudgeResponse,
                    http_options=genai_types.HttpOptions(timeout=60_000),
                ),
            )
        judged = _parse_judge_response(response.parsed, response.text)
    except QaError:
        raise
    except Exception:
        message = "visual judge request or response failed"
        raise QaError(message) from None
    return VisualJudgeEvidence(
        model_id=model,
        verdict=judged.verdict,
        comparison=judged.comparison,
        typography_score=judged.typography_score,
        spacing_score=judged.spacing_score,
        hierarchy_score=judged.hierarchy_score,
        issue_codes=tuple(sorted(set(judged.issue_codes))),
        judged_screenshot_sha256=tuple(
            item.sha256 for item in (*selected_source, *selected_output)
        ),
    )


def _parse_judge_response(parsed: object, text: str | None) -> JudgeResponse:
    try:
        if isinstance(parsed, JudgeResponse):
            return parsed
        return JudgeResponse.model_validate_json(text or "")
    except TypeError, ValueError:
        message = "visual judge response failed strict validation"
        raise QaError(message) from None


def _evenly_sample(
    artifacts: tuple[ArtifactFile, ...],
    limit: int,
) -> tuple[ArtifactFile, ...]:
    if len(artifacts) <= limit:
        return artifacts
    indexes = tuple(round(index * (len(artifacts) - 1) / (limit - 1)) for index in range(limit))
    return tuple(artifacts[index] for index in indexes)


def _require_pdftoppm(expected_version: str) -> str:
    executable = shutil.which("pdftoppm")
    if executable is None:
        _fail("pdftoppm is required to render screenshot evidence")
    if shutil.which("pdfinfo") is None:
        _fail("pdfinfo is required to bound screenshot rendering")
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - validated executable
        [executable, "-v"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    version_line = (completed.stderr or completed.stdout).splitlines()
    if completed.returncode != 0 or not version_line:
        _fail("pdftoppm version could not be verified")
    observed = version_line[0].removeprefix("pdftoppm version ").strip()
    if observed != expected_version:
        _fail("pdftoppm does not match the explicitly pinned version")
    return observed


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
        _fail("production result failed the exact transform telemetry contract")


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
    _ensure_private_directory(path.parent)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(record.model_dump_json())
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)


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
        if record.event == "failed" and record.operation_key in results:
            _fail("ledger contains a failure after a verified result")
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
    _ensure_private_directory(path.parent)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(f"{manifest.model_dump_json(indent=2)}\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def _ensure_private_directory(path: Path) -> None:
    if path.is_symlink():
        _fail("evidence directory must already be private")
    if path.exists():
        if not path.is_dir() or path.stat().st_mode & 0o077:
            _fail("evidence directory must already be private")
        return
    path.mkdir(parents=True, mode=0o700)
    path.chmod(0o700)


def _output_name(
    expected_release_sha: str,
    seed: str,
    ordinal: int,
    *,
    cohort: QaCohort,
) -> str:
    label = "CANARY" if cohort == "canary" else "CORPUS"
    return (
        f"[QA {label} {expected_release_sha[:12]} {_sha256_text(seed)[:8]} {ordinal:02d}] "
        "Sarj polished"
    )


def _safe_rejection(exc: Exception) -> str:
    if isinstance(exc, QaError):
        detail = re.sub(r"[^A-Za-z0-9_-]+", "_", str(exc)).strip("_")
        if detail:
            return detail[:120]
    return type(exc).__name__


def _is_pinned_judge_model(model: str) -> bool:
    return model in _APPROVED_JUDGE_MODELS


def _is_sha256(value: str | None) -> bool:
    return bool(value and re.fullmatch(r"[0-9a-f]{64}", value))


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode())


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class _TaskReadPayload(RootModel[TaskRead]):
    pass


if __name__ == "__main__":
    CLI()
