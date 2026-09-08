from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import uuid

from pydantic_settings import BaseSettings, SettingsConfigDict
import pytest
from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models.document_transform_qa_corpus import (
    DocumentTransformQaCorpusCampaign,
    DocumentTransformQaCorpusMember,
    DocumentTransformQaCorpusSubmission,
)


class _QaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WIKI_POLISH29_QA_")
    database_url: str | None = None


def _qa_engine() -> AsyncEngine:
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
    return create_async_engine(dsn, pool_size=4, max_overflow=0)


def _digest(seed: uuid.UUID, suffix: str) -> str:
    return (seed.hex + suffix.encode().hex()).ljust(64, "0")[:64]


def _campaign_values(
    seed: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
    source_folder_id: str | None = None,
    destination_folder_id: str | None = None,
) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "id": seed,
        "qa_run_id": uuid.uuid7(),
        "qa_grant_id": uuid.uuid7(),
        "user_id": user_id or uuid.uuid7(),
        "user_email": f"qa-{seed.hex}@example.test",
        "execution_name": f"projects/test/locations/test/executions/{seed.hex}",
        "execution_uid": uuid.uuid7(),
        "execution_job_name": "projects/test/locations/test/jobs/wiki-qa",
        "bootstrap_capability_digest": seed.bytes + seed.bytes,
        "release_sha": "a" * 40,
        "image_digest": "sha256:" + "b" * 64,
        "mode": "format",
        "source_folder_id": source_folder_id or f"source-{seed.hex}",
        "source_drive_id": f"drive-{seed.hex}",
        "destination_folder_id": destination_folder_id or f"destination-{seed.hex}",
        "destination_drive_id": None,
        "corpus_plan_id": uuid.uuid7(),
        "campaign_identity_sha256": _digest(seed, "identity"),
        "corpus_plan_sha256": _digest(seed, "plan"),
        "source_root_sha256": _digest(seed, "root"),
        "destination_identity_sha256": _digest(seed, "destination"),
        "destination_permission_sha256": _digest(seed, "permission"),
        "destination_version_at_plan": "17",
        "document_count": 20,
        "unsupported_count": 0,
        "state": "planned",
        "created_at": now,
    }


def _member_values(campaign: dict[str, object], ordinal: int) -> dict[str, object]:
    member_id = uuid.uuid7()
    task_plan_id = uuid.uuid7()
    return {
        "id": member_id,
        "campaign_id": campaign["id"],
        "ordinal": ordinal,
        "source_file_id": f"source-document-{campaign['id']}-{ordinal}",
        "source_name": f"Security document {ordinal}",
        "source_mime_type": "application/vnd.google-apps.document",
        "source_parent_folder_id": campaign["source_folder_id"],
        "source_drive_id": campaign["source_drive_id"],
        "source_version": "1",
        "source_modified_time": "2026-09-08T12:00:00Z",
        "source_fingerprint_sha256": _digest(member_id, "source"),
        "selected_mode": campaign["mode"],
        "task_plan_id": task_plan_id,
        "idempotency_key": task_plan_id,
        "created_at": campaign["created_at"],
    }


def _submission_values(campaign: dict[str, object], member: dict[str, object]) -> dict[str, object]:
    return {
        "id": uuid.uuid7(),
        "campaign_id": campaign["id"],
        "member_id": member["id"],
        "task_plan_id": member["task_plan_id"],
        "idempotency_key": member["task_plan_id"],
        "source_fingerprint_sha256": member["source_fingerprint_sha256"],
        "destination_identity_sha256": campaign["destination_identity_sha256"],
        "destination_permission_sha256": campaign["destination_permission_sha256"],
        "state": "reserved",
        "reserved_at": campaign["created_at"],
    }


async def _create_planned_campaign(
    db: AsyncSession, campaign: dict[str, object], *, member_count: int = 20
) -> list[dict[str, object]]:
    await db.execute(insert(DocumentTransformQaCorpusCampaign).values(**campaign))
    members = [_member_values(campaign, ordinal) for ordinal in range(member_count)]
    if members:
        await db.execute(insert(DocumentTransformQaCorpusMember), members)
    return members


async def _commit_incomplete_plan(db: AsyncSession, campaign: dict[str, object]) -> None:
    async with db.begin():
        members = await _create_planned_campaign(db, campaign, member_count=19)
        await db.execute(
            update(DocumentTransformQaCorpusCampaign)
            .where(DocumentTransformQaCorpusCampaign.id == campaign["id"])
            .values(state="canary_reserved", canary_task_plan_id=members[0]["task_plan_id"])
        )


async def _commit_reserved_canary_as_submitted(
    db: AsyncSession,
    campaign: dict[str, object],
    member: dict[str, object],
) -> None:
    async with db.begin():
        await db.execute(
            insert(DocumentTransformQaCorpusSubmission).values(
                **_submission_values(campaign, member)
            )
        )
        await db.execute(
            update(DocumentTransformQaCorpusCampaign)
            .where(DocumentTransformQaCorpusCampaign.id == campaign["id"])
            .values(state="canary_submitted", canary_task_id=uuid.uuid7())
        )


async def test_real_postgres_freezes_complete_member_plan_at_commit() -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    seed = uuid.uuid7()
    campaign = _campaign_values(seed)
    try:
        async with sessions() as db, db.begin():
            members = await _create_planned_campaign(db, campaign)
            await db.execute(
                update(DocumentTransformQaCorpusCampaign)
                .where(DocumentTransformQaCorpusCampaign.id == seed)
                .values(state="canary_reserved", canary_task_plan_id=members[0]["task_plan_id"])
            )

        async with sessions() as db:
            with pytest.raises(DBAPIError, match="members are frozen"):
                async with db.begin():
                    await db.execute(
                        delete(DocumentTransformQaCorpusMember).where(
                            DocumentTransformQaCorpusMember.campaign_id == seed,
                            DocumentTransformQaCorpusMember.ordinal == 19,
                        )
                    )
    finally:
        async with sessions() as db, db.begin():
            await db.execute(
                update(DocumentTransformQaCorpusCampaign)
                .where(DocumentTransformQaCorpusCampaign.id == seed)
                .values(state="failed", failed_at=datetime.now(UTC))
            )
        await engine.dispose()


async def test_real_postgres_rejects_incomplete_plan_and_cross_bound_submission() -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    incomplete = _campaign_values(uuid.uuid7())
    async with sessions() as db:
        with pytest.raises(DBAPIError, match="exactly 20"):
            await _commit_incomplete_plan(db, incomplete)

    campaign = _campaign_values(uuid.uuid7())
    try:
        async with sessions() as db, db.begin():
            members = await _create_planned_campaign(db, campaign)
            await db.execute(
                update(DocumentTransformQaCorpusCampaign)
                .where(DocumentTransformQaCorpusCampaign.id == campaign["id"])
                .values(state="canary_reserved", canary_task_plan_id=members[0]["task_plan_id"])
            )
        async with sessions() as db:
            with pytest.raises(DBAPIError, match="differs from frozen plan"):
                async with db.begin():
                    await db.execute(
                        insert(DocumentTransformQaCorpusSubmission).values(
                            id=uuid.uuid7(),
                            campaign_id=campaign["id"],
                            member_id=members[0]["id"],
                            task_plan_id=members[0]["task_plan_id"],
                            idempotency_key=members[0]["task_plan_id"],
                            source_fingerprint_sha256=members[0]["source_fingerprint_sha256"],
                            destination_identity_sha256="c" * 64,
                            destination_permission_sha256=campaign["destination_permission_sha256"],
                            state="reserved",
                            reserved_at=campaign["created_at"],
                        )
                    )
    finally:
        async with sessions() as db, db.begin():
            await db.execute(
                update(DocumentTransformQaCorpusCampaign)
                .where(DocumentTransformQaCorpusCampaign.id == campaign["id"])
                .values(state="failed", failed_at=datetime.now(UTC))
            )
        await engine.dispose()


async def test_real_postgres_canary_submitted_requires_exact_submitted_ordinal_zero() -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    campaign = _campaign_values(uuid.uuid7())
    try:
        async with sessions() as db, db.begin():
            members = await _create_planned_campaign(db, campaign)
            await db.execute(
                update(DocumentTransformQaCorpusCampaign)
                .where(DocumentTransformQaCorpusCampaign.id == campaign["id"])
                .values(state="canary_reserved", canary_task_plan_id=members[0]["task_plan_id"])
            )
        async with sessions() as db:
            with pytest.raises(DBAPIError, match="canary submission is not exactly bound"):
                await _commit_reserved_canary_as_submitted(db, campaign, members[0])
    finally:
        async with sessions() as db, db.begin():
            await db.execute(
                update(DocumentTransformQaCorpusCampaign)
                .where(DocumentTransformQaCorpusCampaign.id == campaign["id"])
                .values(state="failed", failed_at=datetime.now(UTC))
            )
        await engine.dispose()


async def test_real_postgres_campaign_output_is_unique_and_chronology_is_bounded() -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    invalid_time = _campaign_values(uuid.uuid7())
    created_at = invalid_time["created_at"]
    assert isinstance(created_at, datetime)
    invalid_time.update(
        state="failed",
        canary_task_plan_id=uuid.uuid7(),
        canary_task_id=uuid.uuid7(),
        canary_result_sha256="1" * 64,
        canary_evidence_sha256="2" * 64,
        canary_output_permission_sha256="3" * 64,
        canary_needs_review=False,
        canary_review_finding_count=0,
        canary_verified_at=created_at + timedelta(hours=1),
        failed_at=created_at + timedelta(minutes=30),
    )
    async with sessions() as db:
        with pytest.raises(IntegrityError):
            async with db.begin():
                await db.execute(insert(DocumentTransformQaCorpusCampaign).values(**invalid_time))

    campaign = _campaign_values(uuid.uuid7())
    now = datetime.now(UTC)
    try:
        async with sessions() as db, db.begin():
            members = await _create_planned_campaign(db, campaign)
            await db.execute(
                update(DocumentTransformQaCorpusCampaign)
                .where(DocumentTransformQaCorpusCampaign.id == campaign["id"])
                .values(state="canary_reserved", canary_task_plan_id=members[0]["task_plan_id"])
            )
        submissions = [_submission_values(campaign, member) for member in members[:2]]
        task_ids = [uuid.uuid7(), uuid.uuid7()]
        shared_output = f"output-{campaign['id']}"
        async with sessions() as db, db.begin():
            await db.execute(insert(DocumentTransformQaCorpusSubmission), submissions)
            for ordinal, submission in enumerate(submissions):
                await db.execute(
                    update(DocumentTransformQaCorpusSubmission)
                    .where(DocumentTransformQaCorpusSubmission.id == submission["id"])
                    .values(state="submitted", task_id=task_ids[ordinal], submitted_at=now)
                )
            await db.execute(
                update(DocumentTransformQaCorpusSubmission)
                .where(DocumentTransformQaCorpusSubmission.id == submissions[0]["id"])
                .values(
                    state="verified",
                    output_file_id=shared_output,
                    output_revision_id="revision-0",
                    result_sha256="4" * 64,
                    evidence_sha256="5" * 64,
                    output_permission_sha256="6" * 64,
                    needs_review=False,
                    review_finding_count=0,
                    verified_at=now,
                )
            )
            await db.execute(
                update(DocumentTransformQaCorpusCampaign)
                .where(DocumentTransformQaCorpusCampaign.id == campaign["id"])
                .values(state="canary_submitted", canary_task_id=task_ids[0])
            )
        async with sessions() as db:
            with pytest.raises(IntegrityError):
                async with db.begin():
                    await db.execute(
                        update(DocumentTransformQaCorpusSubmission)
                        .where(DocumentTransformQaCorpusSubmission.id == submissions[1]["id"])
                        .values(
                            state="verified",
                            output_file_id=shared_output,
                            output_revision_id="revision-1",
                            result_sha256="7" * 64,
                            evidence_sha256="8" * 64,
                            output_permission_sha256="9" * 64,
                            needs_review=False,
                            review_finding_count=0,
                            verified_at=now,
                        )
                    )
    finally:
        async with sessions() as db, db.begin():
            await db.execute(
                update(DocumentTransformQaCorpusCampaign)
                .where(DocumentTransformQaCorpusCampaign.id == campaign["id"])
                .values(state="failed", failed_at=datetime.now(UTC))
            )
        await engine.dispose()


async def test_real_postgres_active_campaign_admission_has_one_winner() -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = uuid.uuid7()
    source = f"source-{uuid.uuid7().hex}"
    destination = f"destination-{uuid.uuid7().hex}"
    campaigns = [
        _campaign_values(
            uuid.uuid7(),
            user_id=owner,
            source_folder_id=source,
            destination_folder_id=destination,
        )
        for _ in range(2)
    ]

    async def admit(campaign: dict[str, object]) -> bool:
        async with sessions() as db:
            try:
                async with db.begin():
                    await db.execute(insert(DocumentTransformQaCorpusCampaign).values(**campaign))
            except IntegrityError:
                return False
            else:
                return True

    try:
        assert sorted(await asyncio.gather(*(admit(campaign) for campaign in campaigns))) == [
            False,
            True,
        ]
        async with sessions() as db:
            admitted = await db.scalar(
                select(DocumentTransformQaCorpusCampaign.id).where(
                    DocumentTransformQaCorpusCampaign.user_id == owner,
                    DocumentTransformQaCorpusCampaign.source_folder_id == source,
                    DocumentTransformQaCorpusCampaign.destination_folder_id == destination,
                )
            )
            assert admitted in {campaign["id"] for campaign in campaigns}
    finally:
        # Audit rows are intentionally immutable; this database is disposable and
        # every binding above is randomized to keep the retained fixture isolated.
        await engine.dispose()


async def test_real_postgres_terminal_campaign_requires_and_freezes_exact_twenty() -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    campaign = _campaign_values(uuid.uuid7())
    now = datetime.now(UTC)
    try:
        async with sessions() as db, db.begin():
            members = await _create_planned_campaign(db, campaign)
            await db.execute(
                update(DocumentTransformQaCorpusCampaign)
                .where(DocumentTransformQaCorpusCampaign.id == campaign["id"])
                .values(state="canary_reserved", canary_task_plan_id=members[0]["task_plan_id"])
            )

        submissions = [_submission_values(campaign, member) for member in members]
        task_ids = [uuid.uuid7() for _ in submissions]
        result_hashes = [_digest(task_id, "result") for task_id in task_ids]
        evidence_hashes = [_digest(task_id, "evidence") for task_id in task_ids]
        permission_hashes = [_digest(task_id, "owner") for task_id in task_ids]
        async with sessions() as db, db.begin():
            await db.execute(insert(DocumentTransformQaCorpusSubmission), submissions)
            await db.execute(
                update(DocumentTransformQaCorpusCampaign)
                .where(DocumentTransformQaCorpusCampaign.id == campaign["id"])
                .values(
                    state="canary_submitted",
                    canary_task_id=task_ids[0],
                )
            )
            for ordinal, submission in enumerate(submissions):
                await db.execute(
                    update(DocumentTransformQaCorpusSubmission)
                    .where(DocumentTransformQaCorpusSubmission.id == submission["id"])
                    .values(state="submitted", task_id=task_ids[ordinal], submitted_at=now)
                )
                await db.execute(
                    update(DocumentTransformQaCorpusSubmission)
                    .where(DocumentTransformQaCorpusSubmission.id == submission["id"])
                    .values(
                        state="verified",
                        output_file_id=f"output-{ordinal}-{campaign['id']}",
                        output_revision_id=f"revision-{ordinal}",
                        result_sha256=result_hashes[ordinal],
                        evidence_sha256=evidence_hashes[ordinal],
                        output_permission_sha256=permission_hashes[ordinal],
                        needs_review=False,
                        review_finding_count=0,
                        verified_at=now,
                    )
                )
            await db.execute(
                update(DocumentTransformQaCorpusCampaign)
                .where(DocumentTransformQaCorpusCampaign.id == campaign["id"])
                .values(
                    state="canary_verified",
                    canary_result_sha256=result_hashes[0],
                    canary_evidence_sha256=evidence_hashes[0],
                    canary_output_permission_sha256=permission_hashes[0],
                    canary_needs_review=False,
                    canary_review_finding_count=0,
                    canary_verified_at=now,
                )
            )
            await db.execute(
                update(DocumentTransformQaCorpusCampaign)
                .where(DocumentTransformQaCorpusCampaign.id == campaign["id"])
                .values(state="running")
            )
            await db.execute(
                update(DocumentTransformQaCorpusCampaign)
                .where(DocumentTransformQaCorpusCampaign.id == campaign["id"])
                .values(state="verified", completed_at=now)
            )

        async with sessions() as db:
            with pytest.raises(DBAPIError, match="terminal submissions are immutable"):
                async with db.begin():
                    await db.execute(
                        delete(DocumentTransformQaCorpusSubmission).where(
                            DocumentTransformQaCorpusSubmission.id == submissions[19]["id"]
                        )
                    )
    finally:
        await engine.dispose()


async def test_real_postgres_rejects_non_monotonic_campaign_and_submission_timestamps() -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    campaign = _campaign_values(uuid.uuid7())
    try:
        async with sessions() as db, db.begin():
            members = await _create_planned_campaign(db, campaign)
            await db.execute(
                update(DocumentTransformQaCorpusCampaign)
                .where(DocumentTransformQaCorpusCampaign.id == campaign["id"])
                .values(state="canary_reserved", canary_task_plan_id=members[0]["task_plan_id"])
            )
        async with sessions() as db:
            with pytest.raises(IntegrityError):
                async with db.begin():
                    await db.execute(
                        insert(DocumentTransformQaCorpusSubmission).values(
                            id=uuid.uuid7(),
                            campaign_id=campaign["id"],
                            member_id=members[0]["id"],
                            task_plan_id=members[0]["task_plan_id"],
                            idempotency_key=members[0]["task_plan_id"],
                            source_fingerprint_sha256=members[0]["source_fingerprint_sha256"],
                            destination_identity_sha256=campaign["destination_identity_sha256"],
                            destination_permission_sha256=campaign["destination_permission_sha256"],
                            state="failed",
                            reserved_at=campaign["created_at"],
                            submitted_at=datetime.now(UTC),
                            task_id=uuid.uuid7(),
                            failed_at=datetime.now(UTC) - timedelta(days=1),
                        )
                    )
    finally:
        async with sessions() as db, db.begin():
            await db.execute(
                update(DocumentTransformQaCorpusCampaign)
                .where(DocumentTransformQaCorpusCampaign.id == campaign["id"])
                .values(state="failed", failed_at=datetime.now(UTC))
            )
        await engine.dispose()
