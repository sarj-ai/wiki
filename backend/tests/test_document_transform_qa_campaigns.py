from datetime import UTC, datetime, timedelta
from unittest.mock import create_autospec
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.document_transform import qa_campaigns
from app.document_transform.qa_broker import QaDestinationPermissionProof
from app.document_transform.qa_campaigns import (
    QaCorpusCampaignError,
    create_or_load_qa_corpus_campaign,
)
from app.document_transform.qa_corpus import (
    QaCorpusDocument,
    QaCorpusMemberVersion,
    QaCorpusPlan,
    QaCorpusSourceFolder,
    QaCorpusUnsupportedFile,
    UnsupportedCorpusReason,
)
from app.document_transform.qa_grants import QaAuthority, QaCampaignAuthoritySnapshot
from app.document_transform.schemas import TransformMode
from app.drive.schemas import MIME_DOC, MIME_FOLDER
from app.identifiers import (
    DriveId,
    FileId,
    QaPlanId,
    QaRunId,
    UserId,
)
from app.models.document_transform_qa_grant import DocumentTransformQaGrantId


_NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)
_USER_ID = UserId(uuid.UUID("11111111-1111-4111-8111-111111111111"))
_GRANT_ID = DocumentTransformQaGrantId(uuid.UUID("22222222-2222-4222-8222-222222222222"))
_RUN_ID = QaRunId(uuid.UUID("33333333-3333-4333-8333-333333333333"))


def _authority(*, mode: TransformMode = TransformMode.FORMAT) -> QaAuthority:
    return QaAuthority(
        grant_id=_GRANT_ID,
        user_id=_USER_ID,
        user_email="owner@example.test",
        execution_name="projects/test/locations/test/jobs/qa/executions/one",
        release_sha="a" * 40,
        image_digest="sha256:" + "b" * 64,
        source_folder_id=FileId("security-folder"),
        destination_folder_id=FileId("owner-only-folder"),
        mode=mode,
        expires_at=_NOW + timedelta(minutes=5),
        job_expires_at=_NOW + timedelta(hours=1),
    )


def _plan(*, count: int = 20, include_unsupported: bool = False) -> QaCorpusPlan:
    documents = tuple(
        QaCorpusDocument(
            file_id=FileId(f"document-{ordinal}"),
            name=f"Security Document {ordinal}",
            mime_type=MIME_DOC,
            kind="native_google_doc",
            parent_folder_id=FileId("security-folder"),
            drive_id=DriveId("security-drive"),
            modified_time=f"2026-09-08T12:{ordinal:02}:00Z",
            version=str(ordinal + 1),
            supported_modes=("format", "unslop", "both"),
        )
        for ordinal in range(count)
    )
    unsupported = (
        (
            QaCorpusUnsupportedFile(
                file_id=FileId("sheet"),
                name="Sheet",
                mime_type="application/vnd.google-apps.spreadsheet",
                parent_folder_id=FileId("security-folder"),
                drive_id=DriveId("security-drive"),
                version="1",
                reason=UnsupportedCorpusReason.UNSUPPORTED_MIME_TYPE,
            ),
        )
        if include_unsupported
        else ()
    )
    return QaCorpusPlan(
        plan_id=QaPlanId(uuid.UUID("55555555-5555-5555-8555-555555555555")),
        source_folder_id=FileId("security-folder"),
        source_drive_id=DriveId("security-drive"),
        source_folder=QaCorpusSourceFolder(
            file_id=FileId("security-folder"),
            name="Security",
            mime_type=MIME_FOLDER,
            parent_folder_id=None,
            drive_id=DriveId("security-drive"),
            modified_time="2026-09-08T11:00:00Z",
            version="root-1",
        ),
        documents=documents,
        unsupported_files=unsupported,
        member_versions=tuple(
            QaCorpusMemberVersion(file_id=document.file_id, version=document.version)
            for document in documents
        ),
        observed_item_count=count + len(unsupported),
        traversed_folder_count=1,
    )


def _destination() -> QaDestinationPermissionProof:
    return QaDestinationPermissionProof(
        file_id=FileId("owner-only-folder"),
        mime_type=MIME_FOLDER,
        parent_folder_id=FileId("my-drive-root"),
        version="17",
        sha256="c" * 64,
        observed_at=_NOW,
    )


def _snapshot() -> QaCampaignAuthoritySnapshot:
    authority = _authority()
    return QaCampaignAuthoritySnapshot(
        run_id=_RUN_ID,
        grant_id=_GRANT_ID,
        user_id=_USER_ID,
        user_email=authority.user_email,
        execution_name=authority.execution_name,
        execution_uid=uuid.UUID("66666666-6666-4666-8666-666666666666"),
        execution_job_name="projects/test/locations/test/jobs/qa",
        bootstrap_capability_digest=b"d" * 32,
        release_sha=authority.release_sha,
        image_digest=authority.image_digest,
        source_folder_id=authority.source_folder_id,
        destination_folder_id=authority.destination_folder_id,
        mode=authority.mode,
    )


def test_prepared_campaign_is_deterministic_and_freezes_twenty_members() -> None:
    first = qa_campaigns._prepare_campaign(_authority(), _snapshot(), _plan(), _destination(), _NOW)
    second = qa_campaigns._prepare_campaign(
        _authority(), _snapshot(), _plan(), _destination(), _NOW
    )

    assert first.campaign.id == second.campaign.id
    assert first.campaign.campaign_identity_sha256 == second.campaign.campaign_identity_sha256
    assert first.campaign.state == "planned"
    assert first.campaign.canary_task_plan_id is None
    assert len(first.members) == 20
    assert [member.ordinal for member in first.members] == list(range(20))
    assert len({member.task_plan_id for member in first.members}) == 20
    assert all(member.task_plan_id == member.idempotency_key for member in first.members)


@pytest.mark.parametrize(
    "plan",
    [_plan(count=19), _plan(include_unsupported=True)],
    ids=("not-exactly-twenty", "unsupported-member"),
)
def test_campaign_rejects_an_ineligible_corpus(plan: QaCorpusPlan) -> None:
    with pytest.raises(QaCorpusCampaignError):
        qa_campaigns._validate_plan(_authority(), plan)


async def test_create_persists_once_and_identical_replay_adds_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = _authority()
    plan = _plan()
    destination = _destination()
    snapshot = _snapshot()
    db = create_autospec(AsyncSession, instance=True)
    db.scalar.return_value = _NOW
    existing: list[qa_campaigns.DocumentTransformQaCorpusCampaign | None] = [None]
    replay_members: list[tuple[qa_campaigns.DocumentTransformQaCorpusMember, ...]] = [()]

    async def load_snapshot(_db: object, *, authority: QaAuthority, for_update: bool) -> object:
        assert authority == _authority()
        assert for_update
        return snapshot

    async def campaign_for_run(_db: object, _run_id: uuid.UUID) -> object:
        return existing[0]

    async def active_campaign(_db: object, _authority: QaAuthority) -> None:
        return None

    async def members(
        _db: object, _campaign_id: qa_campaigns.DocumentTransformQaCorpusCampaignId
    ) -> tuple[qa_campaigns.DocumentTransformQaCorpusMember, ...]:
        return replay_members[0]

    monkeypatch.setattr(qa_campaigns, "load_qa_campaign_authority_snapshot", load_snapshot)
    monkeypatch.setattr(qa_campaigns, "_campaign_for_run", campaign_for_run)
    monkeypatch.setattr(qa_campaigns, "_active_campaign", active_campaign)
    monkeypatch.setattr(qa_campaigns, "_members", members)

    created = await create_or_load_qa_corpus_campaign(
        db, authority=authority, plan=plan, destination=destination
    )

    assert not created.replayed
    assert len(created.campaign.members) == 20
    assert db.add.call_count == 1
    assert len(db.add_all.call_args.args[0]) == 20
    assert db.flush.await_count == 3

    prepared = qa_campaigns._prepare_campaign(authority, snapshot, plan, destination, _NOW)
    existing[0] = prepared.campaign
    replay_members[0] = prepared.members
    added_count = db.add.call_count
    added_many_count = db.add_all.call_count
    flush_count = db.flush.await_count
    replay = await create_or_load_qa_corpus_campaign(
        db, authority=authority, plan=plan, destination=destination
    )
    assert replay.replayed
    assert db.add.call_count == added_count
    assert db.add_all.call_count == added_many_count
    assert db.flush.await_count == flush_count
