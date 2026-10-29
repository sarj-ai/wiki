from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json
from typing import TYPE_CHECKING
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.document_transform.qa_corpus import QaCorpusDocument, QaCorpusPlan
from app.document_transform.qa_grants import (
    QaAuthority,
    QaCampaignAuthoritySnapshot,
    load_qa_campaign_authority_snapshot,
)
from app.drive.schemas import MIME_FOLDER
from app.identifiers import FileId, QaPlanId
from app.models.document_transform_qa_corpus import (
    DocumentTransformQaCorpusCampaign,
    DocumentTransformQaCorpusCampaignId,
    DocumentTransformQaCorpusMember,
    DocumentTransformQaCorpusMemberId,
)


if TYPE_CHECKING:
    from app.document_transform.qa_broker import QaDestinationPermissionProof


class QaCorpusCampaignError(Exception):
    pass


_CORPUS_SIZE = 20
_SHA256_HEX_LENGTH = 64


@dataclass(frozen=True, slots=True)
class QaCorpusCampaignMemberRead:
    id: DocumentTransformQaCorpusMemberId
    ordinal: int
    source_file_id: FileId
    source_name: str
    mime_type: str
    source_parent_folder_id: FileId
    source_version: str
    source_modified_time: str
    task_plan_id: QaPlanId


@dataclass(frozen=True, slots=True)
class QaCorpusCampaignRead:
    id: DocumentTransformQaCorpusCampaignId
    corpus_plan_id: QaPlanId
    state: str
    source_folder_id: FileId
    destination_folder_id: FileId
    mode: str
    created_at: datetime
    members: tuple[QaCorpusCampaignMemberRead, ...]


@dataclass(frozen=True, slots=True)
class CreatedQaCorpusCampaign:
    campaign: QaCorpusCampaignRead
    replayed: bool


@dataclass(frozen=True, slots=True)
class _PreparedCampaign:
    campaign: DocumentTransformQaCorpusCampaign
    members: tuple[DocumentTransformQaCorpusMember, ...]


async def create_or_load_qa_corpus_campaign(
    db: AsyncSession,
    *,
    authority: QaAuthority,
    plan: QaCorpusPlan,
    destination: QaDestinationPermissionProof,
) -> CreatedQaCorpusCampaign:
    _validate_plan(authority, plan)
    _validate_destination(authority, destination)
    await db.execute(select(func.pg_advisory_xact_lock(_advisory_key(authority))))
    snapshot = await load_qa_campaign_authority_snapshot(db, authority=authority, for_update=True)
    if (now := await db.scalar(select(func.clock_timestamp()))) is None:
        raise QaCorpusCampaignError
    prepared = _prepare_campaign(authority, snapshot, plan, destination, now)

    if (existing := await _campaign_for_run(db, snapshot.run_id)) is not None:
        return CreatedQaCorpusCampaign(
            campaign=await _require_identical_campaign(db, existing, prepared),
            replayed=True,
        )

    active = await _active_campaign(db, authority)
    if active is not None:
        raise QaCorpusCampaignError

    db.add(prepared.campaign)
    await db.flush()
    db.add_all(prepared.members)
    await db.flush()
    prepared.campaign.state = "canary_reserved"
    prepared.campaign.canary_task_plan_id = prepared.members[0].task_plan_id
    await db.flush()
    return CreatedQaCorpusCampaign(
        campaign=_to_read(prepared.campaign, prepared.members), replayed=False
    )


async def load_qa_corpus_campaign_for_authority(
    db: AsyncSession,
    *,
    authority: QaAuthority,
) -> QaCorpusCampaignRead | None:
    snapshot = await load_qa_campaign_authority_snapshot(db, authority=authority)
    if (campaign := await _campaign_for_run(db, snapshot.run_id, for_update=False)) is None:
        return None
    _require_authority_binding(campaign, authority, snapshot)
    return _to_read(campaign, await _members(db, campaign.id))


def _validate_plan(authority: QaAuthority, plan: QaCorpusPlan) -> None:
    documents = plan.documents
    member_versions = plan.member_versions
    versions = {item.file_id: item.version for item in member_versions}
    header_is_valid = all(
        (
            plan.source_folder_id == authority.source_folder_id,
            plan.source_folder_id != authority.destination_folder_id,
            plan.source_folder.file_id == authority.source_folder_id,
            plan.source_folder.drive_id == plan.source_drive_id,
            len(documents) == _CORPUS_SIZE,
            not plan.unsupported_files,
            len({document.file_id for document in documents}) == _CORPUS_SIZE,
            len(member_versions) == plan.observed_item_count,
            len(versions) == len(member_versions),
        )
    )
    members_are_valid = all(
        document.drive_id == plan.source_drive_id
        and versions.get(document.file_id) == document.version
        and authority.mode.value in document.supported_modes
        for document in documents
    )
    if not header_is_valid or not members_are_valid:
        raise QaCorpusCampaignError


def _validate_destination(
    authority: QaAuthority, destination: QaDestinationPermissionProof
) -> None:
    if (
        destination.file_id != authority.destination_folder_id
        or destination.mime_type != MIME_FOLDER
        or not destination.version.isdigit()
        or not _is_sha256(destination.sha256)
        or destination.observed_at.tzinfo is None
    ):
        raise QaCorpusCampaignError


def _prepare_campaign(
    authority: QaAuthority,
    snapshot: QaCampaignAuthoritySnapshot,
    plan: QaCorpusPlan,
    destination: QaDestinationPermissionProof,
    now: datetime,
) -> _PreparedCampaign:
    corpus_plan_sha256 = _sha256(plan.model_dump(mode="json"))
    source_root_sha256 = _sha256(plan.source_folder.model_dump(mode="json"))
    destination_identity_sha256 = _sha256(
        {
            "file_id": destination.file_id,
            "mime_type": destination.mime_type,
            "parent_folder_id": destination.parent_folder_id,
        }
    )
    identity = {
        "schema": "qa-corpus-campaign-v1",
        "run_id": snapshot.run_id,
        "grant_id": snapshot.grant_id,
        "execution_uid": snapshot.execution_uid,
        "corpus_plan_sha256": corpus_plan_sha256,
        "destination_identity_sha256": destination_identity_sha256,
        "destination_permission_sha256": destination.sha256,
        "destination_version": destination.version,
        "mode": authority.mode.value,
    }
    campaign_identity_sha256 = _sha256(identity)
    campaign_id = DocumentTransformQaCorpusCampaignId(
        _stable_uuid("qa-corpus-campaign-id-v1", campaign_identity_sha256)
    )
    members = tuple(
        _prepare_member(
            campaign_id=campaign_id,
            campaign_identity_sha256=campaign_identity_sha256,
            authority=authority,
            document=document,
            ordinal=ordinal,
            created_at=now,
        )
        for ordinal, document in enumerate(plan.documents)
    )
    campaign = DocumentTransformQaCorpusCampaign(
        id=campaign_id,
        qa_run_id=snapshot.run_id,
        qa_grant_id=snapshot.grant_id,
        user_id=authority.user_id,
        user_email=authority.user_email,
        execution_name=authority.execution_name,
        execution_uid=snapshot.execution_uid,
        execution_job_name=snapshot.execution_job_name,
        bootstrap_capability_digest=snapshot.bootstrap_capability_digest,
        release_sha=authority.release_sha,
        image_digest=authority.image_digest,
        mode=authority.mode.value,
        source_folder_id=authority.source_folder_id,
        source_drive_id=plan.source_drive_id,
        destination_folder_id=authority.destination_folder_id,
        destination_drive_id=None,
        corpus_plan_id=plan.plan_id,
        campaign_identity_sha256=campaign_identity_sha256,
        corpus_plan_sha256=corpus_plan_sha256,
        source_root_sha256=source_root_sha256,
        destination_identity_sha256=destination_identity_sha256,
        destination_permission_sha256=destination.sha256,
        destination_version_at_plan=destination.version,
        document_count=_CORPUS_SIZE,
        unsupported_count=0,
        state="planned",
        canary_task_plan_id=None,
        created_at=now,
    )
    return _PreparedCampaign(campaign, members)


def _prepare_member(
    *,
    campaign_id: DocumentTransformQaCorpusCampaignId,
    campaign_identity_sha256: str,
    authority: QaAuthority,
    document: QaCorpusDocument,
    ordinal: int,
    created_at: datetime,
) -> DocumentTransformQaCorpusMember:
    fingerprint = _sha256(
        {
            "file_id": document.file_id,
            "name": document.name,
            "mime_type": document.mime_type,
            "parent_folder_id": document.parent_folder_id,
            "drive_id": document.drive_id,
            "modified_time": document.modified_time,
            "version": document.version,
        }
    )
    member_id = DocumentTransformQaCorpusMemberId(
        _stable_uuid("qa-corpus-member-id-v1", str(campaign_id), fingerprint)
    )
    task_plan_id = QaPlanId(
        _stable_uuid(
            "qa-corpus-task-plan-v1",
            campaign_identity_sha256,
            fingerprint,
            authority.mode.value,
        )
    )
    return DocumentTransformQaCorpusMember(
        id=member_id,
        campaign_id=campaign_id,
        ordinal=ordinal,
        source_file_id=document.file_id,
        source_name=document.name,
        source_mime_type=document.mime_type,
        source_parent_folder_id=document.parent_folder_id,
        source_drive_id=document.drive_id,
        source_version=document.version,
        source_modified_time=document.modified_time,
        source_fingerprint_sha256=fingerprint,
        selected_mode=authority.mode.value,
        task_plan_id=task_plan_id,
        idempotency_key=task_plan_id,
        created_at=created_at,
    )


async def _campaign_for_run(
    db: AsyncSession, run_id: uuid.UUID, *, for_update: bool = True
) -> DocumentTransformQaCorpusCampaign | None:
    statement = select(DocumentTransformQaCorpusCampaign).where(
        DocumentTransformQaCorpusCampaign.qa_run_id == run_id
    )
    if for_update:
        statement = statement.with_for_update()
    rows = await db.scalars(statement.execution_options(populate_existing=True))
    return rows.one_or_none()


def _require_authority_binding(
    campaign: DocumentTransformQaCorpusCampaign,
    authority: QaAuthority,
    snapshot: QaCampaignAuthoritySnapshot,
) -> None:
    stored_binding = (
        campaign.qa_run_id,
        campaign.qa_grant_id,
        campaign.user_id,
        campaign.user_email.casefold(),
        campaign.execution_name,
        campaign.execution_uid,
        campaign.execution_job_name,
        campaign.release_sha,
        campaign.image_digest,
        campaign.source_folder_id,
        campaign.destination_folder_id,
        campaign.mode,
    )
    expected_binding = (
        snapshot.run_id,
        snapshot.grant_id,
        authority.user_id,
        authority.user_email.casefold(),
        authority.execution_name,
        snapshot.execution_uid,
        snapshot.execution_job_name,
        authority.release_sha,
        authority.image_digest,
        authority.source_folder_id,
        authority.destination_folder_id,
        authority.mode.value,
    )
    snapshot_matches_authority = (
        snapshot.user_id,
        snapshot.user_email.casefold(),
        snapshot.execution_name,
        snapshot.release_sha,
        snapshot.image_digest,
        snapshot.source_folder_id,
        snapshot.destination_folder_id,
        snapshot.mode,
    ) == (
        authority.user_id,
        authority.user_email.casefold(),
        authority.execution_name,
        authority.release_sha,
        authority.image_digest,
        authority.source_folder_id,
        authority.destination_folder_id,
        authority.mode,
    )
    if not all(
        (
            stored_binding == expected_binding,
            snapshot_matches_authority,
            hmac.compare_digest(
                campaign.bootstrap_capability_digest, snapshot.bootstrap_capability_digest
            ),
        )
    ):
        raise QaCorpusCampaignError


async def _active_campaign(
    db: AsyncSession, authority: QaAuthority
) -> DocumentTransformQaCorpusCampaign | None:
    rows = await db.scalars(
        select(DocumentTransformQaCorpusCampaign)
        .where(
            DocumentTransformQaCorpusCampaign.user_id == authority.user_id,
            DocumentTransformQaCorpusCampaign.source_folder_id == authority.source_folder_id,
            DocumentTransformQaCorpusCampaign.destination_folder_id
            == authority.destination_folder_id,
            DocumentTransformQaCorpusCampaign.mode == authority.mode.value,
            DocumentTransformQaCorpusCampaign.state.not_in(("verified", "failed")),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return rows.one_or_none()


async def _require_identical_campaign(
    db: AsyncSession,
    existing: DocumentTransformQaCorpusCampaign,
    prepared: _PreparedCampaign,
) -> QaCorpusCampaignRead:
    expected = prepared.campaign
    if not hmac.compare_digest(
        existing.campaign_identity_sha256, expected.campaign_identity_sha256
    ):
        raise QaCorpusCampaignError
    members = await _members(db, existing.id)
    if tuple(_member_binding(member) for member in members) != tuple(
        _member_binding(member) for member in prepared.members
    ):
        raise QaCorpusCampaignError
    return _to_read(existing, members)


async def _members(
    db: AsyncSession, campaign_id: DocumentTransformQaCorpusCampaignId
) -> tuple[DocumentTransformQaCorpusMember, ...]:
    return tuple(
        (
            await db.scalars(
                select(DocumentTransformQaCorpusMember)
                .where(DocumentTransformQaCorpusMember.campaign_id == campaign_id)
                .order_by(DocumentTransformQaCorpusMember.ordinal)
            )
        ).all()
    )


def _member_binding(member: DocumentTransformQaCorpusMember) -> tuple[object, ...]:
    return (
        member.ordinal,
        member.source_file_id,
        member.source_name,
        member.source_mime_type,
        member.source_parent_folder_id,
        member.source_drive_id,
        member.source_version,
        member.source_modified_time,
        member.source_fingerprint_sha256,
        member.selected_mode,
        member.task_plan_id,
        member.idempotency_key,
    )


def _to_read(
    campaign: DocumentTransformQaCorpusCampaign,
    members: tuple[DocumentTransformQaCorpusMember, ...],
) -> QaCorpusCampaignRead:
    if len(members) != _CORPUS_SIZE:
        raise QaCorpusCampaignError
    return QaCorpusCampaignRead(
        id=campaign.id,
        corpus_plan_id=campaign.corpus_plan_id,
        state=campaign.state,
        source_folder_id=campaign.source_folder_id,
        destination_folder_id=campaign.destination_folder_id,
        mode=campaign.mode,
        created_at=campaign.created_at,
        members=tuple(
            QaCorpusCampaignMemberRead(
                id=member.id,
                ordinal=member.ordinal,
                source_file_id=member.source_file_id,
                source_name=member.source_name,
                mime_type=member.source_mime_type,
                source_parent_folder_id=member.source_parent_folder_id,
                source_version=member.source_version,
                source_modified_time=member.source_modified_time,
                task_plan_id=member.task_plan_id,
            )
            for member in members
        ),
    )


def _advisory_key(authority: QaAuthority) -> int:
    digest = hashlib.sha256(
        _canonical_json(
            {
                "domain": "qa-corpus-admission-v1",
                "user_id": authority.user_id,
                "source_folder_id": authority.source_folder_id,
                "destination_folder_id": authority.destination_folder_id,
                "mode": authority.mode.value,
            }
        )
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _stable_uuid(domain: str, *values: object) -> uuid.UUID:
    digest = bytearray(hashlib.sha256(_canonical_json([domain, *values])).digest()[:16])
    digest[6] = (digest[6] & 0x0F) | 0x50
    digest[8] = (digest[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(digest))


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode()


def _is_sha256(value: str) -> bool:
    return len(value) == _SHA256_HEX_LENGTH and all(
        character in "0123456789abcdef" for character in value
    )
