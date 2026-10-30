import asyncio
from typing import TYPE_CHECKING

from alembic import context

from app.database import Base, build_engine, close_connector
from app.database_config import database_settings
from app.models import (
    auth_session as auth_session,
    document_transform_qa_grant as document_transform_qa_grant,
    document_transform_qa_corpus as document_transform_qa_corpus,
    document_transform_qa_run as document_transform_qa_run,
    mcp_grant as mcp_grant,
    mcp_native_state as mcp_native_state,
    mcp_oauth as mcp_oauth,
    mcp_registration_admission as mcp_registration_admission,
    task as task,
    task_replay_key as task_replay_key,
    user as user,
    wiki as wiki,
)


if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

config = context.config

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    if database_settings.DATABASE_URL is None:
        raise RuntimeError("Offline migrations require DATABASE_URL")
    context.configure(
        url=str(database_settings.DATABASE_URL),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = build_engine()
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()
    await close_connector()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
