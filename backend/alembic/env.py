import asyncio

from alembic import context

from app.config import settings
from app.database import Base, build_engine, close_connector
from app.models import wiki, user  # noqa: F401 — side-effect: register models on Base

config = context.config

target_metadata = Base.metadata


def run_migrations_offline() -> None:
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
    connectable = build_engine()
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()
    await close_connector()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
