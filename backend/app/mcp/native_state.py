from datetime import datetime, timedelta
from enum import StrEnum
import re
import secrets
from typing import assert_never, override

from pydantic import BaseModel, JsonValue
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp.native_schemas import (
    AccessPayload,
    CodePayload,
    NativeCredential,
    NativeIntent,
    NativeIntentPayload,
    NativeOAuthError,
    RefreshPayload,
)
from app.mcp.native_state_port import NativeStatePort
from app.mcp.protection import OAuthStateProtector
from app.models.mcp_oauth import MCPOAuthEntry


_CLIENT_STATE_LIMIT = 1000
_TOKEN_STATE_LIMIT = 10_000


class NativePurpose(StrEnum):
    CLIENT = "client"
    INTENT = "intent"
    CODE = "code"
    ACCESS = "access"
    REFRESH = "refresh"


def native_state_payload(
    purpose: NativePurpose, value: BaseModel, expires_at: datetime
) -> dict[str, JsonValue]:
    payload = value.model_dump(mode="json")
    state: NativeIntent | NativeCredential
    match purpose:
        case NativePurpose.CLIENT:
            return payload
        case NativePurpose.INTENT:
            state = NativeIntentPayload.model_validate(payload).root
        case NativePurpose.CODE:
            state = CodePayload.model_validate(payload).root
        case NativePurpose.ACCESS:
            state = AccessPayload.model_validate(payload).root
        case NativePurpose.REFRESH:
            state = RefreshPayload.model_validate(payload).root
        case _:
            assert_never(purpose)
    if state.expires_at != expires_at:
        raise NativeOAuthError
    return state.model_dump(mode="json")


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
                update(MCPOAuthEntry)
                .where(
                    MCPOAuthEntry.collection == "native:client",
                    MCPOAuthEntry.key_digest.in_(
                        self._protector.physical_keys("native:client", key)
                    ),
                    MCPOAuthEntry.expires_at > func.clock_timestamp(),
                )
                .values(expires_at=func.greatest(MCPOAuthEntry.expires_at, expires_at))
                .returning(MCPOAuthEntry.key_digest)
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
                select(MCPOAuthEntry).where(
                    MCPOAuthEntry.collection == namespace,
                    MCPOAuthEntry.key_digest.in_(self._protector.physical_keys(namespace, key)),
                    MCPOAuthEntry.expires_at > func.clock_timestamp(),
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
        payload = native_state_payload(purpose, value, expires_at)
        now = await native_database_time(db)
        if not now < expires_at <= now + timedelta(days=31):
            raise NativeOAuthError
        # All writers lock quota buckets in the same order. Expiry cleanup is bounded.
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:identity)"),
            {"identity": self._protector.lock_id("native-state-quota")},
        )
        expired = (
            select(MCPOAuthEntry.collection, MCPOAuthEntry.key_digest)
            .where(MCPOAuthEntry.expires_at <= func.clock_timestamp())
            .limit(100)
            .cte("expired_native")
        )
        await db.execute(
            delete(MCPOAuthEntry).where(
                MCPOAuthEntry.collection == expired.c.collection,
                MCPOAuthEntry.key_digest == expired.c.key_digest,
            )
        )
        digests = self._protector.physical_keys(namespace, key)
        existing = await db.scalar(
            select(MCPOAuthEntry.key_digest).where(
                MCPOAuthEntry.collection == namespace,
                MCPOAuthEntry.key_digest.in_(digests),
                MCPOAuthEntry.expires_at > func.clock_timestamp(),
            )
        )
        if existing is None:
            count = await db.scalar(
                select(func.count())
                .select_from(MCPOAuthEntry)
                .where(
                    MCPOAuthEntry.collection == namespace,
                    MCPOAuthEntry.expires_at > func.clock_timestamp(),
                )
            )
            if (count or 0) >= (
                _CLIENT_STATE_LIMIT
                if purpose in {NativePurpose.CLIENT, NativePurpose.INTENT}
                else _TOKEN_STATE_LIMIT
            ):
                raise NativeOAuthError
        await db.execute(
            delete(MCPOAuthEntry).where(
                MCPOAuthEntry.collection == namespace, MCPOAuthEntry.key_digest.in_(digests[1:])
            )
        )
        statement = insert(MCPOAuthEntry).values(
            collection=namespace,
            key_digest=digests[0],
            expires_at=expires_at,
            ciphertext=self._protector.protect(namespace, key, payload),
        )
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=[MCPOAuthEntry.collection, MCPOAuthEntry.key_digest],
                set_={"ciphertext": statement.excluded.ciphertext, "expires_at": expires_at},
            )
        )
