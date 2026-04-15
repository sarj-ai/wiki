from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,  # detect dead connections before using them
    pool_recycle=180,  # recycle connections every 3 min (Cloud SQL may drop idle sooner)
    pool_size=5,  # max persistent connections
    max_overflow=5,  # allow 5 more under load
    pool_timeout=30,  # wait max 30s for a connection from the pool
    connect_args={
        "server_settings": {"statement_timeout": "120000"},  # 120s per statement
    },
)
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with async_session() as session:
        yield session
