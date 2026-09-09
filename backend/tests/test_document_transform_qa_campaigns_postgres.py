from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import uuid

from pydantic_settings import BaseSettings, SettingsConfigDict
import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.document_transform import qa_campaigns
from app.document_transform.qa_broker import QaDestinationPermissionProof
from app.document_transform.qa_campaigns import (
    QaCorpusCampaignError,
    create_or_load_qa_corpus_campaign,
    load_qa_corpus_campaign_for_authority,
)
from app.document_transform.qa_corpus import (
    QaCorpusDocument,
    QaCorpusMemberVersion,
    QaCorpusPlan,
    QaCorpusSourceFolder,
)
from app.document_transform.qa_grants import QaAuthority, QaCampaignAuthoritySnapshot
from app.document_transform.schemas import TransformMode
from app.drive.schemas import MIME_DOC, MIME_FOLDER
from app.identifiers import DriveId, FileId, QaPlanId, QaRunId, UserId
from app.models.document_transform_qa_corpus import (
    DocumentTransformQaCorpusCampaign,
    DocumentTransformQaCorpusMember,
)
from app.models.document_transform_qa_grant import DocumentTransformQaGrantId


class _QaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WIKI_POLISH29_QA_")
    database_url: str | None = None


@dataclass(frozen=True, slots=True)
class _PostgresHarness:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]


@dataclass(frozen=True, slots=True)
class _CampaignInputs:
    authority: QaAuthority
    snapshot: QaCampaignAuthoritySnapshot
    plan: QaCorpusPlan
    destination: QaDestinationPermissionProof


def _sessions() -> _PostgresHarness:
    dsn = _QaSettings().database_url
    if not dsn:
        pytest.skip("Requires an explicitly configured disposable PostgreSQL QA database")
    url = make_url(dsn)
    if (
        url.host not in {"127.0.0.1", "localhost", "::1"}
        or not (url.database or "").startswith("wiki_polish29_qa")
        or url.drivername != "postgresql+asyncpg"
    ):
        pytest.fail("Only a loopback PostgreSQL database named wiki_polish29_qa* is allowed")
    engine = create_async_engine(dsn, pool_size=4, max_overflow=0)
    return _PostgresHarness(
        engine=engine,
        sessions=async_sessionmaker(engine, expire_on_commit=False),
    )


def _inputs(seed: uuid.UUID) -> _CampaignInputs:
    now = datetime.now(UTC)
    user_id = UserId(uuid.uuid5(seed, "user"))
    grant_id = DocumentTransformQaGrantId(uuid.uuid5(seed, "grant"))
    run_id = QaRunId(uuid.uuid5(seed, "run"))
    source = FileId(f"security-{seed.hex}")
    destination = FileId(f"destination-{seed.hex}")
    drive = DriveId(f"drive-{seed.hex}")
    authority = QaAuthority(
        grant_id=grant_id,
        user_id=user_id,
        user_email=f"qa-{seed.hex}@example.test",
        execution_name=f"projects/test/locations/test/jobs/qa/executions/{seed.hex}",
        release_sha="a" * 40,
        image_digest="sha256:" + "b" * 64,
        source_folder_id=source,
        destination_folder_id=destination,
        mode=TransformMode.FORMAT,
        expires_at=now + timedelta(minutes=5),
        job_expires_at=now + timedelta(hours=1),
    )
    snapshot = QaCampaignAuthoritySnapshot(
        run_id=run_id,
        grant_id=grant_id,
        user_id=user_id,
        user_email=authority.user_email,
        execution_name=authority.execution_name,
        execution_uid=uuid.uuid5(seed, "execution"),
        execution_job_name="projects/test/locations/test/jobs/qa",
        bootstrap_capability_digest=seed.bytes + seed.bytes,
        release_sha=authority.release_sha,
        image_digest=authority.image_digest,
        source_folder_id=source,
        destination_folder_id=destination,
        mode=authority.mode,
    )
    documents = tuple(
        QaCorpusDocument(
            file_id=FileId(f"document-{seed.hex}-{ordinal}"),
            name=f"Security Document {ordinal}",
            mime_type=MIME_DOC,
            kind="native_google_doc",
            parent_folder_id=source,
            drive_id=drive,
            modified_time=f"2026-09-08T12:{ordinal:02}:00Z",
            version=str(ordinal + 1),
            supported_modes=("format", "unslop", "both"),
        )
        for ordinal in range(20)
    )
    plan = QaCorpusPlan(
        plan_id=QaPlanId(uuid.uuid5(seed, "plan")),
        source_folder_id=source,
        source_drive_id=drive,
        source_folder=QaCorpusSourceFolder(
            file_id=source,
            name="Security",
            mime_type=MIME_FOLDER,
            parent_folder_id=None,
            drive_id=drive,
            modified_time="2026-09-08T11:00:00Z",
            version="root-1",
        ),
        documents=documents,
        unsupported_files=(),
        member_versions=tuple(
            QaCorpusMemberVersion(file_id=document.file_id, version=document.version)
            for document in documents
        ),
        observed_item_count=20,
        traversed_folder_count=1,
    )
    destination_proof = QaDestinationPermissionProof(
        file_id=destination,
        mime_type=MIME_FOLDER,
        parent_folder_id=FileId("my-drive-root"),
        version="17",
        sha256=seed.hex * 2,
        observed_at=now,
    )
    return _CampaignInputs(
        authority=authority,
        snapshot=snapshot,
        plan=plan,
        destination=destination_proof,
    )


async def test_real_postgres_concurrent_campaign_create_has_one_exact_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _sessions()
    inputs = _inputs(uuid.uuid7())
    authority, snapshot = inputs.authority, inputs.snapshot

    async def load_snapshot(*_args: object, **_kwargs: object) -> QaCampaignAuthoritySnapshot:
        return snapshot

    monkeypatch.setattr(qa_campaigns, "load_qa_campaign_authority_snapshot", load_snapshot)

    async def create() -> bool:
        async with harness.sessions() as db:
            result = await create_or_load_qa_corpus_campaign(
                db, authority=authority, plan=inputs.plan, destination=inputs.destination
            )
            await db.commit()
            return result.replayed

    try:
        assert sorted(await asyncio.gather(create(), create())) == [False, True]
        async with harness.sessions() as db:
            campaign_count = await db.scalar(
                select(func.count())
                .select_from(DocumentTransformQaCorpusCampaign)
                .where(DocumentTransformQaCorpusCampaign.qa_run_id == snapshot.run_id)
            )
            campaign = await db.scalar(
                select(DocumentTransformQaCorpusCampaign).where(
                    DocumentTransformQaCorpusCampaign.qa_run_id == snapshot.run_id
                )
            )
            assert campaign is not None
            member_count = await db.scalar(
                select(func.count())
                .select_from(DocumentTransformQaCorpusMember)
                .where(DocumentTransformQaCorpusMember.campaign_id == campaign.id)
            )
        assert campaign_count == 1
        assert member_count == 20
        assert campaign.state == "canary_reserved"
    finally:
        await harness.engine.dispose()


async def test_real_postgres_authority_read_is_exact_run_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _sessions()
    inputs = _inputs(uuid.uuid7())
    authority, snapshot = inputs.authority, inputs.snapshot

    async def load_snapshot(*_args: object, **_kwargs: object) -> QaCampaignAuthoritySnapshot:
        return snapshot

    monkeypatch.setattr(qa_campaigns, "load_qa_campaign_authority_snapshot", load_snapshot)
    try:
        async with harness.sessions() as db:
            created = await create_or_load_qa_corpus_campaign(
                db, authority=authority, plan=inputs.plan, destination=inputs.destination
            )
            await db.commit()
        async with harness.sessions() as db:
            loaded = await load_qa_corpus_campaign_for_authority(db, authority=authority)
            assert loaded is not None
            assert loaded.id == created.campaign.id
        mismatched = replace(authority, execution_name=authority.execution_name + "-other")
        async with harness.sessions() as db:
            with pytest.raises(QaCorpusCampaignError):
                await load_qa_corpus_campaign_for_authority(db, authority=mismatched)
    finally:
        await harness.engine.dispose()
