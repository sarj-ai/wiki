import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from cryptography.fernet import Fernet
import pytest

from app.mcp.protection import OAuthStateProtector
from app.mcp.storage import PostgresOAuthOperations


class _Session:
    calls = 0

    async def execute(self, *_args: Any, **_kwargs: Any) -> None:
        self.calls += 1


class _Sessions:
    def __init__(self) -> None:
        self.calls = 0
        self.session = _Session()

    @asynccontextmanager
    async def begin(self) -> AsyncGenerator[_Session]:
        self.calls += 1
        yield self.session

    def factory(self) -> Any:
        return self


def _protector() -> OAuthStateProtector:
    return OAuthStateProtector(
        (Fernet.generate_key().decode(),), lock_key=Fernet.generate_key().decode()
    )


async def test_operation_reuses_session_and_rejects_cross_task_and_nested_identity() -> None:
    sessions = _Sessions()
    session: Any = sessions.session
    store = PostgresOAuthOperations(sessions.factory(), _protector())
    async with store.operation("grant:one") as outer, store.operation("grant:one") as inner:
        assert outer is inner is session
        with pytest.raises(RuntimeError, match="same identity"):
            async with store.operation("grant:two"):
                pytest.fail("different nested lock accepted")

        async def child_operation() -> None:
            async with store.operation("grant:one"):
                pytest.fail("child task inherited the operation")

        with pytest.raises(RuntimeError, match="child tasks"):
            await asyncio.create_task(child_operation())
    assert sessions.session.calls == 3
    assert sessions.calls == 1
    async with store.operation("grant:two") as later:
        assert later is session
    assert sessions.calls == 2
