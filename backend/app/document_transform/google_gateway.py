from __future__ import annotations

import asyncio
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass, field as dataclass_field, replace
from functools import cache
import hashlib
from http import HTTPStatus
from io import BytesIO
import json
import threading
from typing import TYPE_CHECKING, Any, Protocol, TypeIs, override, runtime_checkable
import uuid

from googleapiclient import discovery
from googleapiclient.discovery_cache import get_static_doc
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpRequest, MediaIoBaseDownload, MediaIoBaseUpload
import httplib2
from loguru import logger

from app.document_transform.brand import (
    SarjBrandProfile,
    paragraph_color,
    paragraph_font_size,
    paragraph_keep_with_next,
    paragraph_named_style,
    paragraph_spacing,
)
from app.document_transform.brand_manifest import (
    CURRENT_BRAND_MANIFEST,
    HEADER_TITLE_SEPARATOR,
    normalize_header_title,
)
from app.document_transform.bullet_stage_diagnostics import log_bullet_stage
from app.document_transform.bullet_style_projection import (
    align_branded_bullet_metadata,
    bullet_style_diagnostics,
    project_branded_bullet_styles,
)
from app.document_transform.copy_font_defense import verify_copy_fonts
from app.document_transform.copy_identity import (
    align_verified_copy_list_metadata,
    normalize_copy_list_identity,
)
from app.document_transform.copy_visual_authority import verify_copy_visual_authority
from app.document_transform.custom_furniture import (
    MAX_FURNITURE_BYTES,
    CustomFurnitureBinding,
    CustomHeaderTitleTarget,
    furniture_bindings_known,
    furniture_context_values,
)
from app.document_transform.docx_renderer import markdown_import_manifest
from app.document_transform.errors import (
    ActiveSuggestionsNeedsReviewError,
    DocumentGatewayError,
    IncompatiblePendingOutputError,
    InPlaceNeedsReviewError,
    InPlaceNotAllowedError,
    SourceChangedError,
    SourceTooLargeError,
    UnsupportedSourceError,
)
from app.document_transform.gateway import (
    DocumentGateway,
    InPlaceInspection,
    InPlaceStageResult,
    OutputDraft,
    OutputVerification,
    SourceSnapshot,
    TabHeader,
)
from app.document_transform.header_provenance import (
    HeaderIdentityEvidence,
    HeaderIdentityProof,
    ManagedHeaderIdentity,
)
from app.document_transform.ir import (
    MAX_DOC_TABS,
    BlockKind,
    DocumentIR,
    TextBlock,
    parse_google_document,
    parse_markdown,
    rewrite_markdown,
    utf16_length,
)
from app.document_transform.limits import MAX_MARKDOWN_BYTES
from app.document_transform.markdown_import import (
    markdown_import_brand_diagnostics,
    verify_markdown_import,
)
from app.document_transform.naming import suggested_document_title
from app.document_transform.native_date_flow import (
    NativeDateCheckpoint,
    date_invariant_sha256,
    plan_copy_dates,
    verified_date_projection,
    verify_dates_unchanged,
)
from app.document_transform.native_font_defense import (
    apply_native_font_preservation,
    preserved_native_font_blocks,
)
from app.document_transform.native_font_proof import (
    capture_native_font_proof,
    verify_native_font_proof,
    verify_native_unstyled_text,
)
from app.document_transform.native_footer import FooterAction, FooterCheckpoint, plan_native_footer
from app.document_transform.native_footer_flow import (
    NativeFooterCheckpoint,
    NativeFooterCoverage,
    NativeFooterSkip,
    NativeFooterStage,
    footer_document_tab,
    footer_invariant_sha256,
    verified_footer_coverage_projection,
    verified_footer_projection,
)
from app.document_transform.native_pagination import (
    apply_native_pagination,
    existing_native_pagination,
)
from app.document_transform.native_paragraph_layout import (
    apply_paragraph_layout,
    capture_paragraph_layout,
    paragraph_layout_sha256,
)
from app.document_transform.native_safety_diagnostics import SafetyStage, native_safety_error
from app.document_transform.native_table_font import TableFontPlan, project_table_fonts
from app.document_transform.native_visual_authority import (
    apply_visual_authority,
    visual_authority_block_ids,
)
from app.document_transform.provider_diagnostics import native_provider_diagnostics
from app.document_transform.schemas import (
    DocumentOutput,
    DriveFileSource,
    MarkdownSource,
    TabNamingCheckpoint,
)
from app.document_transform.source_access import native_source_access
from app.document_transform.tab_metadata import native_tab_properties, tab_naming_invariant_sha256
from app.document_transform.tab_names import plan_tab_names
from app.document_transform.tab_structure_execution import StructureProvider
from app.drive.retry import (
    ProviderDeadlineError,
    execute_download_chunk_with_retry,
    execute_mutation,
    execute_with_retry,
    is_retryable_provider_status,
    provider_http_status,
)
from app.drive.schemas import MIME_DOC, MIME_FOLDER
from app.drive.service import (
    GoogleRefreshHandler,
    build_google_credentials,
    reuse_drive_client,
)
from app.files.errors import FileNotInScopeError, FilesError
from app.files.service import FilesService
from app.identifiers import DocsRevisionId, DocsSegmentId, DocsTabId, FileId
from app.models.user import User


_PAGE_BREAK_PARAGRAPH_PART_COUNT = 2
_RGB_TOLERANCE = 1e-6


@dataclass(frozen=True, slots=True)
class ParagraphRangeKey:
    tab_id: str
    start_index: int
    end_index: int


@dataclass(frozen=True, slots=True)
class _CopyBrandVerification:
    brand_applied: bool
    supported_brand_applied: bool


@dataclass(frozen=True, slots=True)
class _InPlaceHeaderState:
    headers: tuple[TabHeader, ...]
    preserved_headers: tuple[TabHeader, ...]
    custom_furniture: tuple[CustomFurnitureBinding, ...]
    custom_header_title_targets: tuple[CustomHeaderTitleTarget, ...]
    custom_header_titles_present: tuple[CustomHeaderTitleTarget, ...]
    missing: tuple[DocsTabId, ...]
    unknown: tuple[DocsTabId, ...]


@dataclass(slots=True)
class _InPlaceHeaderStateBuilder:
    headers: list[TabHeader] = dataclass_field(default_factory=list)
    preserved_headers: list[TabHeader] = dataclass_field(default_factory=list)
    custom_furniture: list[CustomFurnitureBinding] = dataclass_field(default_factory=list)
    custom_header_title_targets: list[CustomHeaderTitleTarget] = dataclass_field(
        default_factory=list
    )
    custom_header_titles_present: list[CustomHeaderTitleTarget] = dataclass_field(
        default_factory=list
    )
    missing: list[DocsTabId] = dataclass_field(default_factory=list)
    unknown: list[DocsTabId] = dataclass_field(default_factory=list)

    def record_custom(
        self,
        binding: CustomFurnitureBinding,
        tab: dict[str, Any],
        present_title_targets: frozenset[tuple[DocsTabId, DocsSegmentId]],
    ) -> None:
        self.custom_furniture.append(binding)
        for identifier in binding.header_ids:
            target = _custom_header_title_target(tab, binding.tab_id, identifier)
            if target is None:
                if binding.tab_id not in self.unknown:
                    self.unknown.append(binding.tab_id)
                continue
            if (target.tab_id, target.header_id) in present_title_targets:
                self.custom_header_titles_present.append(target)
            elif _header_can_accept_custom_title(tab, str(identifier)):
                self.custom_header_title_targets.append(target)
            elif binding.tab_id not in self.unknown:
                self.unknown.append(binding.tab_id)

    def freeze(self) -> _InPlaceHeaderState:
        return _InPlaceHeaderState(
            headers=tuple(self.headers),
            preserved_headers=tuple(self.preserved_headers),
            custom_furniture=tuple(self.custom_furniture),
            custom_header_title_targets=tuple(self.custom_header_title_targets),
            custom_header_titles_present=tuple(self.custom_header_titles_present),
            missing=tuple(self.missing),
            unknown=tuple(self.unknown),
        )


if TYPE_CHECKING:
    from googleapiclient._apis.drive.v3.resources import DriveResource


_PROP_OPERATION = "sarjTransformOperation"
_PROP_SOURCE_HASH = "sarjTransformSourceHash"
_PROP_STATE = "sarjTransformState"
_PROP_MUTATION = "sarjTransformMutation"
_PROP_PENDING_MUTATION = "sarjTransformPendingMutation"
_PROP_MUTATION_BASE_REVISION_HASH = "sarjTransformMutationBaseHash"
_PROP_RESULT_HASH = "sarjTransformResultHash"
_PROP_VERIFIED_REVISION_HASH = "sarjTransformVerifiedRevisionHash"
_STATE_INCOMPLETE = "incomplete"
_STATE_COMPLETE = "complete"
_MARKDOWN_DOWNLOAD_CHUNK_BYTES = 64 * 1024


@runtime_checkable
class _DocumentsProvider(Protocol):
    def documents(self) -> object: ...


@runtime_checkable
class _BoundaryCallable(Protocol):
    def __call__(self, *args: object, **kwargs: object) -> object: ...


def _member(owner: object, name: str) -> object:
    member = getattr(owner, name, None)
    if member is None:
        msg = f"{type(owner).__name__} has no {name} member"
        raise TypeError(msg)
    return member


def _call_boundary(candidate: object, *args: object, **kwargs: object) -> object:
    if not isinstance(candidate, _BoundaryCallable):
        msg = "External API boundary is not callable"
        raise TypeError(msg)
    return candidate(*args, **kwargs)


def _google_http_request(candidate: object, **kwargs: object) -> HttpRequest:
    request = _call_boundary(candidate, **kwargs)
    if not isinstance(request, HttpRequest):
        msg = "Google API method did not return an HTTP request"
        raise TypeError(msg)
    return request


class GoogleDocumentGateway(DocumentGateway):
    supports_native_footer = True
    supports_native_dates = True

    @override
    async def plan_copy_dates(
        self, *, draft: OutputDraft, operation_id: uuid.UUID
    ) -> NativeDateCheckpoint | None:
        document = await asyncio.to_thread(self._get_document_sync, draft.document_id)
        if document.get("revisionId") != draft.revision_id:
            native_safety_error("date_plan", "revision")
        return plan_copy_dates(document, operation_id=operation_id)

    @override
    async def apply_copy_dates(
        self, *, document_id: FileId, checkpoint: NativeDateCheckpoint
    ) -> NativeDateCheckpoint:
        document = await asyncio.to_thread(self._get_document_sync, document_id)
        if checkpoint.declined_reason is not None:
            verify_dates_unchanged(document, checkpoint)
            return checkpoint
        if not _has_exact_named_range(document, checkpoint.marker_name, checkpoint.marker_range):
            if (
                checkpoint.complete
                or document.get("revisionId") != checkpoint.revision_id
                or date_invariant_sha256(document) != checkpoint.invariant_sha256
            ):
                native_safety_error("date_pre", "preimage")
            try:
                result = await self.apply_in_place_stage(
                    document_id=document_id,
                    required_revision_id=checkpoint.revision_id,
                    marker_name=checkpoint.marker_name,
                    marker_range=checkpoint.marker_range,
                    requests=checkpoint.requests,
                )
            except DocumentGatewayError as exc:
                if exc.http_status != HTTPStatus.BAD_REQUEST:
                    raise
                document = await asyncio.to_thread(self._get_document_sync, document_id)
                verify_dates_unchanged(document, checkpoint)
                # The executor persists this outcome before continuing. A crash
                # before that save can redispatch the rejected CAS batch, but
                # exact preimage/marker recovery still prevents duplicate edits.
                return checkpoint.model_copy(update={"declined_reason": "provider_rejected"})
            document = result.document
        verified_date_projection(document, checkpoint)
        return checkpoint.model_copy(
            update={
                "complete": True,
                "revision_id": DocsRevisionId(_required_string(document, "revisionId")),
            }
        )

    def __init__(
        self,
        *,
        user: User,
        files: FilesService,
        logo_uri: str,
        refresh_handler: GoogleRefreshHandler | None = None,
        header_identity: HeaderIdentityProof | None = None,
    ) -> None:
        self.structure_provider = StructureProvider(
            self._get_document_sync, self._write_copy_structure_sync, self._comments_absent_sync
        )
        self._user = user
        self._files = files
        self._logo_uri = logo_uri
        self._refresh_handler = refresh_handler
        self._header_identity_proof = header_identity
        self._header_identity = header_identity.evidence if header_identity is not None else None
        self.capture_header_identity = self._capture_header_identity
        self._drive_clients: dict[int, DriveResource] = {}
        self._docs_clients: dict[int, _DocumentsProvider] = {}

    def _comments_absent_sync(self, source_id: FileId) -> bool:
        token: str | None = None
        seen: set[str] = set()
        for _ in range(10):
            try:
                response = self._execute_dict(
                    _google_http_request(
                        _member(_call_boundary(_member(self._drive(), "comments")), "list"),
                        fileId=source_id,
                        fields="comments(id),nextPageToken",
                        pageSize=100,
                        includeDeleted=True,
                        **({"pageToken": token} if token else {}),
                    )
                )
            except DocumentGatewayError:
                return False
            if response.get("comments"):
                return False
            next_token: object = response.get("nextPageToken")
            if not next_token:
                return True
            if not isinstance(next_token, str) or next_token in seen:
                return False
            token = next_token
            seen.add(token)
        return False

    def _write_copy_structure_sync(self, document_id: FileId, body: dict[str, Any]) -> None:
        self._execute_mutation_dict(
            _google_http_request(
                _member(self._docs().documents(), "batchUpdate"),
                documentId=document_id,
                body=body,
            ),
        )

    @override
    async def load_drive_source(self, source: DriveFileSource) -> SourceSnapshot:
        try:
            file = await self._files.get(source.file_id)
        except FilesError as exc:
            raise DocumentGatewayError(
                retryable=is_retryable_provider_status(exc.http_status),
                http_status=exc.http_status,
            ) from exc
        if file.parent_folder_id is None:
            raise UnsupportedSourceError
        if file.mime_type == MIME_DOC:
            document, metadata = await asyncio.gather(
                asyncio.to_thread(self._get_document_sync, source.file_id),
                asyncio.to_thread(self._files_get_sync, source.file_id),
            )
            proof = getattr(self, "_header_identity_proof", None)
            if proof is not None:
                properties = metadata.get("appProperties", {})
                if not isinstance(properties, dict):
                    raise InPlaceNeedsReviewError
                if (
                    proof.evidence.file_id != source.file_id
                    or properties.get(_PROP_OPERATION) != str(proof.evidence.operation_id)
                    or properties.get(_PROP_STATE) != _STATE_COMPLETE
                    or properties.get(_PROP_RESULT_HASH) != proof.result_sha256
                    or properties.get(_PROP_VERIFIED_REVISION_HASH)
                    != proof.verified_revision_sha256
                ):
                    raise InPlaceNeedsReviewError
            revision_id = document.get("revisionId")
            if not isinstance(revision_id, str) or metadata.get("mimeType") != MIME_DOC:
                raise DocumentGatewayError
            log_bullet_stage(document, "source_read")
            access = native_source_access(metadata)
            return SourceSnapshot(
                title=_required_string(metadata, "name"),
                parent_folder_id=_required_parent(metadata),
                modified_time=_required_string(metadata, "modifiedTime"),
                revision_id=DocsRevisionId(revision_id),
                media_type=file.mime_type,
                google_document=document,
                owned_by_me=access.owned_by_me,
                can_edit=access.can_edit,
                is_personal_drive=access.is_personal_drive,
                shared_drive_id=access.shared_drive_id,
                web_url=(
                    metadata.get("webViewLink")
                    if isinstance(metadata.get("webViewLink"), str)
                    else None
                ),
            )
        if file.mime_type != "text/markdown":
            raise UnsupportedSourceError
        markdown = await asyncio.to_thread(self._download_markdown_sync, source.file_id)
        return SourceSnapshot(
            title=file.name.removesuffix(".md"),
            parent_folder_id=file.parent_folder_id,
            modified_time=file.modified_time,
            revision_id=None,
            media_type=file.mime_type,
            markdown=markdown,
        )

    @override
    async def inspect_in_place(
        self,
        *,
        snapshot: SourceSnapshot,
        title: str,
        table_font_plan: TableFontPlan | None = None,
    ) -> InPlaceInspection:
        inspection = await asyncio.to_thread(self._inspect_in_place_sync, snapshot, title)
        if table_font_plan is None:
            return inspection
        if snapshot.google_document is None:
            raise InPlaceNeedsReviewError
        return replace(
            inspection,
            protected_structure_sha256=_inventory_sha256(
                _protected_structure_inventory(
                    project_table_fonts(snapshot.google_document, table_font_plan),
                    include_native_list_text_styles=True,
                )
            ),
        )

    async def _capture_header_identity(
        self,
        *,
        draft: OutputDraft,
        operation_id: uuid.UUID,
        managed_headers: dict[str, str],
        title: str,
    ) -> HeaderIdentityEvidence | None:
        if not managed_headers:
            return None
        document = await asyncio.to_thread(self._get_document_sync, draft.document_id)
        if document.get("revisionId") != draft.revision_id:
            raise InPlaceNeedsReviewError
        identities: list[ManagedHeaderIdentity] = []
        for tab_id, header_id in managed_headers.items():
            tab = footer_document_tab(document, DocsTabId(tab_id))
            fingerprint = _managed_header_fingerprint(document, tab, expected_title=title)
            if (
                fingerprint is None
                or tab.get("documentStyle", {}).get("defaultHeaderId") != header_id
            ):
                raise InPlaceNeedsReviewError
            identities.append(
                ManagedHeaderIdentity(
                    tab_id=DocsTabId(tab_id),
                    header_id=DocsSegmentId(header_id),
                    fingerprint_sha256=fingerprint,
                )
            )
        return HeaderIdentityEvidence(
            version=2,
            operation_id=operation_id,
            file_id=draft.document_id,
            profile_sha256=CURRENT_BRAND_MANIFEST.sha256,
            title_sha256=_header_identity_title_digest(title),
            tab_ids=tuple(DocsTabId(tab_id) for tab_id, _ in _document_tabs(document)),
            headers=tuple(identities),
        )

    @override
    async def plan_tab_naming(
        self,
        *,
        document_id: FileId,
        title: str,
        operation_id: uuid.UUID,
        required_revision_id: DocsRevisionId,
        prefer_document_title: bool = False,
    ) -> TabNamingCheckpoint | None:
        document = await asyncio.to_thread(self._get_document_sync, document_id)
        if document.get("revisionId") != required_revision_id:
            native_safety_error("tab_naming_plan", "revision")
        plan = plan_tab_names(
            parse_google_document(document, title=title),
            native_tab_properties(document),
            prefer_document_title=prefer_document_title,
        )
        marker_range = _terminal_body_marker_range(document)
        if not plan.renames or marker_range is None:
            return None
        digest = hashlib.sha256(
            json.dumps([rename.request() for rename in plan.renames], sort_keys=True).encode()
        ).hexdigest()
        return TabNamingCheckpoint(
            plan=plan,
            revision_id=DocsRevisionId(_required_string(document, "revisionId")),
            marker_name=f"sarj_tabs_{operation_id.hex}_{digest[:16]}",
            marker_range=marker_range,
            invariant_sha256=tab_naming_invariant_sha256(document),
        )

    @override
    async def plan_copy_footer(
        self, *, draft: OutputDraft, operation_id: uuid.UUID
    ) -> NativeFooterCheckpoint | None:
        document = await asyncio.to_thread(self._get_document_sync, draft.document_id)
        if document.get("revisionId") != draft.revision_id:
            native_safety_error("footer_plan", "revision")
        url = f"https://wiki.sarj.ai/d/{draft.document_id}"
        topology = tuple(DocsTabId(tab_id) for tab_id, _ in _document_tabs(document))
        eligible: list[DocsTabId] = []
        skipped: list[NativeFooterSkip] = []
        for tab_id in topology:
            tab = footer_document_tab(document, tab_id)
            style = tab.get("documentStyle", {})
            reason = None
            if not isinstance(style, dict) or not isinstance(tab.get("footers", {}), dict):
                reason = "unsupported_layout"
            elif tab.get("footers") or any(
                style.get(key)
                for key in ("defaultFooterId", "firstPageFooterId", "evenPageFooterId")
            ):
                reason = "custom_footer"
            elif not isinstance(style.get("documentFormat", {}), dict):
                reason = "unsupported_layout"
            elif style.get("documentFormat", {}).get("documentMode") == "PAGELESS":
                reason = "pageless"
            elif (
                (
                    style.get("documentFormat", {}).get("documentMode") is not None
                    and style.get("documentFormat", {}).get("documentMode") != "PAGES"
                )
                or any(
                    style.get(key) is not None and style.get(key) is not False
                    for key in ("useFirstPageHeaderFooter", "useEvenPageHeaderFooter")
                )
                or (
                    plan_native_footer(
                        tab, tab_id=tab_id, revision_id=draft.revision_id, short_link=url
                    ).action
                    is not FooterAction.CREATE
                )
            ):
                reason = "unsupported_layout"
            if reason is None:
                eligible.append(tab_id)
            else:
                skipped.append(
                    NativeFooterSkip.model_validate({"tab_id": tab_id, "reason": reason})
                )
        marker_range = _terminal_body_marker_range(document)
        if marker_range is None:
            native_safety_error("footer_plan", "marker")
        first_tab = eligible[0] if eligible else draft.tab_id
        suffix = f"{operation_id.hex}_{topology.index(first_tab)}"
        return NativeFooterCheckpoint(
            stage=NativeFooterStage.CREATE if eligible else NativeFooterStage.COMPLETE,
            tab_id=first_tab,
            url=url,
            display_text="Open in Wiki",
            revision_id=draft.revision_id,
            create_marker=f"sarj_footer_create_{suffix}",
            populate_marker=f"sarj_footer_populate_{suffix}",
            marker_range=marker_range,
            invariant_sha256=footer_invariant_sha256(document),
            coverage=NativeFooterCoverage(
                topology=topology,
                pending=tuple(eligible[1:]),
                skipped=tuple(skipped),
                active=bool(eligible),
            ),
        )

    @override
    async def apply_copy_footer_stage(
        self, *, document_id: FileId, checkpoint: NativeFooterCheckpoint
    ) -> NativeFooterCheckpoint:
        document = await asyncio.to_thread(self._get_document_sync, document_id)
        coverage = checkpoint.coverage
        if coverage is not None:
            if tuple(tab_id for tab_id, _ in _document_tabs(document)) != coverage.topology:
                native_safety_error("footer_coverage", "topology")
            if checkpoint.stage is NativeFooterStage.COMPLETE:
                if not coverage.pending:
                    verified_footer_coverage_projection(document, checkpoint)
                    return checkpoint
                if document.get("revisionId") != checkpoint.revision_id:
                    native_safety_error("footer_advance", "revision")
                verified_footer_projection(document, checkpoint, populated=True)
                next_tab = coverage.pending[0]
                suffix = f"{checkpoint.create_marker.rsplit('_', 1)[0]}_{coverage.topology.index(next_tab)}"
                return NativeFooterCheckpoint(
                    tab_id=next_tab,
                    url=checkpoint.url,
                    display_text=checkpoint.display_text,
                    revision_id=checkpoint.revision_id,
                    create_marker=suffix,
                    populate_marker=suffix.replace(
                        "sarj_footer_create_", "sarj_footer_populate_", 1
                    ),
                    marker_range=checkpoint.marker_range,
                    invariant_sha256=footer_invariant_sha256(document),
                    coverage=coverage.model_copy(
                        update={
                            "pending": coverage.pending[1:],
                            "completed": (
                                *coverage.completed,
                                checkpoint.model_copy(update={"coverage": None}),
                            ),
                        }
                    ),
                )
        creating = checkpoint.stage is NativeFooterStage.CREATE
        diagnostic_stage: SafetyStage = "footer_create" if creating else "footer_populate"
        marker = checkpoint.create_marker if creating else checkpoint.populate_marker
        if not _has_exact_named_range(document, marker, checkpoint.marker_range):
            if document.get("revisionId") != checkpoint.revision_id:
                native_safety_error(diagnostic_stage, "revision")
            if creating:
                if footer_invariant_sha256(document) != checkpoint.invariant_sha256:
                    native_safety_error(diagnostic_stage, "invariant")
            else:
                verified_footer_projection(document, checkpoint, populated=False)
            plan = plan_native_footer(
                footer_document_tab(document, checkpoint.tab_id),
                tab_id=checkpoint.tab_id,
                revision_id=checkpoint.revision_id,
                short_link=checkpoint.url,
                display_text=checkpoint.display_text,
                checkpoint=FooterCheckpoint(not creating, checkpoint.footer_id),
            )
            expected_action = FooterAction.CREATE if creating else FooterAction.POPULATE
            if plan.action is not expected_action:
                native_safety_error(diagnostic_stage, "action")
            result = await self.apply_in_place_stage(
                document_id=document_id,
                required_revision_id=checkpoint.revision_id,
                marker_name=marker,
                marker_range=checkpoint.marker_range,
                requests=list(plan.requests),
            )
            document = result.document
        _, footer_id = verified_footer_projection(document, checkpoint, populated=not creating)
        return checkpoint.model_copy(
            update={
                "stage": NativeFooterStage.POPULATE if creating else NativeFooterStage.COMPLETE,
                "footer_id": footer_id,
                "revision_id": DocsRevisionId(_required_string(document, "revisionId")),
            }
        )

    @override
    async def apply_tab_naming(
        self,
        *,
        document_id: FileId,
        checkpoint: TabNamingCheckpoint,
    ) -> DocsRevisionId:
        document = await asyncio.to_thread(self._get_document_sync, document_id)
        if (
            tab_naming_invariant_sha256(document, checkpoint.marker_name)
            != checkpoint.invariant_sha256
        ):
            native_safety_error("tab_naming_pre", "invariant")
        if _has_exact_named_range(document, checkpoint.marker_name, checkpoint.marker_range):
            if not checkpoint.plan.matches_result(native_tab_properties(document)):
                native_safety_error("tab_naming_pre", "topology")
            return DocsRevisionId(_required_string(document, "revisionId"))
        if document.get(
            "revisionId"
        ) != checkpoint.revision_id or not checkpoint.plan.matches_preimage(
            native_tab_properties(document)
        ):
            native_safety_error("tab_naming_pre", "preimage")
        result = await self.apply_in_place_stage(
            document_id=document_id,
            required_revision_id=checkpoint.revision_id,
            marker_name=checkpoint.marker_name,
            marker_range=checkpoint.marker_range,
            requests=[dict(rename.request()) for rename in checkpoint.plan.renames],
        )
        if tab_naming_invariant_sha256(
            result.document, checkpoint.marker_name
        ) != checkpoint.invariant_sha256 or not checkpoint.plan.matches_result(
            native_tab_properties(result.document)
        ):
            native_safety_error("tab_naming_post", "preimage")
        return result.revision_id

    @override
    async def apply_in_place_stage(
        self,
        *,
        document_id: FileId,
        required_revision_id: DocsRevisionId,
        marker_name: str,
        marker_range: dict[str, Any],
        requests: list[dict[str, Any]],
    ) -> InPlaceStageResult:
        return await asyncio.to_thread(
            self._apply_in_place_stage_sync,
            document_id,
            required_revision_id,
            marker_name,
            marker_range,
            requests,
        )

    @override
    async def verify_in_place(
        self,
        *,
        document: dict[str, Any],
        title: str,
        expected: DocumentIR,
        structural_sha256: str,
        protected_structure_sha256: str,
        verify_brand: bool,
        custom_furniture: tuple[CustomFurnitureBinding, ...] = (),
        custom_header_titles: tuple[CustomHeaderTitleTarget, ...] = (),
        table_font_plan: TableFontPlan | None = None,
    ) -> OutputVerification:
        parsed = parse_google_document(document, title=title)
        preserved_native_list_text_style_ids = _native_list_block_ids(expected)
        content_preserved = _typography_content_preserved(
            parsed, expected, verify_brand=verify_brand
        )
        # In-place native-list typography is never written. Keep the protected
        # inventory exact instead of projecting COPY-only bullet side effects.
        structure_document = document
        table_preserved = True
        if table_font_plan is not None:
            try:
                structure_document = project_table_fonts(
                    structure_document, table_font_plan, applied=verify_brand
                )
            except KeyError, IndexError, TypeError, ValueError, AttributeError:
                table_preserved = False
        structure_preserved = (
            table_preserved
            and _inventory_sha256(_structural_inventory(document)) == structural_sha256
            and _inventory_sha256(
                _protected_structure_inventory(
                    structure_document, include_native_list_text_styles=True
                )
            )
            == protected_structure_sha256
            and _custom_furniture_matches(
                document,
                custom_furniture,
                title=title,
                custom_header_titles=custom_header_titles,
            )
        )
        preserved_layout = any(
            block.preserved_paragraph_style_sha256
            or block.preserved_font_family
            or block.preserved_visual_authority
            or block.id in preserved_native_list_text_style_ids
            for block in expected.blocks
        )
        brand_applied = not verify_brand or (
            not preserved_layout
            and verify_brand_application(
                document,
                _typography_verification_ir(parsed, expected),
                expected_header_title=_trusted_header_identity_title(
                    document, getattr(self, "_header_identity", None)
                ),
                header_separator=_owned_header_separator(getattr(self, "_header_identity", None)),
                preserved_native_list_text_style_ids=preserved_native_list_text_style_ids,
            )
        )
        supported_brand_applied = (
            (bool(custom_furniture) or preserved_layout)
            and structure_preserved
            and _custom_header_titles_match(document, title, custom_header_titles, custom_furniture)
            and (
                not verify_brand
                or verify_brand_application(
                    document,
                    _typography_verification_ir(parsed, expected),
                    expected_header_title=_trusted_header_identity_title(
                        document, getattr(self, "_header_identity", None)
                    ),
                    header_separator=_owned_header_separator(
                        getattr(self, "_header_identity", None)
                    ),
                    preserved_furniture_tabs=frozenset(
                        binding.tab_id for binding in custom_furniture
                    ),
                    preserved_header_tabs=frozenset(
                        binding.tab_id for binding in custom_furniture if binding.header_ids
                    ),
                    preserved_native_list_text_style_ids=preserved_native_list_text_style_ids,
                    allow_preserved_visual_authority=True,
                )
            )
        )
        if not (
            content_preserved and structure_preserved and (brand_applied or supported_brand_applied)
        ):
            logger.warning(
                "in-place verification failed content_preserved={content_preserved} "
                "structure_preserved={structure_preserved} brand_applied={brand_applied}",
                content_preserved=content_preserved,
                structure_preserved=structure_preserved,
                brand_applied=brand_applied,
                diagnostics={
                    "supported_brand_applied": supported_brand_applied,
                    "structural_inventory_preserved": _inventory_sha256(
                        _structural_inventory(document)
                    )
                    == structural_sha256,
                    "protected_inventory_preserved": _inventory_sha256(
                        _protected_structure_inventory(
                            structure_document, include_native_list_text_styles=True
                        )
                    )
                    == protected_structure_sha256,
                    **bullet_style_diagnostics(parsed, expected),
                },
            )
        elif supported_brand_applied and not brand_applied:
            logger.info(
                "in-place verification accepted supported brand",
                diagnostics={
                    "supported_brand_applied": True,
                    "structural_inventory_preserved": True,
                    "protected_inventory_preserved": True,
                },
            )
        return OutputVerification(
            supported_brand_applied=supported_brand_applied,
            content_preserved=content_preserved,
            structure_preserved=structure_preserved,
            brand_applied=brand_applied,
            revision_id=DocsRevisionId(_required_string(document, "revisionId")),
        )

    @override
    async def recover_output(self, operation_id: uuid.UUID) -> OutputDraft | None:
        return await asyncio.to_thread(self._recover_output_sync, operation_id)

    @override
    async def source_is_unchanged(
        self,
        source: DriveFileSource,
        *,
        expected_modified_time: str,
        expected_revision_id: DocsRevisionId | None,
        expected_parent_folder_id: FileId,
    ) -> bool:
        try:
            file = await self._files.get(source.file_id)
        except FilesError as exc:
            raise DocumentGatewayError(
                retryable=is_retryable_provider_status(exc.http_status),
                http_status=exc.http_status,
            ) from exc
        if file.modified_time != expected_modified_time:
            return False
        if file.parent_folder_id != expected_parent_folder_id:
            return False
        if expected_revision_id is None:
            return True
        document = await asyncio.to_thread(self._get_document_sync, source.file_id)
        return document.get("revisionId") == expected_revision_id

    @override
    async def create_from_drive_copy(
        self,
        *,
        operation_id: uuid.UUID,
        source: DriveFileSource,
        source_sha256: str,
        output_name: str,
        parent_folder_id: FileId,
    ) -> OutputDraft:
        return await asyncio.to_thread(
            self._copy_sync,
            operation_id,
            source,
            source_sha256,
            output_name,
            parent_folder_id,
        )

    @override
    async def create_from_drive_markdown(
        self,
        *,
        operation_id: uuid.UUID,
        source: DriveFileSource,
        source_sha256: str,
        output_name: str,
        parent_folder_id: FileId,
        imported_docx: bytes | None = None,
    ) -> OutputDraft:
        del source
        return await asyncio.to_thread(
            self._create_sync,
            operation_id,
            source_sha256,
            output_name,
            parent_folder_id,
            imported_docx,
        )

    @override
    async def create_from_markdown(
        self,
        *,
        operation_id: uuid.UUID,
        source: MarkdownSource,
        source_sha256: str,
        output_name: str,
        imported_docx: bytes | None = None,
    ) -> OutputDraft:
        try:
            destination = await self._files.get(source.destination_folder_id)
        except FileNotInScopeError as exc:
            raise UnsupportedSourceError from exc
        except FilesError as exc:
            raise DocumentGatewayError(
                retryable=is_retryable_provider_status(exc.http_status),
                http_status=exc.http_status,
            ) from exc
        if destination.mime_type != MIME_FOLDER or not destination.can_add_children:
            raise UnsupportedSourceError
        return await asyncio.to_thread(
            self._create_sync,
            operation_id,
            source_sha256,
            output_name,
            source.destination_folder_id,
            imported_docx,
        )

    @override
    async def prepare_brand_shell(
        self,
        *,
        operation_id: uuid.UUID,
        draft: OutputDraft,
        managed_headers: dict[str, str] | None = None,
    ) -> OutputDraft:
        return await asyncio.to_thread(
            self._prepare_brand_shell_sync, operation_id, draft, managed_headers
        )

    @override
    async def apply_requests_once(
        self,
        *,
        operation_id: uuid.UUID,
        draft: OutputDraft,
        mutation_sha256: str,
        requests: list[dict[str, Any]],
        required_revision_id: DocsRevisionId,
    ) -> OutputDraft:
        del operation_id
        if draft.applied_mutation_sha256 == mutation_sha256:
            return draft
        return await asyncio.to_thread(
            self._apply_requests_sync,
            draft,
            mutation_sha256,
            requests,
            required_revision_id,
        )

    @override
    async def verify_output(
        self,
        *,
        draft: OutputDraft,
        source: SourceSnapshot,
        expected: DocumentIR,
        verify_brand: bool,
        header_title: str | None = None,
    ) -> OutputVerification:
        return await asyncio.to_thread(
            self._verify_output_sync,
            draft,
            source,
            expected,
            verify_brand=verify_brand,
            header_title=header_title,
        )

    @override
    async def complete(
        self,
        *,
        operation_id: uuid.UUID,
        draft: OutputDraft,
        result: dict[str, Any],
    ) -> None:
        del operation_id
        result_sha256 = _json_sha256(result)
        await asyncio.to_thread(
            self._update_properties_sync,
            draft.document_id,
            {
                _PROP_STATE: _STATE_COMPLETE,
                _PROP_RESULT_HASH: result_sha256,
                _PROP_VERIFIED_REVISION_HASH: _revision_sha256(draft.revision_id),
            },
        )

    def _copy_sync(
        self,
        operation_id: uuid.UUID,
        source: DriveFileSource,
        source_sha256: str,
        output_name: str,
        parent_id: FileId,
    ) -> OutputDraft:
        name = self._unique_name_sync(parent_id, output_name)
        body = {
            "name": name,
            "parents": [parent_id],
            "appProperties": _initial_properties(operation_id, source_sha256),
        }
        try:
            raw = self._execute_mutation_dict(
                _google_http_request(
                    _member(self._drive().files(), "copy"),
                    fileId=source.file_id,
                    body=body,
                    supportsAllDrives=True,
                    fields="id,name,parents,webViewLink,appProperties",
                )
            )
        except DocumentGatewayError as exc:
            return self._recover_ambiguous_create(
                operation_id,
                source_sha256,
                expected_parent_folder_id=parent_id,
                original_error=exc,
            )
        return self._draft_from_file(raw, source_sha256=source_sha256)

    def _inspect_in_place_sync(
        self,
        snapshot: SourceSnapshot,
        title: str,
    ) -> InPlaceInspection:
        document = snapshot.google_document
        eligible = all(
            (
                document is not None,
                snapshot.media_type == MIME_DOC,
                (snapshot.owned_by_me and snapshot.is_personal_drive)
                or snapshot.shared_drive_id is not None,
                snapshot.can_edit,
                snapshot.revision_id is not None,
            )
        )
        if not eligible or document is None:
            raise InPlaceNotAllowedError
        if _has_active_suggestions(document):
            logger.info(
                "document in-place preflight checks={checks}",
                checks={"in_place_active_suggestions": True},
            )
            raise ActiveSuggestionsNeedsReviewError
        header_title = (
            _trusted_header_identity_title(document, getattr(self, "_header_identity", None))
            or title
        )
        header_state = _classify_in_place_headers(
            document,
            header_title,
            getattr(self, "_header_identity", None),
        )
        parsed = parse_google_document(document, title=title)
        existing_layout = apply_native_pagination(
            parsed, existing_native_pagination(document, parsed)
        )
        try:
            existing_layout = apply_paragraph_layout(
                existing_layout, capture_paragraph_layout(document, parsed, parsed)
            )
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as error:
            raise InPlaceNeedsReviewError from error
        existing_layout = apply_native_font_preservation(
            existing_layout,
            preserved_native_font_blocks(document, existing_layout, existing_layout),
        )
        existing_layout = apply_visual_authority(
            existing_layout, visual_authority_block_ids(document, existing_layout)
        )
        logger.info(
            "document in-place preflight checks={checks}",
            checks={
                "tab_count": len(tuple(_document_tabs(document))),
                "unknown_header_count": len(header_state.unknown),
                "missing_header_count": len(header_state.missing),
                "in_place_logo_available": bool(self._logo_uri),
                "in_place_marker_available": _terminal_body_marker_range(document) is not None,
            },
        )
        return InPlaceInspection(
            custom_furniture=header_state.custom_furniture,
            custom_header_title_targets=header_state.custom_header_title_targets,
            custom_header_titles_present=header_state.custom_header_titles_present,
            supported_brand_applied=bool(
                header_state.custom_furniture
                or any(
                    block.preserved_paragraph_style_sha256
                    or block.preserved_font_family
                    or block.preserved_visual_authority
                    or block.native_bullet_json is not None
                    for block in existing_layout.blocks
                )
            )
            and not (header_state.missing or header_state.unknown or header_state.headers)
            and not header_state.custom_header_title_targets
            and verify_brand_application(
                document,
                existing_layout,
                expected_header_title=header_title,
                header_separator=_owned_header_separator(getattr(self, "_header_identity", None)),
                preserved_furniture_tabs=frozenset(
                    binding.tab_id for binding in header_state.custom_furniture
                ),
                preserved_header_tabs=frozenset(
                    binding.tab_id
                    for binding in header_state.custom_furniture
                    if binding.header_ids
                ),
                preserved_native_list_text_style_ids=_native_list_block_ids(existing_layout),
            ),
            output=DocumentOutput(
                file_id=FileId(_required_string(document, "documentId")),
                name=snapshot.title,
                parent_folder_id=snapshot.parent_folder_id,
                web_url=snapshot.web_url,
            ),
            headers=header_state.headers,
            preserved_headers=header_state.preserved_headers,
            header_identity=getattr(self, "_header_identity", None),
            fully_branded=not any(
                block.preserved_paragraph_style_sha256
                or block.preserved_font_family
                or block.preserved_visual_authority
                or block.native_bullet_json is not None
                for block in existing_layout.blocks
            )
            and not (header_state.missing or header_state.unknown or header_state.headers)
            and verify_brand_application(
                document,
                existing_layout,
                expected_header_title=header_title,
                header_separator=_owned_header_separator(getattr(self, "_header_identity", None)),
            ),
            missing_header_tab_ids=header_state.missing,
            unknown_header_tab_ids=header_state.unknown,
            marker_range=_terminal_body_marker_range(document),
            logo_uri=self._logo_uri,
            structural_sha256=_inventory_sha256(_structural_inventory(document)),
            protected_structure_sha256=_inventory_sha256(
                _protected_structure_inventory(document, include_native_list_text_styles=True)
            ),
            body_sha256=_body_sha256(document),
            header_safe_sha256=_header_safe_sha256(document),
        )

    def _apply_in_place_stage_sync(
        self,
        document_id: FileId,
        required_revision_id: DocsRevisionId,
        marker_name: str,
        marker_range: dict[str, Any],
        requests: list[dict[str, Any]],
    ) -> InPlaceStageResult:
        if not requests or not marker_name:
            raise DocumentGatewayError
        expected_marker_range = _post_mutation_marker_range(marker_range, requests)
        atomic_requests = [
            {"createNamedRange": {"name": marker_name, "range": marker_range}},
            *requests,
        ]
        try:
            self._execute_mutation_dict(
                _google_http_request(
                    _member(self._docs().documents(), "batchUpdate"),
                    documentId=document_id,
                    body={
                        "requests": atomic_requests,
                        "writeControl": {"requiredRevisionId": required_revision_id},
                    },
                )
            )
        except (DocumentGatewayError, ProviderDeadlineError) as exc:
            document = self._get_document_sync(document_id)
            logger.warning(
                "document stage provider boundary http_status={http_status} "
                "revision_unchanged={revision_unchanged} marker_checks={marker_checks} "
                "request_kinds={request_kinds} diagnostics={diagnostics}",
                diagnostics=native_provider_diagnostics(
                    exc, atomic_requests, include_envelope=True
                ),
                request_kinds=stage_request_kinds(requests),
                http_status=exc.http_status if isinstance(exc, DocumentGatewayError) else None,
                revision_unchanged=document.get("revisionId") == required_revision_id,
                marker_checks=_named_range_diagnostics(
                    document, marker_name, expected_marker_range
                ),
            )
            if _has_exact_named_range(document, marker_name, expected_marker_range):
                return InPlaceStageResult(
                    revision_id=DocsRevisionId(_required_string(document, "revisionId")),
                    document=document,
                )
            if document.get("revisionId") == required_revision_id:
                raise
            raise SourceChangedError from exc
        document = self._get_document_sync(document_id)
        if not _has_exact_named_range(document, marker_name, expected_marker_range):
            logger.warning(
                "document stage marker verification failed revision_unchanged={revision_unchanged} "
                "marker_checks={marker_checks} request_kinds={request_kinds}",
                request_kinds=stage_request_kinds(requests),
                revision_unchanged=document.get("revisionId") == required_revision_id,
                marker_checks=_named_range_diagnostics(
                    document, marker_name, expected_marker_range
                ),
            )
            raise DocumentGatewayError
        return InPlaceStageResult(
            revision_id=DocsRevisionId(_required_string(document, "revisionId")),
            document=document,
        )

    def _create_sync(
        self,
        operation_id: uuid.UUID,
        source_sha256: str,
        output_name: str,
        parent_folder_id: FileId,
        imported_docx: bytes | None,
    ) -> OutputDraft:
        name = self._unique_name_sync(parent_folder_id, output_name)
        body = {
            "name": name,
            "mimeType": MIME_DOC,
            "parents": [parent_folder_id],
            "appProperties": _initial_properties(operation_id, source_sha256),
        }
        media = (
            MediaIoBaseUpload(
                BytesIO(imported_docx),
                mimetype=(
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                resumable=False,
            )
            if imported_docx is not None
            else None
        )
        try:
            raw = self._execute_mutation_dict(
                _google_http_request(
                    _member(self._drive().files(), "create"),
                    body=body,
                    media_body=media,
                    supportsAllDrives=True,
                    fields="id,name,parents,webViewLink,appProperties",
                )
            )
        except DocumentGatewayError as exc:
            return self._recover_ambiguous_create(
                operation_id,
                source_sha256,
                expected_parent_folder_id=parent_folder_id,
                original_error=exc,
            )
        return self._draft_from_file(raw, source_sha256=source_sha256)

    def _recover_ambiguous_create(
        self,
        operation_id: uuid.UUID,
        source_sha256: str,
        *,
        expected_parent_folder_id: FileId,
        original_error: DocumentGatewayError,
    ) -> OutputDraft:
        recovered = self._recover_output_sync(operation_id)
        if recovered is None:
            raise original_error
        if (
            recovered.source_sha256 != source_sha256
            or recovered.output.parent_folder_id != expected_parent_folder_id
        ):
            raise DocumentGatewayError
        return recovered

    def _recover_output_sync(self, operation_id: uuid.UUID) -> OutputDraft | None:
        query = (
            f"appProperties has {{ key='{_PROP_OPERATION}' and value='{operation_id}' }} "
            "and trashed=false"
        )
        raw = self._execute_dict(
            self._drive()
            .files()
            .list(
                q=query,
                spaces="drive",
                pageSize=2,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                fields="files(id,name,parents,webViewLink,appProperties)",
            )
        )
        files = raw.get("files", [])
        if not isinstance(files, list) or not files:
            return None
        if len(files) != 1 or not isinstance(files[0], dict):
            raise DocumentGatewayError
        properties = files[0].get("appProperties", {})
        if not isinstance(properties, dict):
            raise DocumentGatewayError
        source_sha256 = properties.get(_PROP_SOURCE_HASH)
        if not isinstance(source_sha256, str):
            raise DocumentGatewayError
        return self._draft_from_file(files[0], source_sha256=source_sha256)

    def _draft_from_file(self, raw: dict[str, Any], *, source_sha256: str) -> OutputDraft:
        file_id = _required_string(raw, "id")
        name = _required_string(raw, "name")
        parents = raw.get("parents")
        if not isinstance(parents, list) or not parents or not isinstance(parents[0], str):
            raise DocumentGatewayError
        document = self._get_document_sync(FileId(file_id))
        log_bullet_stage(document, "draft_read")
        revision_id = _required_string(document, "revisionId")
        tab_id = _first_tab_id(document)
        properties = raw.get("appProperties", {})
        properties = properties if isinstance(properties, dict) else {}
        return OutputDraft(
            output=DocumentOutput(
                file_id=FileId(file_id),
                name=name,
                parent_folder_id=FileId(parents[0]),
                web_url=raw.get("webViewLink") if isinstance(raw.get("webViewLink"), str) else None,
            ),
            document_id=FileId(file_id),
            tab_id=tab_id,
            revision_id=DocsRevisionId(revision_id),
            source_sha256=source_sha256,
            headers=(),
            logo_uri=self._logo_uri,
            applied_mutation_sha256=_optional_property(properties, _PROP_MUTATION),
            pending_mutation_sha256=_optional_property(properties, _PROP_PENDING_MUTATION),
            mutation_base_revision_sha256=_optional_property(
                properties, _PROP_MUTATION_BASE_REVISION_HASH
            ),
            completed=properties.get(_PROP_STATE) == _STATE_COMPLETE,
            result_sha256=_optional_property(properties, _PROP_RESULT_HASH),
            verified_revision_sha256=_optional_property(properties, _PROP_VERIFIED_REVISION_HASH),
            brand_warnings=(),
        )

    def _prepare_brand_shell_sync(
        self,
        operation_id: uuid.UUID,
        draft: OutputDraft,
        managed_headers: dict[str, str] | None = None,
    ) -> OutputDraft:
        del operation_id
        if managed_headers is not None:
            return self._recover_owned_brand_shell_sync(draft, managed_headers)
        if draft.headers:
            return draft
        document = self._get_document_sync(draft.document_id)
        tabs = tuple(_document_tabs(document))
        log_bullet_stage(document, "pre_header")
        requests: list[dict[str, object]] = []
        warnings: list[str] = [] if self._logo_uri else ["logo_unavailable"]
        pending_write_may_have_committed = bool(
            draft.pending_mutation_sha256
            and draft.mutation_base_revision_sha256
            and _revision_sha256(draft.revision_id) != draft.mutation_base_revision_sha256
        )
        for tab_id, document_tab in tabs:
            style = document_tab.get("documentStyle", {})
            header_id = style.get("defaultHeaderId") if isinstance(style, dict) else None
            headers = document_tab.get("headers", {})
            if (
                draft.applied_mutation_sha256 is None
                and not pending_write_may_have_committed
                and isinstance(header_id, str)
                and _segment_has_content(headers, header_id)
            ):
                warnings.append(f"header_conflict:{tab_id}")
                continue
            if not isinstance(header_id, str):
                requests.append(
                    {
                        "createHeader": {
                            "type": "DEFAULT",
                            "sectionBreakLocation": {"index": 0, "tabId": tab_id},
                        }
                    }
                )
        if requests:
            self._execute_mutation_dict(
                _google_http_request(
                    _member(self._docs().documents(), "batchUpdate"),
                    documentId=draft.document_id,
                    body={
                        "requests": requests,
                        "writeControl": {"requiredRevisionId": draft.revision_id},
                    },
                )
            )
            document = self._get_document_sync(draft.document_id)
            tabs = tuple(_document_tabs(document))
        log_bullet_stage(document, "post_header")
        tab_headers: list[TabHeader] = []
        for tab_id, document_tab in tabs:
            style = document_tab.get("documentStyle", {})
            header_id = style.get("defaultHeaderId") if isinstance(style, dict) else None
            if not isinstance(header_id, str):
                if f"header_conflict:{tab_id}" in warnings:
                    continue
                raise DocumentGatewayError
            headers = document_tab.get("headers", {})
            if (
                draft.applied_mutation_sha256 is None
                and not pending_write_may_have_committed
                and _segment_has_content(headers, header_id)
            ):
                if f"header_conflict:{tab_id}" not in warnings:
                    warnings.append(f"header_conflict:{tab_id}")
                continue
            tab_headers.append(
                TabHeader(tab_id=DocsTabId(tab_id), header_id=DocsSegmentId(header_id))
            )
        revision_id = _required_string(document, "revisionId")
        return OutputDraft(
            output=draft.output,
            document_id=draft.document_id,
            tab_id=draft.tab_id,
            revision_id=DocsRevisionId(revision_id),
            source_sha256=draft.source_sha256,
            headers=tuple(tab_headers),
            logo_uri=self._logo_uri,
            applied_mutation_sha256=draft.applied_mutation_sha256,
            pending_mutation_sha256=draft.pending_mutation_sha256,
            mutation_base_revision_sha256=draft.mutation_base_revision_sha256,
            completed=draft.completed,
            result=draft.result,
            result_sha256=draft.result_sha256,
            verified_revision_sha256=draft.verified_revision_sha256,
            brand_warnings=tuple(warnings),
        )

    def _recover_owned_brand_shell_sync(
        self, draft: OutputDraft, managed_headers: dict[str, str]
    ) -> OutputDraft:
        # The checkpoint, including an empty ownership map, is authoritative.
        # Never infer ownership from a header populated before a later outage.
        document = self._get_document_sync(draft.document_id)
        tabs = tuple(_document_tabs(document))
        tab_ids = [tab_id for tab_id, _ in tabs]
        if len(set(tab_ids)) != len(tab_ids) or not set(managed_headers) <= set(tab_ids):
            raise SourceChangedError
        headers: list[TabHeader] = []
        warnings: list[str] = [] if self._logo_uri else ["logo_unavailable"]
        for tab_id, tab in tabs:
            style = tab.get("documentStyle")
            header_id = style.get("defaultHeaderId") if isinstance(style, dict) else None
            segments = tab.get("headers")
            if (
                not isinstance(header_id, str)
                or not isinstance(segments, dict)
                or not isinstance(segments.get(header_id), dict)
            ):
                raise SourceChangedError
            if tab_id in managed_headers:
                if managed_headers[tab_id] != header_id:
                    raise SourceChangedError
                if (
                    draft.pending_mutation_sha256 is None
                    and draft.applied_mutation_sha256 is None
                    and _segment_has_content(segments, header_id)
                ):
                    raise SourceChangedError
                headers.append(TabHeader(DocsTabId(tab_id), DocsSegmentId(header_id)))
            elif _segment_has_content(segments, header_id):
                warnings.append(f"header_conflict:{tab_id}")
        return replace(
            draft,
            headers=tuple(headers),
            logo_uri=self._logo_uri,
            revision_id=DocsRevisionId(_required_string(document, "revisionId")),
            brand_warnings=tuple(warnings),
        )

    def _apply_requests_sync(
        self,
        draft: OutputDraft,
        mutation_sha256: str,
        requests: list[dict[str, Any]],
        required_revision_id: DocsRevisionId,
    ) -> OutputDraft:
        if draft.pending_mutation_sha256 not in {None, mutation_sha256}:
            raise IncompatiblePendingOutputError
        marker_name = f"sarj-transform-copy-{mutation_sha256}"
        document = self._get_document_sync(draft.document_id)
        log_bullet_stage(document, "pre_style")
        current_revision = DocsRevisionId(_required_string(document, "revisionId"))
        if draft.pending_mutation_sha256 == mutation_sha256:
            marker_range = _terminal_body_marker_range(document)
            if marker_range is not None and _has_exact_named_range(
                document, marker_name, marker_range
            ):
                self._update_properties_sync(
                    draft.document_id,
                    {_PROP_MUTATION: mutation_sha256},
                )
                return self._draft_with_mutation(
                    draft,
                    mutation_sha256,
                    revision_id=current_revision,
                )
            if (
                current_revision != required_revision_id
                or draft.mutation_base_revision_sha256 != _revision_sha256(current_revision)
            ):
                raise SourceChangedError
        if requests:
            if current_revision != required_revision_id:
                raise SourceChangedError
            marker_range = _terminal_body_marker_range(document)
            if marker_range is None:
                raise DocumentGatewayError(retryable=False)
            expected_marker_range = _post_mutation_marker_range(marker_range, requests)
            self._update_properties_sync(
                draft.document_id,
                {
                    _PROP_PENDING_MUTATION: mutation_sha256,
                    _PROP_MUTATION_BASE_REVISION_HASH: _revision_sha256(required_revision_id),
                },
            )
            try:
                self._execute_mutation_dict(
                    _google_http_request(
                        _member(self._docs().documents(), "batchUpdate"),
                        documentId=draft.document_id,
                        body={
                            "requests": [
                                {"createNamedRange": {"name": marker_name, "range": marker_range}},
                                *requests,
                            ],
                            "writeControl": {"requiredRevisionId": required_revision_id},
                        },
                    )
                )
            except (DocumentGatewayError, ProviderDeadlineError) as exc:
                document = self._get_document_sync(draft.document_id)
                if _has_exact_named_range(document, marker_name, expected_marker_range):
                    pass
                elif document.get("revisionId") == required_revision_id:
                    raise
                else:
                    raise SourceChangedError from exc
        self._update_properties_sync(
            draft.document_id,
            {_PROP_MUTATION: mutation_sha256},
        )
        document = self._get_document_sync(draft.document_id)
        if requests and not _has_exact_named_range(
            document,
            marker_name,
            _terminal_body_marker_range(document) or {},
        ):
            raise DocumentGatewayError
        log_bullet_stage(document, "post_style")
        return OutputDraft(
            output=draft.output,
            document_id=draft.document_id,
            tab_id=draft.tab_id,
            revision_id=DocsRevisionId(_required_string(document, "revisionId")),
            source_sha256=draft.source_sha256,
            headers=draft.headers,
            logo_uri=draft.logo_uri,
            applied_mutation_sha256=mutation_sha256,
            pending_mutation_sha256=mutation_sha256 if requests else None,
            mutation_base_revision_sha256=(
                _revision_sha256(required_revision_id) if requests else None
            ),
            brand_warnings=draft.brand_warnings,
        )

    def _verify_output_sync(
        self,
        draft: OutputDraft,
        source: SourceSnapshot,
        expected: DocumentIR,
        *,
        verify_brand: bool,
        header_title: str | None = None,
    ) -> OutputVerification:
        output_document = self._get_document_sync(draft.document_id)
        if draft.native_dates is not None:
            output_document = verified_date_projection(output_document, draft.native_dates)
        if draft.native_footer is not None:
            output_document = verified_footer_coverage_projection(
                output_document, draft.native_footer
            )
        if source.markdown is not None:
            rendered_markdown = rewrite_markdown(
                source.markdown,
                parse_markdown(source.markdown, title=source.title),
                expected,
            )
            manifest = markdown_import_manifest(
                rendered_markdown,
                title=source.title,
                branded=verify_brand,
                header_title=header_title,
            )
            verification = verify_markdown_import(output_document, manifest)
            logger.info(
                "document markdown import verification checks={checks}",
                checks={
                    "import_content_preserved": verification.content_preserved,
                    "import_structure_preserved": verification.structure_preserved,
                    **markdown_import_brand_diagnostics(output_document, manifest),
                },
            )
            return OutputVerification(
                content_preserved=verification.content_preserved,
                structure_preserved=verification.structure_preserved,
                brand_applied=verification.brand_applied,
                revision_id=DocsRevisionId(_required_string(output_document, "revisionId")),
            )
        structure_document = (
            project_branded_bullet_styles(output_document, expected)
            if verify_brand
            else output_document
        )
        parsed = parse_google_document(structure_document, title=expected.title)
        source_document = source.google_document
        structural_preserved = source_document is None or (
            _structural_inventory(source_document) == _structural_inventory(output_document)
        )
        protected_preserved = source_document is None or (
            _protected_structure_inventory(source_document)
            == _protected_structure_inventory(structure_document)
        )
        copy_list_identity_equivalent = False
        if not protected_preserved and source_document is not None:
            normalized_source = normalize_copy_list_identity(source_document)
            normalized_output = normalize_copy_list_identity(structure_document)
            copy_list_identity_equivalent = (
                normalized_source is not None
                and normalized_output is not None
                and _protected_structure_inventory(normalized_source)
                == _protected_structure_inventory(normalized_output)
            )
            protected_preserved = copy_list_identity_equivalent
            if copy_list_identity_equivalent:
                parsed = align_verified_copy_list_metadata(parsed, expected)
        structure_preserved = (
            structural_preserved
            and protected_preserved
            and (
                source_document is None
                or _occupied_headers_preserved(source_document, output_document)
            )
            and (
                not verify_brand
                or verify_copy_visual_authority(source_document, structure_document, expected)
            )
            and (
                not verify_brand
                or (
                    verify_copy_fonts(
                        source_document,
                        structure_document,
                        expected,
                        list_identity_equivalent=copy_list_identity_equivalent,
                    )
                    if expected.copy_font_defended
                    else _copy_font_preserved(
                        source_document,
                        structure_document,
                        list_identity_equivalent=copy_list_identity_equivalent,
                    )
                )
            )
        )
        content_preserved = _typography_content_preserved(
            parsed, expected, verify_brand=verify_brand
        )
        brand_verification = _copy_brand_verification(
            source_document,
            output_document,
            _typography_verification_ir(parsed, expected),
            source_title=source.title,
            fallback_title=expected.title,
            header_title=header_title,
            brand_warnings=draft.brand_warnings,
            verify_brand=verify_brand,
        )
        if not content_preserved or not structure_preserved:
            logger.warning(
                "document copy verification failed checks={checks}",
                checks={
                    "content_preserved": content_preserved,
                    "structural_inventory_preserved": structural_preserved,
                    "protected_inventory_preserved": protected_preserved,
                    "copy_list_identity_equivalent": copy_list_identity_equivalent,
                    "occupied_headers_preserved": source_document is None
                    or _occupied_headers_preserved(source_document, output_document),
                    "visual_authority_preserved": not verify_brand
                    or verify_copy_visual_authority(source_document, structure_document, expected),
                },
                diagnostics={
                    **_copy_inventory_diagnostics(source_document, structure_document),
                    **bullet_style_diagnostics(
                        parse_google_document(output_document, title=expected.title), expected
                    ),
                    "block_count_mismatch": len(parsed.blocks) != len(expected.blocks),
                    "block_text_mismatches": sum(
                        a.text != b.text
                        for a, b in zip(parsed.blocks, expected.blocks, strict=False)
                    ),
                    "block_kind_mismatches": sum(
                        (a.kind, a.level) != (b.kind, b.level)
                        for a, b in zip(parsed.blocks, expected.blocks, strict=False)
                    ),
                    "block_native_content_mismatches": sum(
                        a.native_content != b.native_content
                        for a, b in zip(parsed.blocks, expected.blocks, strict=False)
                    ),
                    "block_native_style_mismatches": sum(
                        a.native_named_style != b.native_named_style
                        for a, b in zip(parsed.blocks, expected.blocks, strict=False)
                    ),
                    "block_editability_mismatches": sum(
                        a.editable != b.editable
                        for a, b in zip(parsed.blocks, expected.blocks, strict=False)
                    ),
                },
            )
        elif copy_list_identity_equivalent:
            logger.info("document copy verified with exact list identity equivalence")
        return OutputVerification(
            content_preserved=content_preserved,
            structure_preserved=structure_preserved,
            brand_applied=brand_verification.brand_applied,
            supported_brand_applied=verify_brand
            and content_preserved
            and structure_preserved
            and brand_verification.supported_brand_applied,
            revision_id=DocsRevisionId(_required_string(output_document, "revisionId")),
        )

    @staticmethod
    def _draft_with_mutation(
        draft: OutputDraft,
        mutation_sha256: str,
        *,
        revision_id: DocsRevisionId | None = None,
    ) -> OutputDraft:
        return OutputDraft(
            output=draft.output,
            document_id=draft.document_id,
            tab_id=draft.tab_id,
            revision_id=revision_id or draft.revision_id,
            source_sha256=draft.source_sha256,
            headers=draft.headers,
            logo_uri=draft.logo_uri,
            applied_mutation_sha256=mutation_sha256,
            pending_mutation_sha256=draft.pending_mutation_sha256,
            mutation_base_revision_sha256=draft.mutation_base_revision_sha256,
            brand_warnings=draft.brand_warnings,
            completed=draft.completed,
            result=draft.result,
            result_sha256=draft.result_sha256,
            verified_revision_sha256=draft.verified_revision_sha256,
        )

    def _download_markdown_sync(self, file_id: FileId) -> str:
        raw = self._execute_bounded_bytes(
            self._drive().files().get_media(fileId=file_id, supportsAllDrives=True),
            max_bytes=MAX_MARKDOWN_BYTES,
        )
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnsupportedSourceError from exc

    def _get_document_sync(self, file_id: FileId) -> dict[str, Any]:
        # google-api-python-client materializes JSON responses before returning
        # them and offers no streaming hook for documents.get. Keep the request
        # field-complete for structural verification, then apply the strict tab,
        # block, and UTF-16 limits in parse_google_document immediately.
        return self._execute_dict(
            _google_http_request(
                _member(self._docs().documents(), "get"),
                documentId=file_id,
                includeTabsContent=True,
                suggestionsViewMode="SUGGESTIONS_INLINE",
            )
        )

    def _files_get_sync(self, file_id: FileId) -> dict[str, Any]:
        return self._execute_dict(
            self._drive()
            .files()
            .get(
                fileId=file_id,
                supportsAllDrives=True,
                fields=(
                    "id,name,parents,mimeType,modifiedTime,appProperties,ownedByMe,"
                    "capabilities(canEdit),driveId,webViewLink,trashed,contentRestrictions(readOnly)"
                ),
            )
        )

    def _unique_name_sync(self, parent_id: FileId, requested: str) -> str:
        escaped = requested.replace("\\", "\\\\").replace("'", "\\'")
        raw = self._execute_dict(
            self._drive()
            .files()
            .list(
                q=f"'{parent_id}' in parents and name='{escaped}' and trashed=false",
                pageSize=1,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                fields="files(id)",
            )
        )
        files = raw.get("files", [])
        return requested if not files else f"{requested} ({uuid.uuid7().hex[:8]})"

    def _update_properties_sync(self, file_id: FileId, properties: dict[str, str]) -> None:
        self._execute_mutation_dict(
            self._drive()
            .files()
            .update(
                fileId=file_id,
                supportsAllDrives=True,
                body={"appProperties": properties},
                fields="id",
            )
        )

    def _drive(self) -> DriveResource:
        return reuse_drive_client(
            self._user,
            self._drive_clients,
            refresh_handler=self._refresh_handler,
        )

    def _docs(self) -> _DocumentsProvider:
        thread_id = threading.get_ident()
        client = self._docs_clients.get(thread_id)
        if client is None:
            credentials = build_google_credentials(self._user, self._refresh_handler)
            built = _call_boundary(
                _member(discovery, "build_from_document"),
                docs_discovery_document(),
                credentials=credentials,
            )
            if not isinstance(built, _DocumentsProvider):
                msg = "Google Docs discovery builder returned an invalid client"
                raise TypeError(msg)
            client = built
            self._docs_clients[thread_id] = client
        return client

    @staticmethod
    def _execute_dict(request: HttpRequest) -> dict[str, Any]:
        try:
            raw: object = execute_with_retry(request)
        except ProviderDeadlineError:
            raise
        except HttpError as exc:
            raise _gateway_error(exc) from exc
        except (TimeoutError, ConnectionError, httplib2.ServerNotFoundError) as exc:
            raise DocumentGatewayError(retryable=True) from exc
        if isinstance(raw, dict):
            return {str(key): value for key, value in raw.items()}
        raise DocumentGatewayError

    @staticmethod
    def _execute_mutation_dict(request: HttpRequest) -> dict[str, Any]:
        try:
            raw: object = execute_mutation(request)
        except ProviderDeadlineError:
            raise
        except HttpError as exc:
            raise _gateway_error(exc) from exc
        except (TimeoutError, ConnectionError, httplib2.ServerNotFoundError) as exc:
            raise DocumentGatewayError(retryable=True) from exc
        if isinstance(raw, dict):
            return {str(key): value for key, value in raw.items()}
        raise DocumentGatewayError

    @staticmethod
    def _execute_bytes(request: HttpRequest) -> bytes:
        try:
            raw: object = execute_with_retry(request)
        except ProviderDeadlineError:
            raise
        except HttpError as exc:
            raise _gateway_error(exc) from exc
        except (TimeoutError, ConnectionError, httplib2.ServerNotFoundError) as exc:
            raise DocumentGatewayError(retryable=True) from exc
        if isinstance(raw, bytes):
            return raw
        raise DocumentGatewayError

    @staticmethod
    def _execute_bounded_bytes(request: HttpRequest, *, max_bytes: int) -> bytes:
        destination = BytesIO()
        download = MediaIoBaseDownload(
            destination,
            request,
            chunksize=_MARKDOWN_DOWNLOAD_CHUNK_BYTES,
        )
        done = False
        while not done:
            try:
                status, done = execute_download_chunk_with_retry(download)
            except ProviderDeadlineError:
                raise
            except HttpError as exc:
                raise _gateway_error(exc) from exc
            except (TimeoutError, ConnectionError, httplib2.ServerNotFoundError) as exc:
                raise DocumentGatewayError(retryable=True) from exc
            except ValueError as exc:
                # Invalid Content-Length or Content-Range from the provider.
                raise DocumentGatewayError(retryable=False) from exc
            total_size = status.total_size if status is not None else None
            if isinstance(total_size, int) and total_size > max_bytes:
                raise SourceTooLargeError
            if destination.tell() > max_bytes:
                raise SourceTooLargeError
        return destination.getvalue()


class GoogleDocumentRevisionReader(GoogleDocumentGateway):
    async def current_revision(self, file_id: FileId) -> DocsRevisionId:
        document = await asyncio.to_thread(self._get_revision_sync, file_id)
        return DocsRevisionId(_required_string(document, "revisionId"))

    def _get_revision_sync(self, file_id: FileId) -> dict[str, Any]:
        return self._execute_dict(
            _google_http_request(
                _member(self._docs().documents(), "get"),
                documentId=file_id,
                fields="revisionId",
            )
        )


def _gateway_error(exc: HttpError) -> DocumentGatewayError:
    status = provider_http_status(exc)
    return DocumentGatewayError(
        retryable=status is None or is_retryable_provider_status(status),
        http_status=status,
    )


@cache
def docs_discovery_document() -> str:
    provider: object = get_static_doc
    content = _call_boundary(provider, "docs", "v1")
    if not isinstance(content, str):
        msg = "Bundled Google Docs v1 discovery document not found"
        raise TypeError(msg)
    json.loads(content)
    return content


def _initial_properties(operation_id: uuid.UUID, source_sha256: str) -> dict[str, str]:
    return {
        _PROP_OPERATION: str(operation_id),
        _PROP_SOURCE_HASH: source_sha256,
        _PROP_STATE: _STATE_INCOMPLETE,
    }


def _required_parent(file: dict[str, Any]) -> FileId:
    parents = file.get("parents")
    if not isinstance(parents, list) or not parents or not isinstance(parents[0], str):
        raise UnsupportedSourceError
    return FileId(parents[0])


def _required_string(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise DocumentGatewayError
    return value


def _optional_property(properties: dict[str, Any], key: str) -> str | None:
    value = properties.get(key)
    return value if isinstance(value, str) else None


def _revision_sha256(revision_id: DocsRevisionId) -> str:
    return hashlib.sha256(revision_id.encode()).hexdigest()


def _json_sha256(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _first_tab_id(document: dict[str, Any]) -> DocsTabId:
    tabs = document.get("tabs")
    if not isinstance(tabs, list) or not tabs or not isinstance(tabs[0], dict):
        raise DocumentGatewayError
    properties = tabs[0].get("tabProperties")
    if not isinstance(properties, dict):
        raise DocumentGatewayError
    return DocsTabId(_required_string(properties, "tabId"))


def _segment_has_content(segments: object, segment_id: str) -> bool:
    if not isinstance(segments, dict):
        return False
    segment = segments.get(segment_id, {})
    if not isinstance(segment, dict):
        return False
    content = segment.get("content", [])
    if not isinstance(content, list):
        return False
    for item in content:
        if not isinstance(item, dict):
            continue
        typed_item = {str(key): value for key, value in item.items()}
        if _structural_text(typed_item).strip():
            return True
        paragraph = typed_item.get("paragraph")
        if isinstance(paragraph, dict):
            elements = paragraph.get("elements", [])
            if isinstance(elements, list) and any(
                isinstance(element, dict)
                and any(key in element for key in ("inlineObjectElement", "person", "richLink"))
                for element in elements
            ):
                return True
        if any(key in typed_item for key in ("table", "sectionBreak", "tableOfContents")):
            return True
    return False


def _has_active_suggestions(value: object) -> bool:
    match value:
        case dict() as mapping:
            return any(
                (key.startswith("suggested") and child not in (None, False, "", [], {}))
                or _has_active_suggestions(child)
                for key, child in mapping.items()
                if isinstance(key, str)
            )
        case list() as items:
            return any(_has_active_suggestions(child) for child in items)
        case _:
            return False


def _inventory_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _header_can_accept_custom_title(tab: dict[str, Any], header_id: str) -> bool:
    headers = tab.get("headers")
    header = headers.get(header_id) if isinstance(headers, dict) else None
    content = header.get("content") if isinstance(header, dict) else None
    if not isinstance(content, list) or not content or not isinstance(content[0], dict):
        return False
    paragraph = content[0].get("paragraph")
    return isinstance(paragraph, dict) and isinstance(paragraph.get("elements"), list)


def _classify_in_place_headers(
    document: dict[str, Any],
    header_title: str,
    header_identity: HeaderIdentityEvidence | None,
) -> _InPlaceHeaderState:
    state = _InPlaceHeaderStateBuilder()
    document_tabs = tuple(_document_tabs(document))
    present_title_targets = tuple(
        CustomHeaderTitleTarget(tab_id=DocsTabId(raw_tab_id), header_id=DocsSegmentId(header_id))
        for raw_tab_id, document_tab in document_tabs
        for header_id in (
            tuple(document_tab.get("headers", {}))
            if isinstance(document_tab.get("headers"), dict)
            else ()
        )
        if _header_has_custom_title_prefix(document_tab, header_id, header_title)
    )
    present_title_pairs = frozenset(
        (target.tab_id, target.header_id) for target in present_title_targets
    )
    projected_document = _project_custom_header_titles(
        document, header_title, present_title_targets
    )
    projected_tabs = dict(_document_tabs(projected_document))
    for raw_tab_id, document_tab in document_tabs:
        _classify_in_place_tab(
            state,
            document=document,
            document_tabs=document_tabs,
            projected_document=projected_document,
            projected_tabs=projected_tabs,
            present_title_pairs=present_title_pairs,
            raw_tab_id=raw_tab_id,
            document_tab=document_tab,
            header_title=header_title,
            header_identity=header_identity,
        )
    for raw_tab_id, document_tab in document_tabs:
        _classify_in_place_footer(
            state,
            projected_document=projected_document,
            projected_tabs=projected_tabs,
            present_title_pairs=present_title_pairs,
            raw_tab_id=raw_tab_id,
            document_tab=document_tab,
        )
    return state.freeze()


def _classify_in_place_tab(
    state: _InPlaceHeaderStateBuilder,
    *,
    document: dict[str, Any],
    document_tabs: tuple[tuple[str, dict[str, Any]], ...],
    projected_document: dict[str, Any],
    projected_tabs: dict[str, dict[str, Any]],
    present_title_pairs: frozenset[tuple[DocsTabId, DocsSegmentId]],
    raw_tab_id: str,
    document_tab: dict[str, Any],
    header_title: str,
    header_identity: HeaderIdentityEvidence | None,
) -> None:
    tab_id = DocsTabId(raw_tab_id)
    style = document_tab.get("documentStyle", {})
    header_id = style.get("defaultHeaderId") if isinstance(style, dict) else None
    segments = document_tab.get("headers", {})
    if (
        not _furniture_references_resolve(document_tab)
        or not isinstance(segments, dict)
        or any(not isinstance(key, str) or not key for key in segments)
    ):
        state.unknown.append(tab_id)
        return
    if isinstance(header_id, str) and header_id not in segments:
        state.unknown.append(tab_id)
        return
    if not isinstance(header_id, str):
        if not segments:
            state.missing.append(tab_id)
            return
        _record_projected_custom_furniture(
            state,
            projected_document=projected_document,
            projected_tabs=projected_tabs,
            present_title_pairs=present_title_pairs,
            raw_tab_id=raw_tab_id,
            header_ids=tuple(sorted(segments)),
        )
        return
    if any(segment_id != header_id for segment_id in segments):
        _record_projected_custom_furniture(
            state,
            projected_document=projected_document,
            projected_tabs=projected_tabs,
            present_title_pairs=present_title_pairs,
            raw_tab_id=raw_tab_id,
            header_ids=tuple(sorted(segments)),
        )
        return
    if not _segment_has_content(segments, header_id):
        state.headers.append(TabHeader(tab_id=tab_id, header_id=DocsSegmentId(header_id)))
        return
    if _tab_header_is_branded(
        document,
        document_tab,
        expected_title=header_title,
        allow_legacy_inline_objects=len(document_tabs) == 1,
        header_separator=_owned_header_separator(header_identity),
    ):
        state.preserved_headers.append(TabHeader(tab_id=tab_id, header_id=DocsSegmentId(header_id)))
        return
    _record_projected_custom_furniture(
        state,
        projected_document=projected_document,
        projected_tabs=projected_tabs,
        present_title_pairs=present_title_pairs,
        raw_tab_id=raw_tab_id,
        header_ids=(header_id,),
    )


def _record_projected_custom_furniture(
    state: _InPlaceHeaderStateBuilder,
    *,
    projected_document: dict[str, Any],
    projected_tabs: dict[str, dict[str, Any]],
    present_title_pairs: frozenset[tuple[DocsTabId, DocsSegmentId]],
    raw_tab_id: str,
    header_ids: tuple[str, ...],
) -> None:
    tab_id = DocsTabId(raw_tab_id)
    binding = _custom_furniture_binding(
        projected_document,
        tab_id,
        projected_tabs.get(raw_tab_id, {}),
        header_ids,
    )
    if binding is None:
        state.unknown.append(tab_id)
        return
    projected_tab = projected_tabs.get(raw_tab_id)
    if not isinstance(projected_tab, dict):
        state.unknown.append(tab_id)
        return
    state.record_custom(binding, projected_tab, present_title_pairs)


def _classify_in_place_footer(
    state: _InPlaceHeaderStateBuilder,
    *,
    projected_document: dict[str, Any],
    projected_tabs: dict[str, dict[str, Any]],
    present_title_pairs: frozenset[tuple[DocsTabId, DocsSegmentId]],
    raw_tab_id: str,
    document_tab: dict[str, Any],
) -> None:
    tab_id = DocsTabId(raw_tab_id)
    if (
        tab_id in {binding.tab_id for binding in state.custom_furniture}
        or tab_id in state.unknown
        or not document_tab.get("footers")
    ):
        return
    _record_projected_custom_furniture(
        state,
        projected_document=projected_document,
        projected_tabs=projected_tabs,
        present_title_pairs=present_title_pairs,
        raw_tab_id=raw_tab_id,
        header_ids=(),
    )


def _header_has_custom_title_prefix(tab: dict[str, Any], header_id: str, title: str) -> bool:
    projected = _strip_custom_header_title(tab, header_id, title)
    return projected is not None


def _custom_header_title_target(
    tab: dict[str, Any], tab_id: DocsTabId, header_id: DocsSegmentId
) -> CustomHeaderTitleTarget | None:
    headers = tab.get("headers")
    header = headers.get(str(header_id)) if isinstance(headers, dict) else None
    content = header.get("content") if isinstance(header, dict) else None
    if not isinstance(content, list) or not content or not isinstance(content[0], dict):
        return None
    paragraph = content[0].get("paragraph")
    elements = paragraph.get("elements") if isinstance(paragraph, dict) else None
    if not isinstance(elements, list) or not elements:
        return None
    offset = 0
    for element in elements:
        if not isinstance(element, dict):
            return None
        text_run = element.get("textRun")
        if isinstance(text_run, dict):
            content_text = text_run.get("content")
            if not isinstance(content_text, str):
                return None
            end_index = offset + utf16_length(content_text)
            if content_text.endswith("\n"):
                text_style = text_run.get("textStyle", {})
                if not isinstance(text_style, dict):
                    return None
                return CustomHeaderTitleTarget(
                    tab_id=tab_id,
                    header_id=header_id,
                    terminal_newline_index=end_index - 1,
                    terminal_newline_text_style_json=json.dumps(
                        text_style,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
        else:
            start_index = element.get("startIndex")
            raw_end_index = element.get("endIndex")
            if (
                not isinstance(start_index, int) or not isinstance(raw_end_index, int)
            ) and "inlineObjectElement" in element:
                return CustomHeaderTitleTarget(tab_id=tab_id, header_id=header_id)
            if (
                not isinstance(start_index, int)
                or not isinstance(raw_end_index, int)
                or raw_end_index <= start_index
            ):
                return None
            end_index = offset + raw_end_index - start_index
        offset = end_index
    return CustomHeaderTitleTarget(tab_id=tab_id, header_id=header_id)


def _strip_custom_header_title(
    tab: dict[str, Any], header_id: str, title: str
) -> dict[str, Any] | None:
    if not _header_can_accept_custom_title(tab, header_id):
        return None
    projected = deepcopy(tab)
    headers = projected["headers"]
    elements = headers[header_id]["content"][0]["paragraph"]["elements"]
    header_tokens = CURRENT_BRAND_MANIFEST.document.header
    if header_tokens is None:  # pragma: no cover - pinned manifest invariant
        return None
    prefix = normalize_header_title(title, max_chars=header_tokens.max_title_chars) + (
        HEADER_TITLE_SEPARATOR
    )
    remaining = prefix
    stripped: list[Any] = []
    for element in elements:
        if not remaining:
            stripped.append(element)
            continue
        if not isinstance(element, dict) or not isinstance(element.get("textRun"), dict):
            return None
        run = element["textRun"]
        content = run.get("content")
        if not isinstance(content, str) or not remaining.startswith(content[: len(remaining)]):
            return None
        consumed = content[: len(remaining)]
        if not remaining.startswith(consumed) or not _header_text_run_has_brand(run):
            return None
        remaining = remaining[len(consumed) :]
        leftover = content[len(consumed) :]
        if leftover:
            stripped.append({**element, "textRun": {**run, "content": leftover}})
    if remaining:
        return None
    headers[header_id]["content"][0]["paragraph"]["elements"] = stripped
    return projected


def _project_custom_header_titles(
    document: dict[str, Any],
    title: str,
    targets: tuple[CustomHeaderTitleTarget, ...],
) -> dict[str, Any]:
    projected = deepcopy(document)
    tabs = dict(_document_tabs(projected))
    for target in targets:
        tab = tabs.get(str(target.tab_id))
        if not isinstance(tab, dict):
            return document
        stripped = _strip_custom_header_title(tab, str(target.header_id), title)
        if stripped is None:
            return document
        tab.clear()
        tab.update(stripped)
    return projected


def _custom_header_titles_match(
    document: dict[str, Any],
    title: str,
    targets: tuple[CustomHeaderTitleTarget, ...],
    bindings: tuple[CustomFurnitureBinding, ...],
) -> bool:
    expected = {
        (binding.tab_id, header_id) for binding in bindings for header_id in binding.header_ids
    }
    observed = {(target.tab_id, target.header_id) for target in targets}
    if observed != expected or len(observed) != len(targets):
        return False
    tabs = dict(_document_tabs(document))
    return all(
        isinstance((tab := tabs.get(str(target.tab_id))), dict)
        and _header_has_custom_title_prefix(tab, str(target.header_id), title)
        for target in targets
    )


def _custom_furniture_binding(
    document: dict[str, Any], tab_id: DocsTabId, tab: dict[str, Any], header_ids: tuple[str, ...]
) -> CustomFurnitureBinding | None:
    style = tab.get("documentStyle")
    headers, footers = tab.get("headers", {}), tab.get("footers", {})
    if (
        not isinstance(style, dict)
        or not isinstance(headers, dict)
        or not isinstance(footers, dict)
    ):
        return None
    if not furniture_bindings_known(tab):
        return None
    if header_ids:
        header_refs = set().union(
            *(
                _referenced_ids(tab, key)
                for key in ("defaultHeaderId", "firstPageHeaderId", "evenPageHeaderId")
            )
        )
        if (
            len(header_ids) > MAX_DOC_TABS
            or set(headers) != set(header_ids)
            or header_refs != set(header_ids)
        ):
            return None
    if len(footers) > MAX_DOC_TABS or any(not isinstance(key, str) for key in footers):
        return None
    footer_refs = set().union(
        *(
            _referenced_ids(tab, key)
            for key in ("defaultFooterId", "firstPageFooterId", "evenPageFooterId")
        )
    )
    if footer_refs != set(footers):
        return None
    selected_headers = {key: headers.get(key) for key in header_ids}
    segments = {"headers": selected_headers, "footers": footers}
    if any(
        not isinstance(segment, dict) or not isinstance(segment.get("content"), list)
        for group in segments.values()
        for segment in group.values()
    ):
        return None
    canonical_headers = _canonical_custom_header_suffixes(document, tab, selected_headers)
    if canonical_headers is None:
        return None
    segments = {"headers": canonical_headers, "footers": footers}
    payload = {
        **segments,
        "documentStyle": {
            key: value for key, value in style.items() if header_ids or key != "defaultHeaderId"
        },
        "rootDocumentStyle": document.get("documentStyle"),
        "namedStyles": [document.get("namedStyles"), tab.get("namedStyles")],
        "sectionStyles": furniture_context_values(tab.get("body", {}), frozenset({"sectionStyle"})),
    }
    for reference, field in (
        ("inlineObjectId", "inlineObjects"),
        ("positionedObjectIds", "positionedObjects"),
        ("listId", "lists"),
    ):
        identifiers = _referenced_ids(segments, reference)
        definitions = tab.get(field, document.get(field, {}))
        if not isinstance(definitions, dict) or any(
            not isinstance(definitions.get(key), dict) for key in identifiers
        ):
            return None
        payload[field] = {key: definitions[key] for key in sorted(identifiers)}
    try:
        normalized = _stable_object_payload(payload)
        size = len(json.dumps(normalized, ensure_ascii=False, allow_nan=False).encode())
    except TypeError, ValueError, RecursionError:
        return None
    if size > MAX_FURNITURE_BYTES:
        return None
    return CustomFurnitureBinding(
        tab_id=tab_id,
        header_ids=tuple(DocsSegmentId(identifier) for identifier in header_ids),
        footer_ids=tuple(DocsSegmentId(identifier) for identifier in sorted(footers)),
        sha256=_inventory_sha256(normalized),
    )


def _canonical_custom_header_suffixes(
    document: dict[str, Any],
    tab: dict[str, Any],
    headers: dict[str, Any],
) -> dict[str, Any] | None:
    canonical = _strip_location_fields(headers)
    if not _is_string_dict(canonical):
        return None
    named_styles = tab.get("namedStyles", document.get("namedStyles"))
    style_context = {**tab, "namedStyles": named_styles}
    normalized_headers: dict[str, Any] = {}
    for header_id, header in canonical.items():
        normalized = _canonicalize_custom_header(header, style_context)
        if normalized is None:
            return None
        normalized_headers[header_id] = normalized
    return normalized_headers


def _canonicalize_custom_header(
    header: object, style_context: dict[str, Any]
) -> dict[str, Any] | None:
    if not _is_string_dict(header):
        return None
    content = header.get("content")
    if not isinstance(content, list) or not content or not _is_string_dict(content[0]):
        return None
    paragraph = content[0].get("paragraph")
    if not _is_string_dict(paragraph):
        return None
    elements = paragraph.get("elements")
    effective_elements = _effective_paragraph(paragraph, style_context).get("elements")
    if (
        not isinstance(elements, list)
        or not isinstance(effective_elements, list)
        or len(elements) != len(effective_elements)
        or not elements
    ):
        return None
    normalized: list[Any] = []
    effective_styles: list[dict[str, Any] | None] = []
    for index, (element, effective_element) in enumerate(
        zip(elements, effective_elements, strict=True)
    ):
        result = _canonical_header_run(element, effective_element, index, len(elements))
        if result is None:
            return None
        merged = (
            _merged_custom_header_text_run(normalized[-1], result.element)
            if normalized and effective_styles[-1] == result.effective_style
            else None
        )
        if merged is not None:
            normalized[-1] = merged
            continue
        normalized.append(result.element)
        effective_styles.append(result.effective_style)
    normalized_content = list(content)
    normalized_content[0] = {
        **content[0],
        "paragraph": {**paragraph, "elements": normalized},
    }
    return {**header, "content": normalized_content}


@dataclass(frozen=True)
class _CanonicalHeaderRun:
    effective_style: dict[str, Any] | None
    element: Any


def _canonical_header_run(
    element: object, effective_element: object, index: int, count: int
) -> _CanonicalHeaderRun | None:
    if not _is_string_dict(element) or not _is_string_dict(effective_element):
        return _CanonicalHeaderRun(effective_style=None, element=element)
    run = element.get("textRun")
    effective_run = effective_element.get("textRun")
    if not _is_string_dict(run) or not _is_string_dict(effective_run):
        return _CanonicalHeaderRun(effective_style=None, element=element)
    content = run.get("content")
    effective_style = effective_run.get("textStyle")
    if not isinstance(content, str) or not _is_string_dict(effective_style):
        return None
    canonical_element = element
    if index == count - 1 and content == "\n":
        canonical_element = {**element, "textRun": {**run, "textStyle": dict(effective_style)}}
    return _CanonicalHeaderRun(
        effective_style=dict(effective_style),
        element=canonical_element,
    )


def _merged_custom_header_text_run(previous: object, current: object) -> dict[str, Any] | None:
    if not _is_string_dict(previous) or not _is_string_dict(current):
        return None
    if set(previous) != {"textRun"} or set(current) != {"textRun"}:
        return None
    previous_run = previous["textRun"]
    current_run = current["textRun"]
    if (
        not _is_string_dict(previous_run)
        or not _is_string_dict(current_run)
        or set(previous_run) - {"content", "textStyle"}
        or set(current_run) - {"content", "textStyle"}
    ):
        return None
    previous_content = previous_run.get("content")
    current_content = current_run.get("content")
    if not isinstance(previous_content, str) or not isinstance(current_content, str):
        return None
    return {
        "textRun": {
            **previous_run,
            "content": previous_content + current_content,
        }
    }


def _is_string_dict(value: object) -> TypeIs[dict[str, Any]]:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def _furniture_references_resolve(tab: dict[str, Any]) -> bool:
    if not furniture_bindings_known(tab):
        return False
    for segment_type, reference_fields in (
        ("headers", ("defaultHeaderId", "firstPageHeaderId", "evenPageHeaderId")),
        ("footers", ("defaultFooterId", "firstPageFooterId", "evenPageFooterId")),
    ):
        segments = tab.get(segment_type, {})
        if not isinstance(segments, dict) or any(
            not isinstance(key, str) or not key for key in segments
        ):
            return False
        references = set().union(*(_referenced_ids(tab, key) for key in reference_fields))
        if references != set(segments):
            return False
    return True


def _custom_furniture_matches(
    document: dict[str, Any],
    bindings: tuple[CustomFurnitureBinding, ...],
    *,
    title: str | None = None,
    custom_header_titles: tuple[CustomHeaderTitleTarget, ...] = (),
) -> bool:
    projected = (
        _project_custom_header_titles(document, title, custom_header_titles)
        if title is not None and custom_header_titles
        else document
    )
    tabs = dict(_document_tabs(projected))
    return len({binding.tab_id for binding in bindings}) == len(bindings) and all(
        binding.tab_id in tabs
        and _custom_furniture_binding(
            projected, binding.tab_id, tabs[binding.tab_id], binding.header_ids
        )
        == binding
        for binding in bindings
    )


def _body_sha256(document: dict[str, Any]) -> str:
    bodies = [
        {"tab_id": tab_id, "body": document_tab.get("body")}
        for tab_id, document_tab in _document_tabs(document)
    ]
    return _inventory_sha256(bodies)


def _header_safe_sha256(document: dict[str, Any]) -> str:
    def normalize(value: object) -> object:
        if isinstance(value, list):
            return [normalize(child) for child in value]
        if not isinstance(value, dict):
            return value
        normalized: dict[str, object] = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            if key in {"revisionId", "namedRanges", "headers"}:
                continue
            if key == "documentStyle" and isinstance(child, dict):
                normalized[key] = {
                    str(nested_key): normalize(nested_value)
                    for nested_key, nested_value in child.items()
                    if nested_key != "defaultHeaderId"
                }
            else:
                normalized[key] = normalize(child)
        return normalized

    return _inventory_sha256(normalize(document))


def _terminal_body_marker_range(document: dict[str, Any]) -> dict[str, Any] | None:
    candidate: dict[str, Any] | None = None
    for tab_id, document_tab in _document_tabs(document):
        body = document_tab.get("body", {})
        content = body.get("content", []) if isinstance(body, dict) else []
        if not isinstance(content, list):
            raise DocumentGatewayError
        end_indexes: list[int] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            end_index = item.get("endIndex")
            if isinstance(end_index, int):
                end_indexes.append(end_index)
        if not end_indexes:
            continue
        end_index = max(end_indexes)
        if end_index > 0:
            candidate = {
                "startIndex": end_index - 1,
                "endIndex": end_index,
                "tabId": tab_id,
            }
    return candidate


def _post_mutation_marker_range(
    marker_range: dict[str, Any],
    requests: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = dict(marker_range)
    start = expected.get("startIndex")
    end = expected.get("endIndex")
    tab_id = expected.get("tabId")
    if not isinstance(start, int) or not isinstance(end, int) or not isinstance(tab_id, str):
        raise DocumentGatewayError
    for request in requests:
        deletion = request.get("deleteContentRange")
        if isinstance(deletion, dict):
            range_value = deletion.get("range")
            if not isinstance(range_value, dict):
                continue
            deletion_start = range_value.get("startIndex")
            deletion_end = range_value.get("endIndex")
            if (
                range_value.get("tabId") == tab_id
                and range_value.get("segmentId") is None
                and isinstance(deletion_start, int)
                and isinstance(deletion_end, int)
                and deletion_end <= start
            ):
                delta = deletion_end - deletion_start
                start -= delta
                end -= delta
        insertion = request.get("insertText")
        if isinstance(insertion, dict):
            location = insertion.get("location")
            text = insertion.get("text")
            if not isinstance(location, dict) or not isinstance(text, str):
                continue
            insertion_index = location.get("index")
            if (
                location.get("tabId") == tab_id
                and location.get("segmentId") is None
                and isinstance(insertion_index, int)
                and insertion_index <= start
            ):
                delta = len(text.encode("utf-16-le")) // 2
                start += delta
                end += delta
    expected["startIndex"] = start
    expected["endIndex"] = end
    return expected


_STAGE_REQUEST_KINDS = frozenset(
    {
        "createFooter",
        "createHeader",
        "insertText",
        "deleteContentRange",
        "insertDate",
        "updateTextStyle",
        "updateParagraphStyle",
        "updateDocumentStyle",
        "createNamedRange",
        "deleteNamedRange",
        "updateDocumentTabProperties",
    }
)


def stage_request_kinds(requests: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {key if key in _STAGE_REQUEST_KINDS else "other" for request in requests for key in request}
    )


def _named_range_diagnostics(
    document: dict[str, Any],
    marker_name: str,
    expected_range: dict[str, Any],
) -> dict[str, int | bool]:
    # Report marker shape without provider identifiers or index values.
    checks: dict[str, int | bool] = {
        "entry_count": 0,
        "definition_count": 0,
        "range_count": 0,
        "malformed_definitions": 0,
        "malformed_ranges": 0,
        "matching_name_count": 0,
        "matching_tab_count": 0,
        "start_present_count": 0,
        "end_present_count": 0,
        "matching_start_count": 0,
        "matching_end_count": 0,
        "exact_range_count": 0,
    }
    for tab_id, document_tab in _document_tabs(document):
        named_ranges = document_tab.get("namedRanges")
        entry = named_ranges.get(marker_name) if isinstance(named_ranges, dict) else None
        if not isinstance(entry, dict):
            continue
        checks["entry_count"] += 1
        definitions = entry.get("namedRanges")
        if not isinstance(definitions, list):
            checks["malformed_definitions"] += 1
            continue
        checks["definition_count"] += len(definitions)
        for definition in definitions:
            if not isinstance(definition, dict):
                checks["malformed_definitions"] += 1
                continue
            checks["matching_name_count"] += definition.get("name") == marker_name
            ranges = definition.get("ranges")
            if not isinstance(ranges, list):
                checks["malformed_ranges"] += 1
                continue
            checks["range_count"] += len(ranges)
            for observed in ranges:
                if not isinstance(observed, dict):
                    checks["malformed_ranges"] += 1
                    continue
                checks["matching_tab_count"] += tab_id == expected_range.get("tabId")
                checks["start_present_count"] += type(observed.get("startIndex")) is int
                checks["end_present_count"] += type(observed.get("endIndex")) is int
                checks["matching_start_count"] += observed.get("startIndex") == expected_range.get(
                    "startIndex"
                )
                checks["matching_end_count"] += observed.get("endIndex") == expected_range.get(
                    "endIndex"
                )
                checks["exact_range_count"] += _same_named_range(
                    observed, expected_range, tab_id=tab_id
                )
    return checks


def _has_exact_named_range(
    document: dict[str, Any],
    marker_name: str,
    expected_range: dict[str, Any],
) -> bool:
    matches: list[dict[str, Any]] = []
    expected_tab_id = expected_range.get("tabId")
    for tab_id, document_tab in _document_tabs(document):
        named_ranges = document_tab.get("namedRanges")
        if not isinstance(named_ranges, dict):
            continue
        entry = named_ranges.get(marker_name)
        if not isinstance(entry, dict):
            continue
        definitions = entry.get("namedRanges")
        if not isinstance(definitions, list):
            return False
        for definition in definitions:
            if isinstance(definition, dict) and definition.get("name") == marker_name:
                ranges = definition.get("ranges")
                if (
                    tab_id != expected_tab_id
                    or not isinstance(ranges, list)
                    or len(ranges) != 1
                    or not _same_named_range(ranges[0], expected_range, tab_id=tab_id)
                ):
                    return False
                matches.append(definition)
    return len(matches) == 1


def _same_named_range(
    observed: object,
    expected: dict[str, Any],
    *,
    tab_id: str,
) -> bool:
    if not isinstance(observed, dict):
        return False
    observed_start = observed.get("startIndex")
    observed_end = observed.get("endIndex")
    expected_start = expected.get("startIndex")
    expected_end = expected.get("endIndex")
    if not all(
        type(value) is int for value in (observed_start, observed_end, expected_start, expected_end)
    ):
        return False
    observed_tab_id = observed.get("tabId", tab_id)
    return (
        observed_start == expected_start
        and observed_end == expected_end
        and observed_tab_id == expected.get("tabId")
        and observed.get("segmentId", "") == expected.get("segmentId", "")
    )


def _document_tabs(document: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    tabs = document.get("tabs")
    if not isinstance(tabs, list) or not tabs:
        raise DocumentGatewayError
    yield from _walk_document_tabs(tabs)


def _walk_document_tabs(tabs: list[Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for tab in tabs:
        if not isinstance(tab, dict):
            raise DocumentGatewayError
        properties = tab.get("tabProperties")
        if not isinstance(properties, dict):
            raise DocumentGatewayError
        tab_id = _required_string(properties, "tabId")
        document_tab = tab.get("documentTab")
        if not isinstance(document_tab, dict):
            raise DocumentGatewayError
        yield tab_id, document_tab
        children = tab.get("childTabs", [])
        if not isinstance(children, list):
            raise DocumentGatewayError
        yield from _walk_document_tabs(children)


def _structural_text(element: dict[str, Any]) -> str:
    paragraph = element.get("paragraph", {})
    elements = paragraph.get("elements", []) if isinstance(paragraph, dict) else []
    if not isinstance(elements, list):
        return ""
    return "".join(
        content
        for item in elements
        if isinstance(item, dict)
        and isinstance(item.get("textRun"), dict)
        and isinstance((content := item["textRun"].get("content")), str)
    )


def _structural_inventory(document: dict[str, Any]) -> tuple[tuple[int, ...], ...]:
    inventory: list[tuple[int, ...]] = []
    for _, document_tab in _document_tabs(document):
        body = document_tab.get("body", {})
        content = body.get("content", []) if isinstance(body, dict) else []
        if not isinstance(content, list):
            raise DocumentGatewayError
        counts = {
            "paragraph": 0,
            "sectionBreak": 0,
            "table": 0,
            "tableOfContents": 0,
            "inlineObjectElement": 0,
            "footnoteReference": 0,
            "horizontalRule": 0,
        }
        for element in content:
            if not isinstance(element, dict):
                raise DocumentGatewayError
            _count_structural_keys(element, counts)
        inventory.append(tuple(counts[key] for key in sorted(counts)))
    return tuple(inventory)


def _count_structural_keys(value: object, counts: dict[str, int]) -> None:
    match value:
        case dict() as mapping:
            for key, child in mapping.items():
                if isinstance(key, str) and key in counts:
                    counts[key] += 1
                _count_structural_keys(child, counts)
        case list() as items:
            for child in items:
                _count_structural_keys(child, counts)
        case _:
            pass


def _copy_font_preserved(
    source: dict[str, Any] | None,
    actual: dict[str, Any],
    *,
    list_identity_equivalent: bool,
) -> bool:
    if source is None:
        return True
    # List alpha-renaming is allowed only after the complete existing protected
    # inventory proves the bijection. Never infer it from text or run styles.
    if list_identity_equivalent:
        source = normalize_copy_list_identity(source)
        normalized_actual = normalize_copy_list_identity(actual)
        if source is None or normalized_actual is None:
            return False
        actual = normalized_actual
    try:
        original = parse_google_document(source, title="")
        if any(
            not _font_ranges_cover_paragraph(block)
            for block in original.blocks
            if not block.editable and block.font_style_ranges
        ):
            return False
        proofs = capture_native_font_proof(source, original)
        return verify_native_font_proof(actual, proofs) and verify_native_unstyled_text(
            source, actual, original
        )
    except KeyError, IndexError, TypeError, ValueError, AttributeError, UnsupportedSourceError:
        return False


def _font_ranges_cover_paragraph(block: TextBlock) -> bool:
    source = block.source_range
    if source is None or source.segment_id is not None:
        return False
    cursor = source.start_index
    for span in block.font_style_ranges:
        if type(span.start_index) is not int or type(span.end_index) is not int:
            return False
        if (
            span.tab_id != source.tab_id
            or span.segment_id is not None
            or span.start_index != cursor
            or span.end_index <= cursor
            or span.end_index > source.end_index
        ):
            return False
        cursor = span.end_index
    return cursor == source.end_index


def _protected_structure_inventory(
    document: dict[str, Any], *, include_native_list_text_styles: bool = False
) -> tuple[tuple[str, str], ...]:
    inventory: list[tuple[str, str]] = []
    for tab_id, document_tab in _document_tabs(document):
        body = document_tab.get("body", {})
        content = body.get("content", []) if isinstance(body, dict) else []
        if not isinstance(content, list):
            raise DocumentGatewayError
        protected = [
            _protected_element_payload(element)
            for element in content
            if isinstance(element, dict) and not _is_plain_text_paragraph(element)
        ]
        immutable = _immutable_tab_payload(document_tab, content)
        if include_native_list_text_styles:
            native_list_text_styles = _native_list_text_style_inventory(content)
            if native_list_text_styles:
                immutable["nativeListTextStyles"] = native_list_text_styles
        if immutable:
            protected.append(immutable)
        inventory.append(
            (
                tab_id,
                json.dumps(protected, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            )
        )
    return tuple(inventory)


def _copy_inventory_diagnostics(
    original: dict[str, Any] | None, actual: dict[str, Any]
) -> dict[str, bool]:
    if original is None:
        return {"source_document_present": False}
    source_tabs = list(_document_tabs(original))
    actual_tabs = list(_document_tabs(actual))
    checks = {
        "tab_identities_equal": [tab[0] for tab in source_tabs] == [tab[0] for tab in actual_tabs]
    }
    fields = (
        "links",
        "bullets",
        "lists",
        "inlineObjects",
        "positionedObjects",
        "footnotes",
        "footers",
    )
    checks.update(
        {f"protected_{field}_equal": len(source_tabs) == len(actual_tabs) for field in fields}
    )
    for (_, source_tab), (_, actual_tab) in zip(source_tabs, actual_tabs, strict=False):
        source_payload = _immutable_tab_payload(
            source_tab, source_tab.get("body", {}).get("content", [])
        )
        actual_payload = _immutable_tab_payload(
            actual_tab, actual_tab.get("body", {}).get("content", [])
        )
        for field in fields:
            checks[f"protected_{field}_equal"] &= source_payload.get(field) == actual_payload.get(
                field
            )
    normalized_source = normalize_copy_list_identity(original)
    normalized_actual = normalize_copy_list_identity(actual)
    checks["source_list_projection_valid"] = normalized_source is not None
    checks["output_list_projection_valid"] = normalized_actual is not None
    return checks


def _occupied_headers_preserved(original: dict[str, Any], actual: dict[str, Any]) -> bool:
    actual_tabs = dict(_document_tabs(actual))
    for tab_id, source_tab in _document_tabs(original):
        source_headers = source_tab.get("headers", {})
        if not isinstance(source_headers, dict):
            return False
        target_tab = actual_tabs.get(tab_id)
        if target_tab is None:
            return False
        target_headers = target_tab.get("headers", {})
        if not isinstance(target_headers, dict):
            return False
        for header_id, header in source_headers.items():
            # New branding may fill an empty default segment, but it must never
            # replace authored header text, tables, images, or their definitions.
            if not _segment_has_content(source_headers, header_id):
                continue
            if _stable_object_payload(header) != _stable_object_payload(
                target_headers.get(header_id)
            ):
                return False
            for reference, definitions in (
                ("inlineObjectId", "inlineObjects"),
                ("positionedObjectIds", "positionedObjects"),
                ("listId", "lists"),
            ):
                for identifier in _referenced_ids(header, reference):
                    if _stable_object_payload(source_tab.get(definitions, {}).get(identifier)) != (
                        _stable_object_payload(target_tab.get(definitions, {}).get(identifier))
                    ):
                        return False
            for binding in ("defaultHeaderId", "firstPageHeaderId", "evenPageHeaderId"):
                if source_tab.get("documentStyle", {}).get(binding) == header_id and (
                    target_tab.get("documentStyle", {}).get(binding) != header_id
                ):
                    return False
    return True


def _copy_brand_verification(
    original: dict[str, Any] | None,
    actual: dict[str, Any],
    parsed: DocumentIR,
    *,
    source_title: str,
    fallback_title: str,
    header_title: str | None,
    brand_warnings: tuple[str, ...],
    verify_brand: bool,
) -> _CopyBrandVerification:
    if not verify_brand:
        return _CopyBrandVerification(brand_applied=True, supported_brand_applied=False)
    expected_header_title = _expected_copy_header_title(
        original,
        source_title=source_title,
        fallback_title=fallback_title,
        header_title=header_title,
    )
    if brand_warnings and all(warning.startswith("header_conflict:") for warning in brand_warnings):
        return _CopyBrandVerification(
            brand_applied=False,
            supported_brand_applied=original is not None
            and _copy_supported_brand_applied(
                original,
                actual,
                parsed,
                expected_header_title=expected_header_title,
                brand_warnings=brand_warnings,
            ),
        )
    brand_applied = verify_brand_application(
        actual,
        parsed,
        expected_header_title=expected_header_title,
    )
    return _CopyBrandVerification(
        brand_applied=brand_applied,
        supported_brand_applied=False,
    )


def _copy_supported_brand_applied(
    original: dict[str, Any],
    actual: dict[str, Any],
    parsed: DocumentIR,
    *,
    expected_header_title: str,
    brand_warnings: tuple[str, ...],
) -> bool:
    prefix = "header_conflict:"
    if not brand_warnings or any(not warning.startswith(prefix) for warning in brand_warnings):
        return False
    tab_ids = tuple(warning.removeprefix(prefix) for warning in brand_warnings)
    if any(not tab_id for tab_id in tab_ids) or len(set(tab_ids)) != len(tab_ids):
        return False
    original_tabs = dict(_document_tabs(original))
    bindings: list[CustomFurnitureBinding] = []
    for raw_tab_id in tab_ids:
        source_tab = original_tabs.get(raw_tab_id)
        if source_tab is None:
            return False
        headers = source_tab.get("headers")
        style = source_tab.get("documentStyle")
        header_id = style.get("defaultHeaderId") if isinstance(style, dict) else None
        if (
            not isinstance(headers, dict)
            or any(not isinstance(key, str) or not key for key in headers)
            or not isinstance(header_id, str)
            or not _segment_has_content(headers, header_id)
        ):
            return False
        binding = _custom_furniture_binding(
            original,
            DocsTabId(raw_tab_id),
            source_tab,
            tuple(sorted(headers)),
        )
        if binding is None or DocsSegmentId(header_id) not in binding.header_ids:
            return False
        bindings.append(binding)
    evidence = tuple(bindings)
    return _custom_furniture_matches(actual, evidence) and verify_brand_application(
        actual,
        parsed,
        expected_header_title=expected_header_title,
        preserved_furniture_tabs=frozenset(binding.tab_id for binding in evidence),
        preserved_header_tabs=frozenset(binding.tab_id for binding in evidence),
    )


def _expected_copy_header_title(
    original: dict[str, Any] | None,
    *,
    source_title: str,
    fallback_title: str,
    header_title: str | None,
) -> str:
    if header_title is not None:
        return header_title
    if original is not None:
        return suggested_document_title(parse_google_document(original, title=source_title))
    return fallback_title


def _immutable_tab_payload(document_tab: dict[str, Any], content: list[Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in ("footnotes", "footers"):
        if document_tab.get(key):
            payload[key] = _strip_location_fields(document_tab[key])
    links = _paragraph_link_inventory(content)
    if links:
        payload["links"] = links
    # Only referenced body/footnote/footer objects are protected here: branding
    # intentionally adds a header logo. Signed contentUri values expire and are
    # not image identity; retain the stable source URI and all other properties.
    reference_scope = {"body": content, **payload}
    for reference_key, definitions_key in (
        ("inlineObjectId", "inlineObjects"),
        ("positionedObjectIds", "positionedObjects"),
        ("listId", "lists"),
    ):
        identifiers = _referenced_ids(reference_scope, reference_key)
        definitions = document_tab.get(definitions_key, {})
        if identifiers:
            if not isinstance(definitions, dict):
                raise DocumentGatewayError
            payload[definitions_key] = {
                identifier: _stable_object_payload(definitions.get(identifier))
                for identifier in sorted(identifiers)
            }
    bullets = _bullet_inventory(content)
    if bullets:
        payload["bullets"] = bullets
    return payload


def _native_list_text_style_inventory(value: object) -> list[object]:
    inventory: list[object] = []
    match value:
        case list() as items:
            for child in items:
                inventory.extend(_native_list_text_style_inventory(child))
            return inventory
        case dict() as mapping:
            pass
        case _:
            return inventory
    paragraph = mapping.get("paragraph")
    if isinstance(paragraph, dict) and isinstance(paragraph.get("bullet"), dict):
        elements = paragraph.get("elements")
        if not isinstance(elements, list):
            return [{"malformed": True}]
        visible: list[dict[str, Any]] = []
        terminal_count = 0
        for element in elements:
            run = element.get("textRun") if isinstance(element, dict) else None
            if not isinstance(run, dict):
                return [{"malformed": True}]
            content_value: object = run.get("content")
            if not isinstance(content_value, str):
                return [{"malformed": True}]
            content = content_value
            style_value: object = run.get("textStyle", {})
            if not isinstance(style_value, dict) or any(
                not isinstance(key, str) for key in style_value
            ):
                return [{"malformed": True}]
            style: dict[str, Any] = {str(key): child for key, child in style_value.items()}
            visible_text = content.removesuffix("\n")
            if visible_text:
                if visible and visible[-1]["style"] == style:
                    visible[-1]["text"] += visible_text
                else:
                    visible.append({"style": style, "text": visible_text})
            if content.endswith("\n"):
                terminal_count += 1
        styles = [item["style"] for item in visible]
        inventory.append(
            {
                "visible": (
                    styles
                    if len(styles) <= 1
                    else [
                        {
                            "style": item["style"],
                            "textSha256": hashlib.sha256(item["text"].encode()).hexdigest(),
                        }
                        for item in visible
                    ]
                ),
                # Docs may canonicalize this invisible provider-owned newline
                # when the following paragraph is styled. Preserve its exact
                # topology, while keeping every visible list style strict.
                "terminalCount": terminal_count,
            }
        )
        return inventory
    for child in mapping.values():
        inventory.extend(_native_list_text_style_inventory(child))
    return inventory


def _protected_element_payload(element: dict[str, Any]) -> object:
    payload = _strip_location_fields(element)
    if not isinstance(payload, dict):  # pragma: no cover - recursive shape invariant
        return payload
    paragraph = payload.get("paragraph")
    source_paragraph = element.get("paragraph")
    if not isinstance(paragraph, dict) or not isinstance(source_paragraph, dict):
        return payload
    parts = paragraph.get("elements")
    source_parts = source_paragraph.get("elements")
    if (
        not isinstance(parts, list)
        or len(parts) != _PAGE_BREAK_PARAGRAPH_PART_COUNT
        or not isinstance(source_parts, list)
        or len(source_parts) != _PAGE_BREAK_PARAGRAPH_PART_COUNT
    ):
        return payload
    page_break, source_terminal = source_parts
    if (
        not isinstance(page_break, dict)
        or set(page_break) - {"startIndex", "endIndex", "pageBreak"}
        or not isinstance(page_break.get("pageBreak"), dict)
        or not isinstance(source_terminal, dict)
        or set(source_terminal) - {"startIndex", "endIndex", "textRun"}
    ):
        return payload
    terminal = parts[-1]
    run = terminal.get("textRun") if isinstance(terminal, dict) else None
    if isinstance(run, dict) and run.get("content") == "\n":
        # Style values on a page-break paragraph's terminal newline are not
        # visible and cannot be restored exactly: Docs drops explicit default
        # fields. The newline and native page-break payload remain fully hashed.
        run.pop("textStyle", None)
    return payload


def _paragraph_link_inventory(value: object) -> list[object]:
    links: list[object] = []
    match value:
        case list():
            for child in value:
                links.extend(_paragraph_link_inventory(child))
            return links
        case dict():
            pass
        case _:
            return links
    paragraph = value.get("paragraph")
    if isinstance(paragraph, dict):
        runs: list[dict[str, Any]] = []
        elements = paragraph.get("elements", [])
        if not isinstance(elements, list):
            return links
        for part in elements:
            run = part.get("textRun", {}) if isinstance(part, dict) else {}
            if not isinstance(run, dict):
                continue
            style = run.get("textStyle", {})
            link = style.get("link") if isinstance(style, dict) else None
            text = run.get("content", "")
            if runs and runs[-1]["link"] == link:
                runs[-1]["text"] += text
            else:
                runs.append({"text": text, "link": link})
        if any(run["link"] is not None for run in runs):
            links.append(runs)
    else:
        for child in value.values():
            links.extend(_paragraph_link_inventory(child))
    return links


def _bullet_inventory(value: object) -> list[object]:
    match value:
        case dict():
            bullet = value.get("bullet")
            return (
                [bullet]
                if "bullet" in value
                else [item for child in value.values() for item in _bullet_inventory(child)]
            )
        case list():
            return [item for child in value for item in _bullet_inventory(child)]
        case _:
            return []


def _referenced_ids(value: object, reference_key: str) -> set[str]:
    identifiers: set[str] = set()
    match value:
        case dict():
            reference = value.get(reference_key)
            match reference:
                case str():
                    identifiers.add(reference)
                case list():
                    identifiers.update(item for item in reference if isinstance(item, str))
                case _:
                    pass
            for child in value.values():
                identifiers.update(_referenced_ids(child, reference_key))
        case list():
            for child in value:
                identifiers.update(_referenced_ids(child, reference_key))
        case _:
            pass
    return identifiers


def _stable_object_payload(value: object) -> object:
    match value:
        case dict():
            return {
                key: (
                    {field: item for field, item in child.items() if field != "contentUri"}
                    if key == "imageProperties" and isinstance(child, dict)
                    else _stable_object_payload(child)
                )
                for key, child in value.items()
            }
        case list():
            return [_stable_object_payload(child) for child in value]
        case _:
            return value


def _is_plain_text_paragraph(element: dict[str, Any]) -> bool:
    paragraph = element.get("paragraph")
    if not isinstance(paragraph, dict):
        return False
    parts = paragraph.get("elements", [])
    return isinstance(parts, list) and all(
        isinstance(part, dict) and isinstance(part.get("textRun"), dict) for part in parts
    )


def _strip_location_fields(value: object) -> object:
    match value:
        case dict() as mapping:
            return {
                key: _strip_location_fields(child)
                for key, child in mapping.items()
                if key not in {"startIndex", "endIndex"}
            }
        case list() as items:
            return [_strip_location_fields(child) for child in items]
        case _:
            return value


def _typography_content_preserved(
    actual: DocumentIR, expected: DocumentIR, *, verify_brand: bool
) -> bool:
    if verify_brand:
        actual = align_branded_bullet_metadata(actual, expected)
    if actual.semantic_sha256 == expected.semantic_sha256:
        return True
    if not verify_brand or len(actual.blocks) != len(expected.blocks):
        return False
    # Font-only changes can make the provider coalesce adjacent text runs.
    # Allow only that capability transition; persisted checkpoint hashes and
    # independent protected-content/structure verification remain unchanged.
    return all(
        (observed.kind, observed.level, observed.text)
        == (planned.kind, planned.level, planned.text)
        and observed.native_content == planned.native_content
        and observed.native_named_style == planned.native_named_style
        and observed.native_bullet_json == planned.native_bullet_json
        and (
            observed.editable == planned.editable
            or (not planned.editable and observed.editable and bool(planned.font_style_ranges))
        )
        for observed, planned in zip(actual.blocks, expected.blocks, strict=True)
    )


def _native_list_block_ids(document: DocumentIR) -> frozenset[str]:
    return frozenset(block.id for block in document.blocks if block.native_bullet_json is not None)


def _typography_verification_ir(actual: DocumentIR, expected: DocumentIR) -> DocumentIR:
    if len(actual.blocks) != len(expected.blocks):
        return actual
    blocks = []
    for observed, planned in zip(actual.blocks, expected.blocks, strict=True):
        if (
            planned.editable
            or not planned.font_style_ranges
            or planned.source_range is None
            or observed.source_range is None
        ):
            blocks.append(
                replace(
                    observed,
                    pagination_keep_with_next=planned.pagination_keep_with_next,
                    suppress_space_above=planned.suppress_space_above,
                    suppress_space_below=planned.suppress_space_below,
                    preserved_paragraph_style_sha256=planned.preserved_paragraph_style_sha256,
                    preserved_visual_authority=planned.preserved_visual_authority,
                    preserved_font_family=planned.preserved_font_family,
                )
                if observed.sha256 == planned.sha256
                else observed
            )
            continue
        delta = observed.source_range.start_index - planned.source_range.start_index
        blocks.append(
            replace(
                observed,
                editable=False,
                preserved_font_family=planned.preserved_font_family,
                font_style_ranges=tuple(
                    replace(
                        item, start_index=item.start_index + delta, end_index=item.end_index + delta
                    )
                    for item in planned.font_style_ranges
                ),
            )
        )
    return replace(actual, blocks=tuple(blocks))


def _font_only_has_brand(paragraph: dict[str, Any], block: TextBlock) -> bool:
    if block.preserved_font_family:
        # This is excluded from full-brand claims. The executor's persisted
        # exact-family proof must independently pass before scoped completion.
        return True
    elements = paragraph.get("elements", [])
    if not isinstance(elements, list):
        return False
    for source in block.font_style_ranges:
        cursor = source.start_index
        for part in elements:
            if not isinstance(part, dict):
                continue
            start, end = part.get("startIndex"), part.get("endIndex")
            if not isinstance(start, int) or not isinstance(end, int) or end <= cursor:
                continue
            if start > cursor:
                return False
            run = part.get("textRun", {})
            style = run.get("textStyle", {}) if isinstance(run, dict) else {}
            family = style.get("weightedFontFamily", {}) if isinstance(style, dict) else {}
            if (
                not isinstance(family, dict)
                or family.get("fontFamily") != CURRENT_BRAND_MANIFEST.document.font_family
            ):
                return False
            cursor = min(end, source.end_index)
            if cursor == source.end_index:
                break
        if cursor != source.end_index:
            return False
    return True


def verify_brand_application(
    document: dict[str, Any],
    parsed: DocumentIR,
    *,
    expected_header_title: str | None = None,
    header_separator: str = HEADER_TITLE_SEPARATOR,
    preserved_furniture_tabs: frozenset[DocsTabId] = frozenset(),
    preserved_header_tabs: frozenset[DocsTabId] = frozenset(),
    preserved_native_list_text_style_ids: frozenset[str] = frozenset(),
    allow_preserved_visual_authority: bool = False,
) -> bool:
    tabs = tuple(_document_tabs(document))
    applied = (
        bool(tabs)
        and preserved_header_tabs <= preserved_furniture_tabs
        and all(
            tab_id in preserved_header_tabs
            or (
                tab_id in preserved_furniture_tabs
                and _tab_header_is_branded(
                    document,
                    document_tab,
                    expected_title=parsed.title
                    if expected_header_title is None
                    else expected_header_title,
                    allow_legacy_inline_objects=len(tabs) == 1,
                    header_separator=header_separator,
                )
            )
            or _tab_shell_is_branded(
                document,
                document_tab,
                expected_title=parsed.title
                if expected_header_title is None
                else expected_header_title,
                allow_legacy_inline_objects=len(tabs) == 1,
                header_separator=header_separator,
            )
            for tab_id, document_tab in tabs
        )
    )
    paragraphs = _paragraphs_by_range(tabs)
    if applied:
        for block in parsed.blocks:
            source_range = block.source_range
            if source_range is None or block.kind is BlockKind.BLANK:
                continue
            if not block.editable and not block.font_style_ranges:
                continue
            paragraph = paragraphs.get(
                ParagraphRangeKey(
                    tab_id=source_range.tab_id,
                    start_index=source_range.start_index,
                    end_index=source_range.end_index,
                )
            )
            if paragraph is None or not (
                _paragraph_has_brand(
                    paragraph,
                    block,
                    preserve_text_style=block.id in preserved_native_list_text_style_ids,
                    allow_preserved_visual_authority=allow_preserved_visual_authority,
                )
                if block.editable
                else _font_only_has_brand(paragraph, block)
            ):
                applied = False
                break
    if not applied:
        checks = _brand_application_diagnostics(
            document,
            parsed,
            expected_header_title=expected_header_title,
            header_separator=header_separator,
            preserved_native_list_text_style_ids=preserved_native_list_text_style_ids,
            allow_preserved_visual_authority=allow_preserved_visual_authority,
        )
        logger.warning("document brand verification failed checks={checks}", checks=checks)
    return applied


def _brand_application_diagnostics(
    document: dict[str, Any],
    parsed: DocumentIR,
    *,
    expected_header_title: str | None = None,
    header_separator: str = HEADER_TITLE_SEPARATOR,
    preserved_native_list_text_style_ids: frozenset[str] = frozenset(),
    allow_preserved_visual_authority: bool = False,
) -> dict[str, bool | int]:
    tabs = tuple(_document_tabs(document))
    tab_checks: list[dict[str, bool]] = []
    for _, document_tab in tabs:
        tab_checks.append(
            _tab_brand_diagnostics(
                document,
                document_tab,
                expected_title=parsed.title
                if expected_header_title is None
                else expected_header_title,
                allow_legacy_inline_objects=len(tabs) == 1,
                header_separator=header_separator,
            )
        )
    paragraphs = _paragraphs_by_range(tabs)
    paragraph_range_checks: list[bool] = []
    paragraph_checks: list[dict[str, bool]] = []
    font_only_checks: list[bool] = []
    for block in parsed.blocks:
        source_range = block.source_range
        if source_range is None or block.kind is BlockKind.BLANK:
            continue
        if not block.editable and not block.font_style_ranges:
            continue
        paragraph = paragraphs.get(
            ParagraphRangeKey(
                tab_id=source_range.tab_id,
                start_index=source_range.start_index,
                end_index=source_range.end_index,
            )
        )
        if not block.editable:
            font_only_checks.append(
                paragraph is not None and _font_only_has_brand(paragraph, block)
            )
            continue
        paragraph_range_checks.append(paragraph is not None)
        paragraph_checks.append(
            _paragraph_brand_diagnostics(
                paragraph,
                block,
                preserve_text_style=block.id in preserved_native_list_text_style_ids,
                allow_preserved_visual_authority=allow_preserved_visual_authority,
            )
            if paragraph is not None
            else {"named_style": False, "layout": False, "text_style": False}
        )
    checks: dict[str, bool | int] = {
        "tabs_present": bool(tabs),
        "tab_count": len(tabs),
        "margins": bool(tabs) and all(item["margins"] for item in tab_checks),
        "header_shape": bool(tabs) and all(item["header_shape"] for item in tab_checks),
        "header_paragraph_style": bool(tabs)
        and all(item["header_paragraph_style"] for item in tab_checks),
        "header_title": bool(tabs) and all(item["header_title"] for item in tab_checks),
        "header_text_style": bool(tabs) and all(item["header_text_style"] for item in tab_checks),
        "logo_count": bool(tabs) and all(item["logo_count"] for item in tab_checks),
        "logo_asset": bool(tabs) and all(item["logo_asset"] for item in tab_checks),
        "paragraph_ranges": all(paragraph_range_checks),
        "paragraph_named_styles": all(item["named_style"] for item in paragraph_checks),
        "paragraph_layout": all(item["layout"] for item in paragraph_checks),
        "paragraph_text_styles": all(item["text_style"] for item in paragraph_checks),
        "paragraph_count": len(paragraph_checks),
    }
    if font_only_checks:
        checks["paragraph_font_only_styles"] = all(font_only_checks)
        checks["paragraph_font_only_count"] = len(font_only_checks)
    return checks


def _tab_brand_diagnostics(
    document: dict[str, Any],
    document_tab: dict[str, Any],
    *,
    expected_title: str,
    allow_legacy_inline_objects: bool,
    header_separator: str = HEADER_TITLE_SEPARATOR,
) -> dict[str, bool]:
    style = document_tab.get("documentStyle")
    expected_margin = CURRENT_BRAND_MANIFEST.document.horizontal_margin_pt
    expected_vertical = CURRENT_BRAND_MANIFEST.document.vertical_margin_pt
    margin_ok = (
        isinstance(style, dict)
        and _dimension_matches(style.get("marginLeft"), expected_margin)
        and _dimension_matches(style.get("marginRight"), expected_margin)
        and _dimension_matches(style.get("marginTop"), expected_vertical)
        and _dimension_matches(style.get("marginBottom"), expected_vertical)
    )
    header_id = style.get("defaultHeaderId") if isinstance(style, dict) else None
    headers = document_tab.get("headers")
    header = (
        headers.get(header_id) if isinstance(headers, dict) and isinstance(header_id, str) else None
    )
    header_checks = _header_brand_diagnostics(
        header if isinstance(header, dict) else None,
        expected_title=expected_title,
        header_separator=header_separator,
    )
    object_ids = _values_for_key(header, "inlineObjectId") if isinstance(header, dict) else ()
    logo_count_ok = len(object_ids) == 1
    inline_objects = document_tab.get("inlineObjects")
    if not isinstance(inline_objects, dict) and allow_legacy_inline_objects:
        inline_objects = document.get("inlineObjects")
    logo_asset_ok = (
        isinstance(inline_objects, dict)
        and logo_count_ok
        and _inline_object_uses_canonical_logo(inline_objects.get(object_ids[0]))
    )
    return {
        "margins": margin_ok,
        **header_checks,
        "logo_count": logo_count_ok,
        "logo_asset": logo_asset_ok,
    }


def _header_brand_diagnostics(
    header: dict[str, Any] | None,
    *,
    expected_title: str,
    header_separator: str = HEADER_TITLE_SEPARATOR,
) -> dict[str, bool]:
    header_tokens = CURRENT_BRAND_MANIFEST.document.header
    content = header.get("content") if isinstance(header, dict) else None
    element = content[0] if isinstance(content, list) and len(content) == 1 else None
    paragraph = element.get("paragraph") if isinstance(element, dict) else None
    paragraph_style = paragraph.get("paragraphStyle") if isinstance(paragraph, dict) else None
    elements = paragraph.get("elements") if isinstance(paragraph, dict) else None
    shape_ok = (
        header_tokens is not None
        and isinstance(element, dict)
        and not set(element) - {"startIndex", "endIndex", "paragraph"}
        and isinstance(paragraph_style, dict)
        and isinstance(elements, list)
    )
    typed_elements = elements if isinstance(elements, list) else []
    logo_positions = [
        index
        for index, item in enumerate(typed_elements)
        if isinstance(item, dict) and isinstance(item.get("inlineObjectElement"), dict)
    ]
    title_elements = typed_elements[: logo_positions[0]] if len(logo_positions) == 1 else []
    text_runs = [
        item["textRun"]
        for item in title_elements
        if isinstance(item, dict) and isinstance(item.get("textRun"), dict)
    ]
    normalized_title = (
        normalize_header_title(expected_title, max_chars=header_tokens.max_title_chars)
        if header_tokens is not None
        else ""
    )
    return {
        "header_shape": shape_ok and len(logo_positions) == 1 and bool(title_elements),
        "header_paragraph_style": bool(
            header_tokens is not None
            and isinstance(paragraph_style, dict)
            and paragraph_style.get("namedStyleType") == "NORMAL_TEXT"
            and paragraph_style.get("alignment") == header_tokens.alignment
            and paragraph_style.get("lineSpacing") == header_tokens.line_spacing_percent
            and _dimension_matches(paragraph_style.get("spaceAbove"), header_tokens.space_above_pt)
            and _dimension_matches(paragraph_style.get("spaceBelow"), header_tokens.space_below_pt)
            and paragraph_style.get("keepWithNext") is False
        ),
        "header_title": bool(text_runs)
        and len(text_runs) == len(title_elements)
        and all(isinstance(run.get("content"), str) for run in text_runs)
        and "".join(run["content"] for run in text_runs) == normalized_title + header_separator,
        "header_text_style": bool(text_runs)
        and all(_header_text_run_has_brand(run) for run in text_runs),
    }


def _paragraph_brand_diagnostics(
    paragraph: dict[str, Any],
    block: TextBlock,
    *,
    preserve_text_style: bool = False,
    allow_preserved_visual_authority: bool = False,
) -> dict[str, bool]:
    elements = paragraph.get("elements")
    paragraph_style = paragraph.get("paragraphStyle")
    if not isinstance(elements, list) or not isinstance(paragraph_style, dict):
        return {"named_style": False, "layout": False, "text_style": False}
    expected_named_style = paragraph_named_style(block)
    spacing = paragraph_spacing(block, SarjBrandProfile())
    text_runs = [
        item["textRun"]
        for item in elements
        if isinstance(item, dict)
        and isinstance(item.get("textRun"), dict)
        and isinstance(item["textRun"].get("content"), str)
        and item["textRun"]["content"].strip()
    ]
    expected_color = paragraph_color(block, SarjBrandProfile())
    return {
        "named_style": (allow_preserved_visual_authority and block.preserved_visual_authority)
        or _preserved_layout_matches(paragraph, block)
        or paragraph_style.get("namedStyleType") == expected_named_style,
        "layout": _preserved_layout_matches(paragraph, block)
        or (allow_preserved_visual_authority and block.preserved_visual_authority)
        if block.preserved_paragraph_style_sha256 is not None or block.preserved_visual_authority
        else bool(
            paragraph_style.get("alignment") == "START"
            and paragraph_style.get("lineSpacing")
            == CURRENT_BRAND_MANIFEST.document.line_spacing_percent
            and _dimension_matches(
                paragraph_style.get("spaceAbove"),
                spacing.above,
            )
            and _dimension_matches(
                paragraph_style.get("spaceBelow"),
                spacing.below,
            )
            and paragraph_style.get("keepWithNext") is paragraph_keep_with_next(block)
            and (
                block.pagination_keep_with_next is None
                or paragraph_style.get("keepLinesTogether") is True
            )
        ),
        "text_style": preserve_text_style
        or (allow_preserved_visual_authority and block.preserved_visual_authority)
        or (
            bool(text_runs)
            and all(
                _text_run_has_brand(run, _brand_font_size(block), expected_color, block)
                for run in text_runs
            )
        ),
    }


def _tab_shell_is_branded(
    document: dict[str, Any],
    document_tab: dict[str, Any],
    *,
    expected_title: str,
    allow_legacy_inline_objects: bool,
    header_separator: str = HEADER_TITLE_SEPARATOR,
) -> bool:
    style = document_tab.get("documentStyle")
    if not isinstance(style, dict):
        return False
    expected_margin = CURRENT_BRAND_MANIFEST.document.horizontal_margin_pt
    expected_vertical = CURRENT_BRAND_MANIFEST.document.vertical_margin_pt
    if not (
        _dimension_matches(style.get("marginLeft"), expected_margin)
        and _dimension_matches(style.get("marginRight"), expected_margin)
        and _dimension_matches(style.get("marginTop"), expected_vertical)
        and _dimension_matches(style.get("marginBottom"), expected_vertical)
    ):
        return False
    return _tab_header_is_branded(
        document,
        document_tab,
        expected_title=expected_title,
        allow_legacy_inline_objects=allow_legacy_inline_objects,
        header_separator=header_separator,
    )


def _header_identity_title_digest(title: str) -> str:
    tokens = CURRENT_BRAND_MANIFEST.document.header
    if tokens is None:
        raise InPlaceNeedsReviewError
    return hashlib.sha256(
        normalize_header_title(title, max_chars=tokens.max_title_chars).encode()
    ).hexdigest()


def _managed_header_fingerprint(
    document: dict[str, Any],
    tab: dict[str, Any],
    *,
    expected_title: str,
    header_separator: str = HEADER_TITLE_SEPARATOR,
) -> str | None:
    if not _tab_header_is_branded(
        document,
        tab,
        expected_title=expected_title,
        allow_legacy_inline_objects=False,
        header_separator=header_separator,
    ):
        return None
    header_id = tab["documentStyle"]["defaultHeaderId"]
    header = deepcopy(tab["headers"][header_id])
    for item in header["content"]:
        if isinstance(item.get("paragraph"), dict):
            item["paragraph"] = _effective_paragraph(item["paragraph"], tab)
    object_id = _values_for_key(header, "inlineObjectId")[0]
    logo = deepcopy(tab["inlineObjects"][object_id])
    # Only the documented temporary download URI is excluded. Canonical
    # sourceUri/size were checked above; every other object field is bound.
    logo["inlineObjectProperties"]["embeddedObject"]["imageProperties"].pop("contentUri", None)
    return _inventory_sha256({"header": header, "logo": logo})


def _owned_header_separator(evidence: HeaderIdentityEvidence | None) -> str:
    return "\t" if evidence is not None and evidence.version == 1 else HEADER_TITLE_SEPARATOR


def _trusted_header_identity_title(
    document: dict[str, Any], evidence: HeaderIdentityEvidence | None
) -> str | None:
    if evidence is None:
        return None
    # The first version supports only wholly producer-managed header topology.
    # Never lend a trusted title to an unowned canonical-looking custom header.
    if {header.tab_id for header in evidence.headers} != set(evidence.tab_ids):
        raise InPlaceNeedsReviewError
    if tuple(tab_id for tab_id, _ in _document_tabs(document)) != evidence.tab_ids:
        raise InPlaceNeedsReviewError
    if (
        document.get("documentId") != evidence.file_id
        or evidence.profile_sha256 != CURRENT_BRAND_MANIFEST.sha256
    ):
        raise InPlaceNeedsReviewError
    title: str | None = None
    for binding in evidence.headers:
        tab = footer_document_tab(document, binding.tab_id)
        if tab.get("documentStyle", {}).get("defaultHeaderId") != binding.header_id:
            raise InPlaceNeedsReviewError
        header = tab.get("headers", {}).get(binding.header_id, {})
        content = header.get("content", [])
        if not isinstance(content, list) or len(content) != 1:
            raise InPlaceNeedsReviewError
        parts: list[str] = []
        for element in content[0].get("paragraph", {}).get("elements", []):
            if "inlineObjectElement" in element:
                break
            value = element.get("textRun", {}).get("content")
            if not isinstance(value, str):
                raise InPlaceNeedsReviewError
            parts.append(value)
        separator = _owned_header_separator(evidence)
        candidate = "".join(parts).removesuffix(separator)
        if (
            _header_identity_title_digest(candidate) != evidence.title_sha256
            or _managed_header_fingerprint(
                document, tab, expected_title=candidate, header_separator=separator
            )
            != binding.fingerprint_sha256
            or (title is not None and title != candidate)
        ):
            raise InPlaceNeedsReviewError
        title = candidate
    return title


def _tab_header_is_branded(
    document: dict[str, Any],
    document_tab: dict[str, Any],
    *,
    expected_title: str,
    allow_legacy_inline_objects: bool,
    header_separator: str = HEADER_TITLE_SEPARATOR,
) -> bool:
    style = document_tab.get("documentStyle", {})
    header_id = style.get("defaultHeaderId") if isinstance(style, dict) else None
    headers = document_tab.get("headers")
    if not isinstance(header_id, str) or not isinstance(headers, dict):
        return False
    if set(headers) != {header_id}:
        return False
    header = headers.get(header_id)
    if not isinstance(header, dict) or not _segment_has_text(headers, header_id):
        return False
    if not isinstance(header.get("content"), list):
        return False
    effective_header = {
        **header,
        "content": [
            {**item, "paragraph": _effective_paragraph(item["paragraph"], document_tab)}
            if isinstance(item, dict) and isinstance(item.get("paragraph"), dict)
            else item
            for item in header.get("content", [])
        ],
    }
    if not _header_has_brand(
        effective_header, expected_title=expected_title, header_separator=header_separator
    ):
        return False
    object_ids = _values_for_key(header, "inlineObjectId")
    if len(object_ids) != 1:
        return False
    inline_objects = document_tab.get("inlineObjects")
    if not isinstance(inline_objects, dict) and allow_legacy_inline_objects:
        inline_objects = document.get("inlineObjects")
    return isinstance(inline_objects, dict) and _inline_object_uses_canonical_logo(
        inline_objects.get(object_ids[0])
    )


def _segment_has_text(segments: object, segment_id: str) -> bool:
    if not isinstance(segments, dict):
        return False
    segment = segments.get(segment_id, {})
    if not isinstance(segment, dict):
        return False
    content = segment.get("content", [])
    if not isinstance(content, list):
        return False
    return any(
        _structural_text({str(key): value for key, value in item.items()}).strip()
        for item in content
        if isinstance(item, dict)
    )


def _header_has_brand(
    header: dict[str, Any],
    *,
    expected_title: str,
    header_separator: str = HEADER_TITLE_SEPARATOR,
) -> bool:
    header_tokens = CURRENT_BRAND_MANIFEST.document.header
    if header_tokens is None:
        return False
    content = header.get("content")
    if not isinstance(content, list):
        return False
    if len(content) != 1 or not isinstance(content[0], dict):
        return False
    structural_element = content[0]
    if set(structural_element) - {"startIndex", "endIndex", "paragraph"}:
        return False
    paragraph = structural_element.get("paragraph")
    if not isinstance(paragraph, dict):
        return False
    paragraph_style = paragraph.get("paragraphStyle")
    elements = paragraph.get("elements")
    if not isinstance(paragraph_style, dict) or not isinstance(elements, list):
        return False
    logo_positions = [
        index
        for index, element in enumerate(elements)
        if isinstance(element, dict) and isinstance(element.get("inlineObjectElement"), dict)
    ]
    if len(logo_positions) != 1:
        return False
    logo_position = logo_positions[0]
    title_elements = elements[:logo_position]
    trailing_elements = elements[logo_position + 1 :]
    if not title_elements or not all(
        isinstance(element, dict) and isinstance(element.get("textRun"), dict)
        for element in title_elements
    ):
        return False
    if not all(
        isinstance(element, dict)
        and isinstance(element.get("textRun"), dict)
        and isinstance(element["textRun"].get("content"), str)
        and not element["textRun"]["content"].strip()
        for element in trailing_elements
    ):
        return False
    if not all(
        (
            paragraph_style.get("namedStyleType") == "NORMAL_TEXT",
            paragraph_style.get("alignment") == header_tokens.alignment,
            paragraph_style.get("lineSpacing") == header_tokens.line_spacing_percent,
            _dimension_matches(paragraph_style.get("spaceAbove"), header_tokens.space_above_pt),
            _dimension_matches(paragraph_style.get("spaceBelow"), header_tokens.space_below_pt),
            paragraph_style.get("keepWithNext") is False,
        )
    ):
        return False
    text_runs = [element["textRun"] for element in title_elements]
    if not all(isinstance(run.get("content"), str) for run in text_runs):
        return False
    normalized_title = normalize_header_title(
        expected_title,
        max_chars=header_tokens.max_title_chars,
    )
    if "".join(run["content"] for run in text_runs) != normalized_title + header_separator:
        return False
    return bool(text_runs) and all(_header_text_run_has_brand(run) for run in text_runs)


def _header_text_run_has_brand(text_run: dict[str, Any]) -> bool:
    header_tokens = CURRENT_BRAND_MANIFEST.document.header
    if header_tokens is None:
        return False
    style = text_run.get("textStyle")
    if not isinstance(style, dict):
        return False
    family = style.get("weightedFontFamily")
    return (
        isinstance(family, dict)
        and family.get("fontFamily") == header_tokens.font_family
        and _dimension_matches(style.get("fontSize"), header_tokens.size_pt)
        and _color_matches(style.get("foregroundColor"), header_tokens.color)
        and style.get("bold") is header_tokens.bold
    )


def _paragraphs_by_range(
    tabs: tuple[tuple[str, dict[str, Any]], ...],
) -> dict[ParagraphRangeKey, dict[str, Any]]:
    paragraphs: dict[ParagraphRangeKey, dict[str, Any]] = {}
    for tab_id, document_tab in tabs:
        body = document_tab.get("body", {})
        content = body.get("content", []) if isinstance(body, dict) else []
        if not isinstance(content, list):
            return {}
        for element in content:
            if not isinstance(element, dict) or not isinstance(element.get("paragraph"), dict):
                continue
            start = element.get("startIndex")
            end = element.get("endIndex")
            if isinstance(start, int) and isinstance(end, int):
                paragraphs[
                    ParagraphRangeKey(
                        tab_id=tab_id,
                        start_index=start,
                        end_index=end - 1,
                    )
                ] = {
                    **_effective_paragraph(element["paragraph"], document_tab),
                    "_sourceParagraphStyleSha256": _paragraph_style_digest(element["paragraph"]),
                }
    return paragraphs


def _effective_paragraph(paragraph: dict[str, Any], document_tab: dict[str, Any]) -> dict[str, Any]:
    local = paragraph.get("paragraphStyle", {})
    if not isinstance(local, dict):
        return paragraph
    named = document_tab.get("namedStyles", {})
    styles = named.get("styles", []) if isinstance(named, dict) else []
    by_name = (
        {
            name: style
            for style in styles
            if isinstance(style, dict) and isinstance(name := style.get("namedStyleType"), str)
        }
        if isinstance(styles, list)
        else {}
    )
    paragraph_style: dict[str, Any] = {}
    text_style: dict[str, Any] = {}
    for name in ("NORMAL_TEXT", local.get("namedStyleType", "NORMAL_TEXT")):
        parent = by_name.get(name, {})
        for field, target in (("paragraphStyle", paragraph_style), ("textStyle", text_style)):
            value = parent.get(field)
            if isinstance(value, dict):
                target.update(value)
    paragraph_style.update(local)
    elements = paragraph.get("elements", [])
    if not isinstance(elements, list):
        return paragraph
    resolved = []
    for element in elements:
        if not isinstance(element, dict) or not isinstance(element.get("textRun"), dict):
            resolved.append(element)
            continue
        run = element["textRun"]
        local_text = run.get("textStyle", {})
        if not isinstance(local_text, dict):
            resolved.append(element)
            continue
        resolved.append({**element, "textRun": {**run, "textStyle": {**text_style, **local_text}}})
    return {**paragraph, "paragraphStyle": paragraph_style, "elements": resolved}


def _paragraph_style_digest(paragraph: dict[str, Any]) -> str | None:
    style = paragraph.get("paragraphStyle")
    if not isinstance(style, dict):
        return None
    try:
        return paragraph_layout_sha256(style)
    except TypeError, ValueError:
        return None


def _preserved_layout_matches(paragraph: dict[str, Any], block: TextBlock) -> bool:
    if block.preserved_paragraph_style_sha256 is None:
        return False
    return paragraph.get("_sourceParagraphStyleSha256") == block.preserved_paragraph_style_sha256


def _paragraph_has_brand(
    paragraph: dict[str, Any],
    block: TextBlock,
    *,
    preserve_text_style: bool = False,
    allow_preserved_visual_authority: bool = False,
) -> bool:
    elements = paragraph.get("elements", [])
    paragraph_style = paragraph.get("paragraphStyle", {})
    if not isinstance(elements, list) or not isinstance(paragraph_style, dict):
        return False
    if allow_preserved_visual_authority and block.preserved_visual_authority:
        return True
    expected_named_style = paragraph_named_style(block)
    spacing = paragraph_spacing(block, SarjBrandProfile())
    style_checks = (
        paragraph_style.get("namedStyleType") == expected_named_style,
        paragraph_style.get("alignment") == "START",
        paragraph_style.get("lineSpacing") == CURRENT_BRAND_MANIFEST.document.line_spacing_percent,
        _dimension_matches(
            paragraph_style.get("spaceAbove"),
            spacing.above,
        ),
        _dimension_matches(
            paragraph_style.get("spaceBelow"),
            spacing.below,
        ),
        paragraph_style.get("keepWithNext") is paragraph_keep_with_next(block),
        block.pagination_keep_with_next is None or paragraph_style.get("keepLinesTogether") is True,
    )
    if not (
        _preserved_layout_matches(paragraph, block)
        if block.preserved_paragraph_style_sha256 is not None
        else all(style_checks)
    ):
        return False
    if preserve_text_style:
        return True
    expected_size = _brand_font_size(block)
    expected_color = paragraph_color(block, SarjBrandProfile())
    return all(
        _text_run_has_brand(part["textRun"], expected_size, expected_color, block)
        for part in elements
        if isinstance(part, dict)
        and isinstance(part.get("textRun"), dict)
        and isinstance(part["textRun"].get("content"), str)
        and part["textRun"]["content"].strip()
    )


def _text_run_has_brand(
    text_run: dict[str, Any],
    expected_size: int,
    expected_color: str,
    block: TextBlock,
) -> bool:
    style = text_run.get("textStyle")
    if not isinstance(style, dict):
        return False
    family = style.get("weightedFontFamily")
    color = style.get("foregroundColor")
    return (
        isinstance(family, dict)
        and family.get("fontFamily") == CURRENT_BRAND_MANIFEST.document.font_family
        and _dimension_matches(style.get("fontSize"), expected_size)
        and _color_matches(color, expected_color)
        and (
            block.kind is BlockKind.HEADING
            or all(style.get(key, False) is value for key, value in block.authored_emphasis)
        )
        and (
            block.kind is not BlockKind.HEADING
            or style.get("bold", False) is (block.native_named_style != "SUBTITLE")
        )
    )


def _brand_font_size(block: TextBlock) -> int:
    return paragraph_font_size(block, SarjBrandProfile())


def _dimension_matches(value: object, magnitude: int) -> bool:
    return (
        isinstance(value, dict)
        and value.get("unit") == "PT"
        and value.get("magnitude", 0) == magnitude
    )


def _color_matches(value: object, expected_hex: str) -> bool:
    if not isinstance(value, dict):
        return False
    color = value.get("color")
    rgb = color.get("rgbColor") if isinstance(color, dict) else None
    if not isinstance(rgb, dict):
        return False
    expected = tuple(int(expected_hex[index : index + 2], 16) / 255 for index in (1, 3, 5))
    observed = tuple(rgb.get(channel, 0) for channel in ("red", "green", "blue"))
    return all(
        isinstance(actual, int | float) and abs(actual - target) < _RGB_TOLERANCE
        for actual, target in zip(observed, expected, strict=True)
    )


def _values_for_key(value: object, target: str) -> tuple[str, ...]:
    values: list[str] = []
    match value:
        case dict() as mapping:
            candidate = mapping.get(target)
            if isinstance(candidate, str):
                values.append(candidate)
            for child in mapping.values():
                values.extend(_values_for_key(child, target))
        case list() as items:
            for child in items:
                values.extend(_values_for_key(child, target))
        case _:
            pass
    return tuple(values)


def _inline_object_uses_canonical_logo(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    properties = value.get("inlineObjectProperties")
    embedded = properties.get("embeddedObject") if isinstance(properties, dict) else None
    image = embedded.get("imageProperties") if isinstance(embedded, dict) else None
    size = embedded.get("size") if isinstance(embedded, dict) else None
    header_tokens = CURRENT_BRAND_MANIFEST.document.header
    asset_width = CURRENT_BRAND_MANIFEST.assets.logo_pixel_width
    asset_height = CURRENT_BRAND_MANIFEST.assets.logo_pixel_height
    return (
        header_tokens is not None
        and asset_width is not None
        and asset_height is not None
        and isinstance(image, dict)
        and image.get("sourceUri") == CURRENT_BRAND_MANIFEST.assets.logo_uri
        and isinstance(size, dict)
        and _fitted_logo_size_matches(
            size,
            pixel_width=asset_width,
            pixel_height=asset_height,
            box_width=header_tokens.logo_width_pt,
            box_height=header_tokens.logo_height_pt,
            tolerance=header_tokens.logo_size_tolerance_pt,
        )
    )


def _fitted_logo_size_matches(
    size: object,
    *,
    pixel_width: int,
    pixel_height: int,
    box_width: int,
    box_height: int,
    tolerance: float,
) -> bool:
    if not isinstance(size, dict):
        return False
    width = _point_magnitude(size.get("width"))
    height = _point_magnitude(size.get("height"))
    if width is None or height is None:
        return False
    scale = min(box_width / pixel_width, box_height / pixel_height)
    return (
        abs(width - pixel_width * scale) <= tolerance
        and abs(height - pixel_height * scale) <= tolerance
    )


def _point_magnitude(value: object) -> float | None:
    if not isinstance(value, dict) or value.get("unit") != "PT":
        return None
    magnitude = value.get("magnitude")
    return float(magnitude) if isinstance(magnitude, int | float) else None
