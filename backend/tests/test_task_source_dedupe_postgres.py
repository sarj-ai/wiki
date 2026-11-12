from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import uuid

from pydantic_settings import BaseSettings, SettingsConfigDict
import pytest
from sqlalchemy import delete, func, null, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.document_transform.schemas import (
    DocumentOutput,
    DocumentTransformRequest,
    DocumentTransformResult,
    DriveFileSource,
    MarkdownSource,
    TransformMode,
    ValidationSummary,
)
from app.document_transform.unslop import UNSLOP_CATALOG_SHA256
from app.document_transform.versions import DOCUMENT_PIPELINE_VERSION
from app.files.schemas import FileResponse
from app.identifiers import FileId, GoogleUserId, IdempotencyKey, TaskId, UserId
from app.models.task import Task
from app.models.task_replay_key import TaskReplayKey
from app.models.user import User
from app.smart_create.schemas import BlankSource, SmartCreateResult, SmartFileCreate
from app.tasks.errors import (
    TaskIdempotencyConflictError,
    TaskNotClaimableError,
    TaskTargetBusyError,
)
from app.tasks.schemas import DocumentTransformSucceededTaskRead, TaskKind, TaskRead, TaskStatus
from app.tasks.service import PostgresTaskService


class _QASettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WIKI_POLISH29_QA_")
    database_url: str | None = None


def _qa_engine() -> AsyncEngine:
    dsn = _QASettings().database_url
    if not dsn:
        pytest.skip("Requires an explicitly configured disposable PostgreSQL QA database")
    url = make_url(dsn)
    if (
        url.host not in {"127.0.0.1", "localhost", "::1"}
        or not (url.database or "").startswith("wiki_polish29_qa")
        or url.drivername != "postgresql+asyncpg"
    ):
        pytest.fail("Only a loopback PostgreSQL database named wiki_polish29_qa* is allowed")
    return create_async_engine(dsn, pool_size=12, max_overflow=0)


@pytest.mark.parametrize(
    "corruption",
    ["wrong-kind", "foreign-task-id", "foreign-operation-id", "missing-marker", "hybrid"],
)
async def test_real_postgres_result_kind_invariants_and_rollback(corruption: str) -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = UserId(uuid.uuid7())
    kind = (
        TaskKind.FILE_SMART_CREATE
        if corruption == "foreign-task-id"
        else TaskKind.FILE_DOCUMENT_TRANSFORM
    )
    payload = (
        SmartFileCreate(
            source=BlankSource(kind="blank", file_type="document"),
            parent_folder_id=FileId("qa-folder"),
            name="Result invariant fixture",
            emoji="📄",
            alias="qa-result",
        )
        if kind is TaskKind.FILE_SMART_CREATE
        else _markdown_payload()
    )
    try:
        async with sessions() as session:
            session.add(
                User(id=owner, email=f"qa-{owner}@sarj.ai", google_id=GoogleUserId(str(owner)))
            )
            await session.commit()
            service = PostgresTaskService(db=session)
            reservation = await service.create_or_replay(
                kind=kind,
                owner_user_id=owner,
                idempotency_key=IdempotencyKey(uuid.uuid7()),
                payload=payload,
            )
            task_id = reservation.task.id
            claim = await service.claim(task_id, lease_duration=timedelta(minutes=1))
            smart = SmartCreateResult(
                task_id=TaskId(uuid.uuid7()) if corruption == "foreign-task-id" else task_id,
                file=FileResponse(
                    id=FileId("qa-created"),
                    name="Fixture",
                    mime_type="application/vnd.google-apps.document",
                    parent_folder_id=FileId("qa-folder"),
                    drive_id=None,
                    modified_time=None,
                ),
                warnings=[],
            )
            if corruption in {"wrong-kind", "foreign-task-id"}:
                with pytest.raises(IntegrityError):
                    await service.succeed(task_id, lease_token=claim.lease_token, result=smart)
                assert session.is_active, "The service must roll back a rejected transition"
            elif corruption == "foreign-operation-id":
                with pytest.raises(IntegrityError):
                    await service.succeed(
                        task_id,
                        lease_token=claim.lease_token,
                        result=_completed_result(TaskId(uuid.uuid7()), TransformMode.FORMAT),
                    )
                assert session.is_active, "The service must roll back a rejected transition"
            else:
                invalid = _completed_result(task_id, TransformMode.FORMAT).model_dump(mode="json")
                if corruption == "missing-marker":
                    invalid.pop("operation_id")
                else:
                    invalid["file"] = {}
                with pytest.raises(IntegrityError):
                    await session.execute(
                        update(Task)
                        .where(Task.id == task_id)
                        .values(
                            status=TaskStatus.SUCCEEDED,
                            result=invalid,
                            finished_at=func.now(),
                            lease_token=None,
                            lease_expires_at=None,
                        )
                    )
                await session.rollback()
            retained = await service.get_for_owner(task_id, owner)
            assert retained.status is TaskStatus.RUNNING
            valid = (
                SmartCreateResult(task_id=task_id, file=smart.file, warnings=[])
                if kind is TaskKind.FILE_SMART_CREATE
                else _completed_result(task_id, TransformMode.FORMAT)
            )
            completed = await service.succeed(task_id, lease_token=claim.lease_token, result=valid)
            assert completed.status is TaskStatus.SUCCEEDED
    finally:
        async with sessions() as session:
            await session.execute(delete(User).where(User.id == owner))
            await session.commit()
        await engine.dispose()


@pytest.mark.parametrize("source_kind", ["drive", "markdown", "uploaded_markdown"])
@pytest.mark.parametrize("terminal", [TaskStatus.CANCELLED, TaskStatus.SUCCEEDED])
@pytest.mark.parametrize("expired_pending", [False, True])
async def test_real_postgres_concurrent_source_dedupe(
    source_kind: str, terminal: TaskStatus, *, expired_pending: bool
) -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = UserId(uuid.uuid7())
    source = FileId(f"qa-source-{uuid.uuid7()}")
    payload = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file", file_id=source, expected_modified_time="2026-09-05T00:00:00Z"
        ),
        mode=TransformMode.FORMAT,
    )
    if source_kind == "markdown":
        payload = _markdown_payload()
    elif source_kind == "uploaded_markdown":
        payload = DocumentTransformRequest(
            source=MarkdownSource(
                kind="uploaded_markdown",
                upload_id=uuid.uuid7(),
                name="Review",
                destination_folder_id=FileId("qa-folder"),
            ),
            mode=TransformMode.FORMAT,
        )
    keys = [IdempotencyKey(uuid.uuid7()) for _ in range(8)]
    start = asyncio.Event()
    admission_calls = 0

    async def admit() -> None:
        nonlocal admission_calls
        admission_calls += 1
        await asyncio.sleep(0.02)

    async def admit_terminal_replay(_task: object) -> bool:
        return True

    async def reserve(key: IdempotencyKey):
        await start.wait()
        async with sessions() as session:
            return await PostgresTaskService(db=session).create_or_replay(
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                owner_user_id=owner,
                idempotency_key=key,
                payload=payload,
                admit=admit,
                admit_terminal_replay=admit_terminal_replay,
            )

    try:
        async with sessions() as session:
            session.add(
                User(id=owner, email=f"qa-{owner}@sarj.ai", google_id=GoogleUserId(str(owner)))
            )
            await session.commit()
            if expired_pending:
                previous = await PostgresTaskService(db=session).create_or_replay(
                    kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                    owner_user_id=owner,
                    idempotency_key=keys[0],
                    payload=payload,
                )
                await session.execute(
                    update(Task)
                    .where(Task.id == previous.task.id)
                    .values(
                        created_at=func.now() - timedelta(days=8),
                        expires_at=func.now(),
                    )
                )
                await session.commit()
        pending = [asyncio.create_task(reserve(key)) for key in keys]
        start.set()
        reservations = await asyncio.gather(*pending)
        task_id = reservations[0].task.id
        assert {reservation.task.id for reservation in reservations} == {task_id}
        assert sum(reservation.created for reservation in reservations) == 1
        assert admission_calls == len(keys)
        async with sessions() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(Task).where(Task.owner_user_id == owner)
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(TaskReplayKey)
                    .where(TaskReplayKey.owner_user_id == owner)
                )
                == len(keys) - 1
            )
            service = PostgresTaskService(db=session)
            if source_kind == "drive":
                with pytest.raises(TaskTargetBusyError):
                    await service.create_or_replay(
                        kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                        owner_user_id=owner,
                        idempotency_key=IdempotencyKey(uuid.uuid7()),
                        payload=payload.model_copy(update={"mode": TransformMode.UNSLOP}),
                        admit=admit,
                    )
            if terminal is TaskStatus.CANCELLED:
                await service.cancel(task_id, owner_user_id=owner)
            else:
                await session.execute(
                    update(Task)
                    .where(Task.id == task_id)
                    .values(
                        status=terminal,
                        started_at=func.now(),
                        finished_at=func.now(),
                        attempt_count=1,
                        result=_completed_result(task_id, payload.mode).model_dump(mode="json"),
                    )
                )
                await session.commit()
        for key in keys:
            replay = await reserve(key)
            assert replay.task.id == task_id
            assert not replay.created
        async with sessions() as session:
            with pytest.raises(TaskIdempotencyConflictError):
                await PostgresTaskService(db=session).create_or_replay(
                    kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                    owner_user_id=owner,
                    idempotency_key=keys[-1],
                    payload=payload.model_copy(update={"mode": TransformMode.BOTH}),
                    admit=admit,
                )
        deliberate = await reserve(IdempotencyKey(uuid.uuid7()))
        if terminal is TaskStatus.SUCCEEDED:
            assert not deliberate.created
            assert deliberate.task.id == task_id
        else:
            assert deliberate.created
            assert deliberate.task.id != task_id
    finally:
        await engine.dispose()


@pytest.mark.parametrize("source_kind", ["drive", "markdown"])
async def test_real_postgres_concurrent_verified_terminal_replay(source_kind: str) -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = UserId(uuid.uuid7())
    payload = (
        _markdown_payload()
        if source_kind == "markdown"
        else DocumentTransformRequest(
            source=DriveFileSource(
                kind="drive_file",
                file_id=FileId(f"qa-terminal-{uuid.uuid7()}"),
                expected_modified_time="2026-09-07T00:00:00Z",
            ),
            mode=TransformMode.FORMAT,
        )
    )
    admission_calls = 0
    retained_output_checks = 0
    start = asyncio.Event()

    async def admit() -> None:
        nonlocal admission_calls
        admission_calls += 1
        await asyncio.sleep(0.01)

    async def admit_terminal_replay(_task: object) -> bool:
        nonlocal retained_output_checks
        retained_output_checks += 1
        return True

    try:
        async with sessions() as session:
            session.add(
                User(id=owner, email=f"qa-{owner}@sarj.ai", google_id=GoogleUserId(str(owner)))
            )
            await session.commit()
            canonical = await PostgresTaskService(db=session).create_or_replay(
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                owner_user_id=owner,
                idempotency_key=IdempotencyKey(uuid.uuid7()),
                payload=payload,
            )
            await session.execute(
                update(Task)
                .where(Task.id == canonical.task.id)
                .values(
                    status=TaskStatus.SUCCEEDED,
                    started_at=func.now(),
                    finished_at=func.now(),
                    attempt_count=1,
                    result=_completed_result(canonical.task.id, payload.mode).model_dump(
                        mode="json"
                    ),
                )
            )
            await session.commit()

        async def replay(key: IdempotencyKey):
            await start.wait()
            async with sessions() as session:
                return await PostgresTaskService(db=session).create_or_replay(
                    kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                    owner_user_id=owner,
                    idempotency_key=key,
                    payload=payload,
                    admit=admit,
                    admit_terminal_replay=admit_terminal_replay,
                )

        keys = [IdempotencyKey(uuid.uuid7()) for _ in range(12)]
        pending = [asyncio.create_task(replay(key)) for key in keys]
        start.set()
        reservations = await asyncio.gather(*pending)

        assert {reservation.task.id for reservation in reservations} == {canonical.task.id}
        assert not any(reservation.created for reservation in reservations)
        assert admission_calls == len(keys)
        assert retained_output_checks == len(keys)
        async with sessions() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(Task).where(Task.owner_user_id == owner)
                )
                == 1
            )
            assert await session.scalar(
                select(func.count())
                .select_from(TaskReplayKey)
                .where(TaskReplayKey.task_id == canonical.task.id)
            ) == len(keys)
    finally:
        await engine.dispose()


async def test_real_postgres_concurrent_missing_terminal_output_creates_one_replacement() -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = UserId(uuid.uuid7())
    payload = _markdown_payload()
    output_checks = 0
    start = asyncio.Event()

    async def reject_missing_output(_task: object) -> bool:
        nonlocal output_checks
        output_checks += 1
        await asyncio.sleep(0.01)
        return False

    try:
        async with sessions() as session:
            session.add(
                User(id=owner, email=f"qa-{owner}@sarj.ai", google_id=GoogleUserId(str(owner)))
            )
            await session.commit()
            canonical = await PostgresTaskService(db=session).create_or_replay(
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                owner_user_id=owner,
                idempotency_key=IdempotencyKey(uuid.uuid7()),
                payload=payload,
            )
            await session.execute(
                update(Task)
                .where(Task.id == canonical.task.id)
                .values(
                    status=TaskStatus.SUCCEEDED,
                    started_at=func.now(),
                    finished_at=func.now(),
                    attempt_count=1,
                    result=_completed_result(canonical.task.id, payload.mode).model_dump(
                        mode="json"
                    ),
                )
            )
            await session.commit()

        async def replay(key: IdempotencyKey):
            await start.wait()
            async with sessions() as session:
                return await PostgresTaskService(db=session).create_or_replay(
                    kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                    owner_user_id=owner,
                    idempotency_key=key,
                    payload=payload,
                    admit_terminal_replay=reject_missing_output,
                )

        keys = [IdempotencyKey(uuid.uuid7()) for _ in range(12)]
        pending = [asyncio.create_task(replay(key)) for key in keys]
        start.set()
        reservations = await asyncio.gather(*pending)

        replacement_ids = {reservation.task.id for reservation in reservations}
        assert canonical.task.id not in replacement_ids
        assert len(replacement_ids) == 1
        assert sum(reservation.created for reservation in reservations) == 1
        assert output_checks == 1
        replacement_id = replacement_ids.pop()
        async with sessions() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(Task).where(Task.owner_user_id == owner)
                )
                == 2
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(TaskReplayKey)
                    .where(TaskReplayKey.task_id == replacement_id)
                )
                == len(keys) - 1
            )
    finally:
        await engine.dispose()


@pytest.mark.parametrize("terminal_change", ["expiry", "status", "needs_review"])
async def test_real_postgres_terminal_change_during_output_admission_creates_replacement(
    terminal_change: str,
) -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = UserId(uuid.uuid7())
    payload = _markdown_payload()

    try:
        async with sessions() as session:
            session.add(
                User(id=owner, email=f"qa-{owner}@sarj.ai", google_id=GoogleUserId(str(owner)))
            )
            await session.commit()
            canonical = await PostgresTaskService(db=session).create_or_replay(
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                owner_user_id=owner,
                idempotency_key=IdempotencyKey(uuid.uuid7()),
                payload=payload,
            )
            await session.execute(
                update(Task)
                .where(Task.id == canonical.task.id)
                .values(
                    created_at=func.now() - timedelta(days=8),
                    expires_at=func.now() + timedelta(hours=1),
                    status=TaskStatus.SUCCEEDED,
                    started_at=func.now(),
                    finished_at=func.now(),
                    attempt_count=1,
                    result=_completed_result(canonical.task.id, payload.mode).model_dump(
                        mode="json"
                    ),
                )
            )
            await session.commit()

        async def change_terminal(_task: object) -> bool:
            async with sessions() as session:
                values: dict[str, object]
                match terminal_change:
                    case "expiry":
                        values = {"expires_at": func.now()}
                    case "needs_review":
                        values = {
                            "result": _completed_result(canonical.task.id, payload.mode)
                            .model_copy(update={"needs_review": True})
                            .model_dump(mode="json")
                        }
                    case _:
                        values = {
                            "error_code": "qa_terminal_changed",
                            "result": null(),
                            "status": TaskStatus.FAILED,
                        }
                await session.execute(
                    update(Task).where(Task.id == canonical.task.id).values(**values)
                )
                await session.commit()
            return True

        async with sessions() as session:
            replacement = await PostgresTaskService(db=session).create_or_replay(
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                owner_user_id=owner,
                idempotency_key=IdempotencyKey(uuid.uuid7()),
                payload=payload,
                admit_terminal_replay=change_terminal,
            )

        assert replacement.created
        assert replacement.task.id != canonical.task.id
        async with sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(TaskReplayKey)
                    .where(TaskReplayKey.task_id == canonical.task.id)
                )
                == 0
            )
    finally:
        await engine.dispose()


@pytest.mark.parametrize("active_change", ["completed", "expired"])
async def test_real_postgres_active_change_during_source_admission_creates_replacement(
    active_change: str,
) -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = UserId(uuid.uuid7())
    payload = _markdown_payload()
    admission_count = 0
    terminal_checks = 0

    try:
        async with sessions() as session:
            session.add(
                User(id=owner, email=f"qa-{owner}@sarj.ai", google_id=GoogleUserId(str(owner)))
            )
            await session.commit()
            first = await PostgresTaskService(db=session).create_or_replay(
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                owner_user_id=owner,
                idempotency_key=IdempotencyKey(uuid.uuid7()),
                payload=payload,
            )

        async def admit() -> None:
            nonlocal admission_count
            admission_count += 1
            async with sessions() as session:
                if active_change == "completed":
                    values = {
                        "status": TaskStatus.SUCCEEDED,
                        "started_at": func.now(),
                        "finished_at": func.now(),
                        "attempt_count": 1,
                        "result": _completed_result(first.task.id, payload.mode).model_dump(
                            mode="json"
                        ),
                    }
                else:
                    values = {
                        "created_at": func.now() - timedelta(days=8),
                        "expires_at": func.now() - timedelta(seconds=1),
                    }
                await session.execute(update(Task).where(Task.id == first.task.id).values(**values))
                await session.commit()

        async def reject_terminal(_task: object) -> bool:
            nonlocal terminal_checks
            terminal_checks += 1
            return False

        async with sessions() as session:
            replacement = await PostgresTaskService(db=session).create_or_replay(
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                owner_user_id=owner,
                idempotency_key=IdempotencyKey(uuid.uuid7()),
                payload=payload,
                admit=admit,
                admit_terminal_replay=reject_terminal,
            )

        assert replacement.created
        assert replacement.task.id != first.task.id
        assert admission_count == 1
        assert terminal_checks == (1 if active_change == "completed" else 0)
        async with sessions() as session:
            replacement_row = await session.get(Task, replacement.task.id)
            assert replacement_row is not None
            assert replacement_row.dispatch_ready_at is not None
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(TaskReplayKey)
                    .where(TaskReplayKey.task_id == first.task.id)
                )
                == 0
            )
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "terminal_case",
    ["cancelled", "failed", "stale_pipeline", "stale_brand", "needs_review"],
)
async def test_real_postgres_unverified_terminal_never_semantically_replays(
    terminal_case: str,
) -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = UserId(uuid.uuid7())
    payload = _markdown_payload()
    admissions: list[bool] = []

    async def admit() -> None:
        admissions.append(True)

    try:
        async with sessions() as session:
            session.add(
                User(id=owner, email=f"qa-{owner}@sarj.ai", google_id=GoogleUserId(str(owner)))
            )
            await session.commit()
            service = PostgresTaskService(db=session)
            canonical = await service.create_or_replay(
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                owner_user_id=owner,
                idempotency_key=IdempotencyKey(uuid.uuid7()),
                payload=payload,
            )
            match terminal_case:
                case "cancelled":
                    await service.cancel(canonical.task.id, owner_user_id=owner)
                case "failed":
                    await session.execute(
                        update(Task)
                        .where(Task.id == canonical.task.id)
                        .values(
                            status=TaskStatus.FAILED,
                            started_at=func.now(),
                            finished_at=func.now(),
                            attempt_count=1,
                            error_code="qa_failure",
                        )
                    )
                    await session.commit()
                case _:
                    result = _completed_result(canonical.task.id, payload.mode)
                    finished_at = datetime.now(UTC)
                    match terminal_case:
                        case "stale_pipeline":
                            result = result.model_copy(update={"pipeline_version": "legacy"})
                        case "needs_review":
                            result = result.model_copy(update={"needs_review": True})
                        case _:
                            result = result.model_copy(update={"brand_profile_sha256": "0" * 64})
                    await session.execute(
                        update(Task)
                        .where(Task.id == canonical.task.id)
                        .values(
                            status=TaskStatus.SUCCEEDED,
                            started_at=finished_at,
                            finished_at=finished_at,
                            attempt_count=1,
                            result=result.model_dump(mode="json"),
                        )
                    )
                    await session.commit()

        fresh_key = IdempotencyKey(uuid.uuid7())
        async with sessions() as session:
            replacement = await PostgresTaskService(db=session).create_or_replay(
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                owner_user_id=owner,
                idempotency_key=fresh_key,
                payload=payload,
                admit=admit,
            )
            assert replacement.created
            assert replacement.task.id != canonical.task.id
            assert admissions == [True]
            assert (
                await session.scalar(
                    select(TaskReplayKey.task_id).where(
                        TaskReplayKey.owner_user_id == owner,
                        TaskReplayKey.idempotency_key == fresh_key,
                    )
                )
                is None
            )
            assert (
                await session.scalar(
                    select(func.count()).select_from(Task).where(Task.owner_user_id == owner)
                )
                == 2
            )
    finally:
        await engine.dispose()


def _markdown_payload() -> DocumentTransformRequest:
    return DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown",
            content="Plain synthetic content.",
            name="Review",
            destination_folder_id=FileId("qa-folder"),
        ),
        mode=TransformMode.FORMAT,
    )


def _completed_result(operation_id: uuid.UUID, mode: TransformMode) -> DocumentTransformResult:
    return DocumentTransformResult(
        operation_id=operation_id,
        source_sha256="0" * 64,
        output=DocumentOutput(
            file_id=FileId("qa-output"), name="Review", parent_folder_id=FileId("qa-folder")
        ),
        mode=mode,
        applied_patch_count=0,
        findings=[],
        validation=ValidationSummary(
            source_unchanged=True,
            protected_content_preserved=True,
            structure_preserved=True,
            unsafe_patch_count=0,
        ),
        model_ids=[],
        tool_versions={},
        pipeline_version=DOCUMENT_PIPELINE_VERSION,
        needs_review=False,
    )


@pytest.mark.parametrize("change", ["mode", "folder", "name", "text", "output_name", "owner"])
async def test_real_postgres_markdown_distinct_intent_or_owner_never_collapses(change: str) -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = UserId(uuid.uuid7())
    other = UserId(uuid.uuid7())
    payload = _markdown_payload()
    changed = payload
    assert isinstance(payload.source, MarkdownSource)
    if change == "mode":
        changed = payload.model_copy(update={"mode": TransformMode.BOTH})
    elif change == "output_name":
        changed = payload.model_copy(update={"output_name": "Another output"})
    elif change != "owner":
        field = {"folder": "destination_folder_id", "name": "name", "text": "content"}[change]
        changed = payload.model_copy(
            update={"source": payload.source.model_copy(update={field: "different"})}
        )
    try:
        async with sessions() as session:
            session.add_all(
                [
                    User(id=user, email=f"qa-{user}@sarj.ai", google_id=GoogleUserId(str(user)))
                    for user in (owner, other)
                ]
            )
            await session.commit()
        async with sessions() as session:
            first = await PostgresTaskService(db=session).create_or_replay(
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                owner_user_id=owner,
                idempotency_key=IdempotencyKey(uuid.uuid7()),
                payload=payload,
            )
        async with sessions() as session:
            second = await PostgresTaskService(db=session).create_or_replay(
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                owner_user_id=other if change == "owner" else owner,
                idempotency_key=IdempotencyKey(uuid.uuid7()),
                payload=changed,
            )
        assert first.created
        assert second.created
        assert first.task.id != second.task.id
    finally:
        await engine.dispose()


@pytest.mark.parametrize("source_kind", ["drive", "markdown"])
@pytest.mark.parametrize("key_kind", ["fresh", "original", "alias"])
@pytest.mark.parametrize("state", ["pending", "live", "grace", "boundary", "stale"])
async def test_real_postgres_expired_source_recovery_respects_execution_lease(
    source_kind: str, key_kind: str, state: str
) -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = UserId(uuid.uuid7())
    keys = {kind: IdempotencyKey(uuid.uuid7()) for kind in ("fresh", "original", "alias")}
    admissions: list[bool] = []

    async def admit() -> None:
        admissions.append(True)

    payload = (
        _markdown_payload()
        if source_kind == "markdown"
        else DocumentTransformRequest(
            source=DriveFileSource(
                kind="drive_file",
                file_id=FileId("qa-expired-source"),
                expected_modified_time="before",
            ),
            mode=TransformMode.FORMAT,
        )
    )
    try:
        async with sessions() as session:
            session.add(
                User(id=owner, email=f"qa-{owner}@sarj.ai", google_id=GoogleUserId(str(owner)))
            )
            await session.commit()
            service = PostgresTaskService(db=session)
            first = await service.create_or_replay(
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                owner_user_id=owner,
                idempotency_key=keys["original"],
                payload=payload,
            )
            alias = await service.create_or_replay(
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                owner_user_id=owner,
                idempotency_key=keys["alias"],
                payload=payload,
            )
            assert alias.task.id == first.task.id
            await session.execute(
                update(Task)
                .where(Task.id == first.task.id)
                .values(
                    created_at=func.now() - timedelta(days=8),
                    expires_at=func.now(),
                )
            )
            if state == "pending":
                await session.commit()
                with pytest.raises(TaskNotClaimableError):
                    await service.claim(first.task.id, lease_duration=timedelta(minutes=1))
            else:
                await session.execute(
                    update(Task)
                    .where(Task.id == first.task.id)
                    .values(
                        status=TaskStatus.RUNNING,
                        started_at=func.now() - timedelta(hours=1),
                        attempt_count=1,
                        lease_token=uuid.uuid7(),
                        lease_expires_at=func.now()
                        + timedelta(
                            minutes={"live": 5, "grace": -5, "boundary": -10, "stale": -11}[state]
                        ),
                    )
                )
            if state in {"live", "grace"}:
                await session.commit()
                with pytest.raises(TaskTargetBusyError):
                    await service.create_or_replay(
                        kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                        owner_user_id=owner,
                        idempotency_key=keys[key_kind],
                        payload=payload,
                        admit=admit,
                    )
                assert admissions == [True]
                retained = await session.get(Task, first.task.id)
                assert retained is not None
                assert retained.status == TaskStatus.RUNNING
                assert retained.lease_token is not None
                assert await session.scalar(
                    select(Task.expires_at <= func.now()).where(Task.id == first.task.id)
                )
                assert (
                    await session.scalar(
                        select(func.count()).select_from(Task).where(Task.owner_user_id == owner)
                    )
                    == 1
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(TaskReplayKey)
                        .where(TaskReplayKey.task_id == first.task.id)
                    )
                    == 1
                )
                return
            replacement = await service.create_or_replay(
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                owner_user_id=owner,
                idempotency_key=keys[key_kind],
                payload=payload,
                admit=admit,
            )
            assert admissions == [True]
            assert replacement.created
            assert replacement.task.id != first.task.id
            assert (
                await session.scalar(
                    select(func.count()).select_from(Task).where(Task.owner_user_id == owner)
                )
                == 1
            )
            assert await session.get(Task, first.task.id) is None
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(TaskReplayKey)
                    .where(TaskReplayKey.task_id == first.task.id)
                )
                == 0
            )
            claimed = await service.claim(replacement.task.id, lease_duration=timedelta(minutes=1))
            assert claimed.task.id == replacement.task.id
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("mode", "catalog_sha256", "expect_replay"),
    [
        (TransformMode.UNSLOP, UNSLOP_CATALOG_SHA256, True),
        (TransformMode.UNSLOP, "0" * 64, False),
        (TransformMode.UNSLOP, None, False),
        (TransformMode.BOTH, UNSLOP_CATALOG_SHA256, True),
        (TransformMode.BOTH, "0" * 64, False),
        (TransformMode.BOTH, None, False),
        (TransformMode.FORMAT, UNSLOP_CATALOG_SHA256, True),
        (TransformMode.FORMAT, "0" * 64, True),
        (TransformMode.FORMAT, None, True),
    ],
)
async def test_real_postgres_terminal_replay_requires_current_editor_catalog(
    mode: TransformMode, catalog_sha256: str | None, *, expect_replay: bool
) -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = UserId(uuid.uuid7())
    original_key = IdempotencyKey(uuid.uuid7())
    fresh_key = IdempotencyKey(uuid.uuid7())
    payload = _catalog_payload(mode)
    checked_outputs: list[TaskId] = []

    async def admit_terminal(task: TaskRead) -> bool:
        checked_outputs.append(task.id)
        return True

    try:
        canonical = await _seed_catalog_terminal(
            sessions=sessions,
            owner=owner,
            payload=payload,
            key=original_key,
            catalog_sha256=catalog_sha256,
        )
        async with sessions() as session:
            service = PostgresTaskService(db=session)
            retried = await service.create_or_replay(
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                owner_user_id=owner,
                idempotency_key=original_key,
                payload=payload,
            )
            assert retried.task.id == canonical
            assert not retried.created
            assert isinstance(retried.task, DocumentTransformSucceededTaskRead)
            assert isinstance(retried.task.result, DocumentTransformResult)
            assert retried.task.result.tool_versions.get("unslop_catalog_sha256") == catalog_sha256

            fresh = await service.create_or_replay(
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                owner_user_id=owner,
                idempotency_key=fresh_key,
                payload=payload,
                admit_terminal_replay=admit_terminal,
            )
            assert fresh.created is not expect_replay
            assert (fresh.task.id == canonical) is expect_replay
            assert checked_outputs == ([canonical] if expect_replay else [])
            alias = await session.scalar(
                select(TaskReplayKey.task_id).where(
                    TaskReplayKey.owner_user_id == owner,
                    TaskReplayKey.idempotency_key == fresh_key,
                )
            )
            assert alias == (canonical if expect_replay else None)
            if expect_replay:
                review_needed = retried.task.result.model_copy(update={"needs_review": True})
                await session.execute(
                    update(Task)
                    .where(Task.id == canonical)
                    .values(result=review_needed.model_dump(mode="json"))
                )
                await session.commit()
                for retained_key in (original_key, fresh_key):
                    async with sessions() as replay_session:
                        historical = await PostgresTaskService(db=replay_session).create_or_replay(
                            kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                            owner_user_id=owner,
                            idempotency_key=retained_key,
                            payload=payload,
                        )
                        assert not historical.created
                        assert historical.task.id == canonical
                        assert isinstance(historical.task, DocumentTransformSucceededTaskRead)
                        assert isinstance(historical.task.result, DocumentTransformResult)
                        assert historical.task.result.needs_review is True
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("mode", "expect_replay"),
    [(TransformMode.UNSLOP, False), (TransformMode.BOTH, False), (TransformMode.FORMAT, True)],
)
@pytest.mark.parametrize("changed_catalog", ["0" * 64, None])
async def test_real_postgres_terminal_catalog_is_rechecked_after_output_admission(
    mode: TransformMode, changed_catalog: str | None, *, expect_replay: bool
) -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = UserId(uuid.uuid7())
    payload = _catalog_payload(mode)
    fresh_key = IdempotencyKey(uuid.uuid7())
    checked_outputs: list[TaskId] = []
    try:
        canonical = await _seed_catalog_terminal(
            sessions=sessions,
            owner=owner,
            payload=payload,
            key=IdempotencyKey(uuid.uuid7()),
            catalog_sha256=UNSLOP_CATALOG_SHA256,
        )

        async def change_catalog(task: TaskRead) -> bool:
            checked_outputs.append(task.id)
            async with sessions() as session:
                await _store_catalog_terminal(
                    session=session,
                    task_id=canonical,
                    mode=mode,
                    catalog_sha256=changed_catalog,
                )
            return True

        async with sessions() as session:
            fresh = await PostgresTaskService(db=session).create_or_replay(
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                owner_user_id=owner,
                idempotency_key=fresh_key,
                payload=payload,
                admit_terminal_replay=change_catalog,
            )
            assert checked_outputs == [canonical]
            assert fresh.created is not expect_replay
            assert (fresh.task.id == canonical) is expect_replay
            alias = await session.scalar(
                select(TaskReplayKey.task_id).where(
                    TaskReplayKey.owner_user_id == owner,
                    TaskReplayKey.idempotency_key == fresh_key,
                )
            )
            assert alias == (canonical if expect_replay else None)
    finally:
        await engine.dispose()


def _catalog_payload(mode: TransformMode) -> DocumentTransformRequest:
    return DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file",
            file_id=FileId(f"qa-catalog-{uuid.uuid7()}"),
            expected_modified_time="2026-09-07T00:00:00Z",
        ),
        mode=mode,
        output_name="Review",
    )


async def _seed_catalog_terminal(
    *,
    sessions: async_sessionmaker[AsyncSession],
    owner: UserId,
    payload: DocumentTransformRequest,
    key: IdempotencyKey,
    catalog_sha256: str | None,
) -> TaskId:
    async with sessions() as session:
        session.add(User(id=owner, email=f"qa-{owner}@sarj.ai", google_id=GoogleUserId(str(owner))))
        await session.commit()
        original = await PostgresTaskService(db=session).create_or_replay(
            kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
            owner_user_id=owner,
            idempotency_key=key,
            payload=payload,
        )
        await _store_catalog_terminal(
            session=session,
            task_id=original.task.id,
            mode=payload.mode,
            catalog_sha256=catalog_sha256,
        )
    return original.task.id


async def _store_catalog_terminal(
    *, session: AsyncSession, task_id: TaskId, mode: TransformMode, catalog_sha256: str | None
) -> None:
    result = _completed_result(task_id, mode)
    if catalog_sha256 is not None:
        result.tool_versions["unslop_catalog_sha256"] = catalog_sha256
    await session.execute(
        update(Task)
        .where(Task.id == task_id)
        .values(
            status=TaskStatus.SUCCEEDED,
            started_at=func.now(),
            finished_at=func.now(),
            attempt_count=1,
            result=result.model_dump(mode="json"),
        )
    )
    await session.commit()
