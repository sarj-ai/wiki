from __future__ import annotations

import asyncio
import importlib.util
from io import StringIO
from pathlib import Path
from typing import Protocol, runtime_checkable

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict
import pytest
from sqlalchemy import inspect, select
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.auth.errors import DomainRestrictedError
from app.auth.jwt import decode_token
from app.auth.service import GOOGLE_OAUTH_URL, GOOGLE_USERINFO_URL, GoogleAuthService
from app.config import settings
from app.database_config import database_settings
from app.identifiers import GoogleUserId
from app.models.auth_session import UserSession
from app.models.user import User
from app.models.wiki import DocumentAlias


_BACKEND = Path(__file__).parents[1]
_HEAD = "0024_configurable_email_domain"


@runtime_checkable
class _Migration(Protocol):
    revision: str
    down_revision: str

    def upgrade(self) -> None: ...

    def downgrade(self) -> None: ...


class _QASettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WIKI_AUTH_MIGRATION_QA_")
    database_url: str | None = None


def _migration(filename: str) -> _Migration:
    spec = importlib.util.spec_from_file_location(
        "auth_domain_migration", _BACKEND / "alembic/versions" / filename
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert isinstance(module, _Migration)
    return module


def _config() -> Config:
    config = Config(str(_BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND / "alembic"))
    return config


def test_seed_revision_preserves_history_without_seeding_or_deleting_aliases() -> None:
    seed = _migration("0003_seed_org_chart_alias.py")
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )

    with Operations.context(context):
        seed.upgrade()
        seed.downgrade()

    assert not output.getvalue()
    assert (seed.revision, seed.down_revision) == (
        "0003_seed_org_chart_alias",
        "0002_document_aliases",
    )
    scripts = ScriptDirectory.from_config(_config())
    assert scripts.get_heads() == [_HEAD]
    head = scripts.get_revision(_HEAD)
    assert head is not None
    assert head.down_revision == "0023_storage_hot_paths"


def _drop_obsolete_check_again(connection: Connection) -> None:
    migration = _migration("0024_configurable_email_domain.py")
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()


async def test_fresh_migrations_and_oauth_support_only_the_configured_company_domain() -> None:
    dsn = _QASettings().database_url
    if not dsn:
        pytest.skip(
            "Requires an explicitly configured fresh disposable auth migration PostgreSQL database"
        )
    url = make_url(dsn)
    if (
        url.host not in {"127.0.0.1", "localhost", "::1"}
        or not (url.database or "").startswith("wiki_auth_migration_qa_")
        or url.drivername != "postgresql+asyncpg"
    ):
        pytest.fail("Only a loopback PostgreSQL database named wiki_auth_migration_qa_* is allowed")
    assert database_settings.DATABASE_URL is not None
    assert make_url(str(database_settings.DATABASE_URL)) == url
    assert settings.ALLOWED_DOMAIN == "example.org"
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            assert await connection.run_sync(lambda sync: inspect(sync).get_table_names()) == []
        await asyncio.to_thread(command.upgrade, _config(), "0023_storage_hot_paths")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            assert list((await db.execute(select(DocumentAlias.alias))).scalars()) == []
            db.add(User(email="member@example.org", google_id=GoogleUserId("pre-upgrade-subject")))
            with pytest.raises(IntegrityError, match="ck_users_email_domain"):
                await db.commit()
            await db.rollback()

        await asyncio.to_thread(command.upgrade, _config(), "head")
        async with engine.begin() as connection:
            checks = await connection.run_sync(
                lambda sync: inspect(sync).get_check_constraints("users")
            )
            assert "ck_users_email_domain" not in {check["name"] for check in checks}
            await connection.run_sync(_drop_obsolete_check_again)

        def provider(email: str) -> httpx.MockTransport:
            def respond(request: httpx.Request) -> httpx.Response:
                if str(request.url) == GOOGLE_OAUTH_URL:
                    return httpx.Response(
                        200,
                        json={
                            "access_token": "test-google-access",
                            "refresh_token": "test-google-refresh",
                            "expires_in": 3600,
                        },
                    )
                assert str(request.url) == GOOGLE_USERINFO_URL
                return httpx.Response(
                    200, json={"email": email, "id": f"subject-{email}", "verified_email": True}
                )

            return httpx.MockTransport(respond)

        async with (
            sessions() as db,
            httpx.AsyncClient(transport=provider("member@example.org")) as client,
        ):
            auth = GoogleAuthService(db, client)
            result = await auth.exchange_code_for_session("code", "state", "state")
            user = (await db.execute(select(User))).scalar_one()
            session = (await db.execute(select(UserSession))).scalar_one()
            assert user.email == "member@example.org"
            assert session.user_id == user.id
            assert decode_token(result.jwt_access).sub == user.id
            assert decode_token(result.jwt_refresh).sub == user.id

        for rejected in (
            "member@sarj.ai",
            "member@outside.example.org",
            "member@example.org.evil.example.com",
        ):
            async with sessions() as db, httpx.AsyncClient(transport=provider(rejected)) as client:
                auth = GoogleAuthService(db, client)
                with pytest.raises(DomainRestrictedError):
                    await auth.exchange_code_for_session("code", "state", "state")
                assert list((await db.execute(select(User.email))).scalars()) == [
                    "member@example.org"
                ]
                assert len(list((await db.execute(select(UserSession))).scalars())) == 1
    finally:
        await engine.dispose()
