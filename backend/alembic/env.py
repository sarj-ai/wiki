import asyncio
from logging.config import fileConfig

from alembic import context

from app.config import settings
from app.database import Base, build_engine, close_connector
from app.models import wiki, user  # noqa: F401 — side-effect: register models on Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    # Offline mode is a dev-only tool for emitting SQL scripts; it needs
    # a literal URL and can't go through the Cloud SQL connector.
    if settings.DATABASE_URL is None:
        raise RuntimeError("Offline migrations require DATABASE_URL")
    context.configure(
        url=str(settings.DATABASE_URL),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    # Shared factory so startup migrations use the same auth path
    # (password DSN or IAM connector) as the app itself.
    connectable = build_engine()
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()
    await close_connector()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
