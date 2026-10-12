from datetime import datetime, timedelta
from enum import StrEnum
import re
import secrets
from typing import override

from pydantic import BaseModel
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp.native_schemas import NativeOAuthError
from app.mcp.native_state_port import NativeStatePort
from app.mcp.protection import OAuthStateProtector
from app.models.mcp_native_state import McpNativeState


_CLIENT_STATE_LIMIT = 1000
_TOKEN_STATE_LIMIT = 10000


class NativePurpose(StrEnum):
    CLIENT = "client"
    INTENT = "intent"
    CODE = "code"
    ACCESS = "access"
    REFRESH = "refresh"


def new_native_secret(purpose: NativePurpose) -> str:
    return f"wm_{purpose}_{secrets.token_urlsafe(32)}"


def valid_native_secret(purpose: NativePurpose, value: str) -> bool:
    return re.fullmatch(rf"wm_{purpose}_[A-Za-z0-9_-]{{43}}", value) is not None


async def native_database_time(db: AsyncSession) -> datetime:
    now = await db.scalar(select(func.clock_timestamp()))
    if not isinstance(now, datetime):
        raise NativeOAuthError
    return now


class NativeStateStore(NativeStatePort):
    def __init__(self, protector: OAuthStateProtector) -> None:
        self._protector = protector

    @override
    async def promote_client(self, db: AsyncSession, key: str, expires_at: datetime) -> None:
        now = await native_database_time(db)
        if not now < expires_at <= now + timedelta(days=31):
            raise NativeOAuthError
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:identity)"),
            {"identity": self._protector.lock_id("native-state-quota")},
        )
        updated = list(
            await db.scalars(
                update(McpNativeState)
                .where(
                    McpNativeState.purpose == NativePurpose.CLIENT,
                    McpNativeState.key_digest.in_(
                        self._protector.physical_keys("native:client", key)
                    ),
                    McpNativeState.expires_at > func.clock_timestamp(),
                )
                .values(expires_at=func.greatest(McpNativeState.expires_at, expires_at))
                .returning(McpNativeState.key_digest)
            )
        )
        if len(updated) != 1:
            raise NativeOAuthError

    @override
    async def get[T: BaseModel](
        self, db: AsyncSession, purpose: str, key: str, model: type[T]
    ) -> T | None:
        purpose = NativePurpose(purpose)
        namespace = f"native:{purpose}"
        rows = list(
            await db.scalars(
                select(McpNativeState).where(
                    McpNativeState.purpose == purpose,
                    McpNativeState.key_digest.in_(self._protector.physical_keys(namespace, key)),
                    McpNativeState.expires_at > func.clock_timestamp(),
                )
            )
        )
        if len(rows) > 1:
            raise NativeOAuthError
        if not rows:
            return None
        return model.model_validate(self._protector.unprotect(namespace, key, rows[0].ciphertext))

    @override
    async def put(
        self,
        db: AsyncSession,
        purpose: str,
        key: str,
        value: BaseModel,
        expires_at: datetime,
    ) -> None:
        purpose = NativePurpose(purpose)
        namespace = f"native:{purpose}"
        now = await native_database_time(db)
        if not now < expires_at <= now + timedelta(days=31):
            raise NativeOAuthError
        # All writers lock quota buckets in the same order. Expiry cleanup is bounded.
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:identity)"),
            {"identity": self._protector.lock_id("native-state-quota")},
        )
        expired = (
            select(McpNativeState.purpose, McpNativeState.key_digest)
            .where(McpNativeState.expires_at <= func.clock_timestamp())
            .limit(100)
            .cte("expired_native")
        )
        await db.execute(
            delete(McpNativeState).where(
                McpNativeState.purpose == expired.c.purpose,
                McpNativeState.key_digest == expired.c.key_digest,
            )
        )
        digests = self._protector.physical_keys(namespace, key)
        existing = await db.scalar(
            select(McpNativeState.key_digest).where(
                McpNativeState.purpose == purpose,
                McpNativeState.key_digest.in_(digests),
                McpNativeState.expires_at > func.clock_timestamp(),
            )
        )
        if existing is None:
            count = await db.scalar(
                select(func.count())
                .select_from(McpNativeState)
                .where(
                    McpNativeState.purpose == purpose,
                    McpNativeState.expires_at > func.clock_timestamp(),
                )
            )
            if (count or 0) >= (
                _CLIENT_STATE_LIMIT
                if purpose in {NativePurpose.CLIENT, NativePurpose.INTENT}
                else _TOKEN_STATE_LIMIT
            ):
                raise NativeOAuthError
        await db.execute(
            delete(McpNativeState).where(
                McpNativeState.purpose == purpose, McpNativeState.key_digest.in_(digests[1:])
            )
        )
        statement = insert(McpNativeState).values(
            purpose=purpose,
            key_digest=digests[0],
            expires_at=expires_at,
            ciphertext=self._protector.protect(namespace, key, value.model_dump(mode="json")),
        )
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=[McpNativeState.purpose, McpNativeState.key_digest],
                set_={"ciphertext": statement.excluded.ciphertext, "expires_at": expires_at},
            )
        )
