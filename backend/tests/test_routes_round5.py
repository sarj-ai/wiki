from __future__ import annotations

import asyncio
from collections import UserString
from datetime import UTC, datetime, timedelta
import json
import time
from typing import override
import uuid

import httpx
import jwt
from pydantic import ValidationError
import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.aliases.errors import AliasEditForbiddenError
from app.aliases.service import PostgresAliasService
from app.auth.errors import AuthError, AuthFailedError
from app.auth.jwt import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    ALGORITHM,
    JWT_AUDIENCE,
    JWT_ISSUER,
    REFRESH_TOKEN_EXPIRE_DAYS,
    create_access_token,
    create_refresh_token,
)
from app.auth.service import GoogleAuthService, GoogleIdentity, GoogleTokens
from app.config import settings
from app.drive.schemas import MIME_DOC, MIME_FOLDER, DriveNode, DriveSummary
from app.files.schemas import FileResponse
from app.identifiers import AuthSessionId, DriveId, FileId, GoogleUserId, TokenId, UserId
from app.models.user import User
from app.models.wiki import DocumentAlias
from app.templates.service import DriveTemplateCatalogService
from tests.support import dependency
from tests.test_task_queue import _Client, _enqueuer
from tests.test_task_source_dedupe_postgres import _qa_engine
from tests.test_templates import _TemplateDrive


class _ConflictingIdentityService(GoogleAuthService):
    @override
    async def _resolve_identity_user(self, *, google_id: GoogleUserId, email: str) -> User | None:
        raise AuthFailedError


async def test_rejected_identity_performs_no_token_encryption() -> None:
    class UnconsumedToken(UserString):
        @override
        def encode(self, encoding: str | None = None, errors: str | None = None) -> bytes:
            message = "A rejected identity must not consume provider credentials"
            raise AssertionError(message)

    service = _ConflictingIdentityService(dependency(None), dependency(None))
    with pytest.raises(AuthFailedError):
        await service._upsert_user(
            GoogleIdentity(
                email="recycled@sarj.ai",
                google_id=GoogleUserId("synthetic-subject"),
                display_name=None,
                avatar_url=None,
            ),
            GoogleTokens(
                access_token=dependency(UnconsumedToken("synthetic-access")),
                refresh_token=dependency(UnconsumedToken("synthetic-refresh")),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            ),
        )


def test_integer_jwt_dates_preserve_expiry_and_claims() -> None:
    before = int(time.time())
    user_id = UserId(uuid.uuid7())
    session_id = AuthSessionId(uuid.uuid7())
    refresh_id = TokenId(uuid.uuid7())
    access = create_access_token(user_id, session_id, token_version=4)
    refresh = create_refresh_token(user_id, session_id, refresh_id, token_version=4)
    after = int(time.time())
    for encoded, kind, lifetime in (
        (access, "access", ACCESS_TOKEN_EXPIRE_MINUTES * 60),
        (refresh, "refresh", REFRESH_TOKEN_EXPIRE_DAYS * 86400),
    ):
        payload = jwt.decode(
            encoded,
            settings.SECRET_KEY.get_secret_value(),
            algorithms=[ALGORITHM],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
        )
        assert before <= payload["iat"] <= after
        assert payload["exp"] == payload["iat"] + lifetime
        assert payload["type"] == kind
        assert payload["sub"] == str(user_id)
        assert payload["sid"] == str(session_id)
        assert payload["tv"] == 4
        assert isinstance(payload["iat"], int)
        assert isinstance(payload["exp"], int)


@pytest.mark.parametrize("verified", [False, "true", 1, None])
async def test_json_identity_validation_requires_literal_verified_email(verified: object) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"id": "synthetic-subject", "email": "person@sarj.ai", "verified_email": verified},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = GoogleAuthService(dependency(None), client)
        with pytest.raises(AuthFailedError, match="could not be verified"):
            await service._fetch_userinfo("synthetic-access")


async def test_json_identity_validation_preserves_unicode_profile() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "synthetic-subject",
                "email": "Person@sarj.ai",
                "verified_email": True,
                "name": "Renée 🚀",
                "picture": None,
                "unused_provider_field": {"ignored": True},
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await GoogleAuthService(dependency(None), client)._fetch_userinfo("synthetic")
    assert result.email == "person@sarj.ai"
    assert result.display_name == "Renée 🚀"
    assert result.avatar_url is None


async def test_malformed_oauth_identity_json_returns_redacted_auth_error() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"private_provider_content":', request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(AuthFailedError, match="could not be verified") as caught:
            await GoogleAuthService(dependency(None), client)._fetch_userinfo("synthetic")
    assert "private_provider_content" not in str(caught.value)


@pytest.mark.parametrize("expires_in", [0, -1, 86401])
async def test_json_token_exchange_retains_expiry_bounds(expires_in: int) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"access_token": "synthetic-access", "expires_in": expires_in},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValidationError):
            await GoogleAuthService(dependency(None), client)._exchange_google_code("synthetic")


async def test_single_folder_reads_preserve_categories_and_cycles() -> None:
    drive = _TemplateDrive()
    drive._children[FileId("templates")] = [
        DriveNode(
            id=FileId("one"),
            name="Design",
            mime_type=MIME_FOLDER,
            parent_id=FileId("templates"),
            modified_time=None,
        )
    ]
    drive._children[FileId("one")] = [
        DriveNode(
            id=FileId("template"),
            name="Brief",
            mime_type=MIME_DOC,
            parent_id=FileId("one"),
            modified_time="2026-10-01T00:00:00Z",
        ),
        DriveNode(
            id=FileId("templates"),
            name="Cycle",
            mime_type=MIME_FOLDER,
            parent_id=FileId("one"),
            modified_time=None,
        ),
    ]
    catalog = await DriveTemplateCatalogService(drive=drive, user_id=uuid.uuid7()).list_templates()
    assert len(catalog.templates) == 1
    assert catalog.templates[0].category_path == ["Design"]
    assert drive.calls[FileId("templates")] == 1
    assert drive.calls[FileId("one")] == 1


async def test_single_folder_read_is_cancelled_with_its_traversal() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class DelayedDrive(_TemplateDrive):
        @override
        async def list_template_children(self, folder_id: FileId) -> list[DriveNode]:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return []

    service = DriveTemplateCatalogService(drive=DelayedDrive(), user_id=uuid.uuid7())
    traversal = asyncio.create_task(
        service._walk_template_root(
            DriveSummary(id=DriveId("drive-1"), name="Product"), FileId("templates")
        )
    )
    await started.wait()
    traversal.cancel()
    with pytest.raises(asyncio.CancelledError):
        await traversal
    assert cancelled.is_set()


async def test_combined_identity_read_rejects_conflicts_and_observes_fresh_rows() -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = UserId(uuid.uuid7())
    other = UserId(uuid.uuid7())
    original_email = f"round5-{owner}@sarj.ai"
    other_email = f"round5-{other}@sarj.ai"
    subject = GoogleUserId(f"round5-{owner}")
    statements: list[str] = []

    def record(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    try:
        async with sessions() as db:
            db.add_all(
                [
                    User(id=owner, email=original_email, google_id=subject),
                    User(id=other, email=other_email, google_id=GoogleUserId(f"round5-{other}")),
                ]
            )
            await db.commit()
        async with sessions() as db:
            service = GoogleAuthService(db, dependency(None))
            event.listen(engine.sync_engine, "before_cursor_execute", record)
            identity = await service._resolve_identity_user(google_id=subject, email=original_email)
            event.remove(engine.sync_engine, "before_cursor_execute", record)
            assert identity is not None
            assert identity.id == owner
            assert len(statements) == 1
            with pytest.raises(AuthFailedError):
                await service._resolve_identity_user(google_id=subject, email=other_email)
            with pytest.raises(AuthFailedError):
                await service._resolve_identity_user(
                    google_id=GoogleUserId("unbound-subject"), email=original_email
                )
            renamed = await service._resolve_identity_user(
                google_id=subject, email=f"renamed-{owner}@sarj.ai"
            )
            assert renamed is not None
            assert renamed.id == owner
            assert (
                await service._resolve_identity_user(
                    google_id=GoogleUserId("unbound-subject"), email=f"new-{owner}@sarj.ai"
                )
                is None
            )
    finally:
        async with sessions() as db:
            await db.execute(delete(User).where(User.id.in_([owner, other])))
            await db.commit()
        await engine.dispose()


class _AliasFiles:
    def __init__(self, *, editable: bool) -> None:
        self._editable = editable

    async def get(self, file_id: FileId) -> FileResponse:
        return FileResponse(
            id=file_id,
            name="Synthetic review",
            mime_type=MIME_DOC,
            drive_id=None,
            modified_time=None,
            parent_folder_id=None,
            can_edit=self._editable,
        )


async def test_atomic_alias_retirement_keeps_tombstones_and_fresh_edit_authority() -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = UserId(uuid.uuid7())
    file_id = FileId(f"round5-{owner}")
    alias = "round-five-retirement"
    statements: list[str] = []

    def record(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    try:
        async with sessions() as db:
            user = User(id=owner, email=f"round5-{owner}@sarj.ai", google_id=str(owner))
            db.add(user)
            await db.flush()
            db.add(DocumentAlias(alias=alias, file_id=file_id, created_by=owner))
            await db.commit()
            denied = PostgresAliasService(
                db=db, files=dependency(_AliasFiles(editable=False)), user=user
            )
            event.listen(engine.sync_engine, "before_cursor_execute", record)
            with pytest.raises(AliasEditForbiddenError):
                await denied.retire(file_id)
            assert statements == []
            service = PostgresAliasService(
                db=db, files=dependency(_AliasFiles(editable=True)), user=user
            )
            await service.retire(file_id)
            event.remove(engine.sync_engine, "before_cursor_execute", record)
            assert len(statements) == 1
            assert statements[0].startswith("UPDATE ")
        async with sessions() as db:
            row = await db.get(DocumentAlias, alias)
            assert row is not None
            assert row.retired_at is not None
            assert row.retired_by == owner
            retired_at = row.retired_at
            await db.commit()
            await PostgresAliasService(
                db=db, files=dependency(_AliasFiles(editable=True)), user=user
            ).retire(file_id)
        async with sessions() as db:
            row = await db.scalar(select(DocumentAlias).where(DocumentAlias.alias == alias))
            assert row is not None
            assert row.retired_at == retired_at
        async with sessions() as db:
            pending_user = await db.get(User, owner)
            assert pending_user is not None
            pending_user.display_name = "Pending unrelated profile"
            await PostgresAliasService(
                db=db, files=dependency(_AliasFiles(editable=True)), user=pending_user
            ).retire(FileId(f"absent-{owner}"))
            assert db.in_transaction()
            await db.rollback()
        async with sessions() as db:
            persisted_user = await db.get(User, owner)
            assert persisted_user is not None
            assert persisted_user.display_name is None
    finally:
        async with sessions() as db:
            await db.execute(delete(DocumentAlias).where(DocumentAlias.alias == alias))
            await db.execute(delete(User).where(User.id == owner))
            await db.commit()
        await engine.dispose()


async def test_queue_templates_keep_concurrent_enqueue_requests_independent() -> None:
    from google.cloud import tasks_v2

    ready = asyncio.Event()
    release = asyncio.Event()

    class ConcurrentClient(_Client):
        def __init__(self) -> None:
            super().__init__()
            self.requests: list[tasks_v2.Task] = []

        @override
        async def create_task(self, *, parent: str, task: tasks_v2.Task) -> tasks_v2.Task:
            self.requests.append(task)
            if len(self.requests) == 4:
                ready.set()
            await release.wait()
            return task

    client = ConcurrentClient()
    enqueuer = _enqueuer(client)
    ids = [uuid.uuid7() for _ in range(4)]
    enqueue = [
        asyncio.create_task(enqueuer.enqueue_smart_create(dependency(ids[0]))),
        asyncio.create_task(enqueuer.enqueue_smart_create(dependency(ids[1]))),
        asyncio.create_task(enqueuer.enqueue_document_transform(dependency(ids[2]))),
        asyncio.create_task(enqueuer.enqueue_document_transform(dependency(ids[3]))),
    ]
    await ready.wait()
    assert len({id(task) for task in client.requests}) == len(ids)
    assert len({task.name for task in client.requests}) == len(ids)
    assert {json.loads(task.http_request.body)["task_id"] for task in client.requests} == {
        str(task_id) for task_id in ids
    }
    for task in client.requests:
        assert task.http_request.oidc_token.audience == task.http_request.url
        assert dict(task.http_request.headers) == {"Content-Type": "application/json"}
    client.requests[0].http_request.headers["previous-request"] = "synthetic"
    client.requests[0].http_request.body = b"previous request body"
    release.set()
    await asyncio.gather(*enqueue)
    next_id = uuid.uuid7()
    await enqueuer.enqueue_smart_create(dependency(next_id))
    assert json.loads(client.requests[-1].http_request.body) == {"task_id": str(next_id)}
    assert dict(client.requests[-1].http_request.headers) == {"Content-Type": "application/json"}


@pytest.mark.parametrize("identity_case", ["same_subject", "same_email"])
async def test_callback_identity_race_cannot_create_or_take_over_two_accounts(
    identity_case: str,
) -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    marker = uuid.uuid7()
    email = f"round5-race-{marker}@sarj.ai"
    subjects = [GoogleUserId(f"round5-race-{marker}-{index}") for index in range(2)]
    if identity_case == "same_subject":
        subjects[1] = subjects[0]
    ready = asyncio.Event()
    lookups = 0

    class RacingService(GoogleAuthService):
        def __init__(self, db: AsyncSession, identity: GoogleIdentity) -> None:
            super().__init__(db, dependency(None))
            self._identity = identity

        @override
        async def _exchange_google_code(self, code: str) -> GoogleTokens:
            return GoogleTokens(
                access_token="synthetic-access",
                refresh_token=None,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )

        @override
        async def _fetch_userinfo(self, google_access_token: str) -> GoogleIdentity:
            return self._identity

        @override
        async def _resolve_identity_user(
            self, *, google_id: GoogleUserId, email: str
        ) -> User | None:
            nonlocal lookups
            user = await super()._resolve_identity_user(google_id=google_id, email=email)
            lookups += 1
            if lookups == 2:
                ready.set()
            await ready.wait()
            return user

    async def callback(subject: GoogleUserId) -> bool:
        async with sessions() as db:
            service = RacingService(
                db,
                GoogleIdentity(email=email, google_id=subject, display_name=None, avatar_url=None),
            )
            try:
                await service.exchange_code_for_session("synthetic-code", "same", "same")
            except AuthError:
                return False
            return True

    try:
        results = await asyncio.gather(*(callback(subject) for subject in subjects))
        assert sum(results) == 1
        async with sessions() as db:
            users = list((await db.scalars(select(User).where(User.email == email))).all())
            assert len(users) == 1
            assert users[0].google_id in subjects
    finally:
        async with sessions() as db:
            await db.execute(delete(User).where(User.email == email))
            await db.commit()
        await engine.dispose()
