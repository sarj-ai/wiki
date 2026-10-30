from datetime import datetime
from typing import Protocol

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession


class NativeStatePort(Protocol):
    async def promote_client(self, db: AsyncSession, key: str, expires_at: datetime) -> None: ...

    async def get[T: BaseModel](
        self, db: AsyncSession, purpose: str, key: str, model: type[T]
    ) -> T | None: ...

    async def put(
        self, db: AsyncSession, purpose: str, key: str, value: BaseModel, expires_at: datetime
    ) -> None: ...
