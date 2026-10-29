from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, override

from loguru import logger
import pytest
import uvloop

from app.auth.encryption import encrypt_cache
from app.drive.schemas import DriveTree
from app.identifiers import DriveId
from app.models.wiki import DriveTreeCache
from tests.test_sidebar_cache import FakeDrive, FakeSession, _db, _service, _tree, _user


_PRIVATE_PROVIDER_BODY = "synthetic-provider-response-must-stay-redacted"
type _Outcome = Literal["failure", "cancel", "success"]


class _ControlledDrive(FakeDrive):
    def __init__(self, outcome: _Outcome) -> None:
        super().__init__(_tree())
        self.outcome = outcome
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    @override
    async def walk_drive_folders(self, drive_id: str) -> DriveTree:
        self.walks += 1
        self.started.set()
        await self.release.wait()
        if self.outcome == "failure":
            raise RuntimeError(_PRIVATE_PROVIDER_BODY)
        return self.tree


async def _background_refresh_case(
    outcome: _Outcome, cancel_follower: bool, keep_survivor: bool
) -> None:
    loop = asyncio.get_running_loop()
    reports: list[dict[str, object]] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: reports.append(context))
    messages: list[str] = []
    sink = logger.add(messages.append, format="{message}")
    provider = _ControlledDrive(outcome)
    user = _user()
    service = _service(provider, FakeSession(), user=user)
    drive_id = DriveId("drive-1")
    row = DriveTreeCache(
        user_id=user.id,
        drive_id=drive_id,
        tree=encrypt_cache(provider.tree.model_dump_json()),
        refreshed_at=datetime.now(UTC) - timedelta(days=1),
    )
    owned: list[asyncio.Task[Any]] = []
    try:
        # A stale public lookup starts the native background leader.
        cached = await service.peek_drive_tree(_db(FakeSession(row=row)), drive_id)
        assert cached is not None
        assert cached.tree == provider.tree
        await provider.started.wait()
        background = list(service._bg_tasks)
        owned.extend(background)
        assert len(background) == 1
        cancelled = (
            asyncio.create_task(service.get_drive_tree(_db(FakeSession()), drive_id))
            if cancel_follower
            else None
        )
        survivor = (
            asyncio.create_task(service.get_drive_tree(_db(FakeSession()), drive_id))
            if keep_survivor
            else None
        )
        owned.extend(task for task in (cancelled, survivor) if task is not None)
        # Fake sessions complete inline. One loop turn places both callers in
        # the shared wait before cancellation or provider completion.
        await asyncio.sleep(0)
        assert provider.walks == 1
        if cancelled is not None:
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled
        if outcome == "cancel":
            background[0].cancel()
        else:
            provider.release.set()
        if outcome == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await background[0]
        else:
            await background[0]
        if survivor is not None:
            match outcome:
                case "failure":
                    with pytest.raises(RuntimeError, match=_PRIVATE_PROVIDER_BODY):
                        await survivor
                case "cancel":
                    with pytest.raises(asyncio.CancelledError):
                        await survivor
                case _:
                    assert (await survivor).tree == provider.tree
        assert not service._bg_tasks
        provider.outcome = "success"
        provider.release.set()
        assert (await service.get_drive_tree(_db(FakeSession()), drive_id)).tree == provider.tree
        assert provider.walks == 2
    finally:
        provider.release.set()
        for task in owned:
            if not task.done():
                task.cancel()
        await asyncio.gather(*owned, return_exceptions=True)
        # Drain shield completion callbacks before restoring the observer.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        loop.set_exception_handler(previous_handler)
        logger.remove(sink)

    assert not reports
    assert len(messages) == (1 if outcome == "failure" else 0)
    assert _PRIVATE_PROVIDER_BODY not in "".join(messages)
    if outcome == "failure":
        assert "error_type=RuntimeError" in messages[0]


@pytest.mark.parametrize("use_uvloop", [False, True], ids=["asyncio", "uvloop"])
@pytest.mark.parametrize(
    ("outcome", "cancel_follower", "keep_survivor"),
    [
        ("failure", False, True),
        ("failure", True, True),
        ("failure", True, False),
        ("cancel", False, True),
        ("cancel", True, True),
        ("cancel", True, False),
        ("success", True, True),
        ("success", True, False),
    ],
)
def test_background_refresh_keeps_failure_logs_redacted_after_cancellation(
    use_uvloop: bool, outcome: _Outcome, cancel_follower: bool, keep_survivor: bool
) -> None:
    factory = uvloop.new_event_loop if use_uvloop else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=factory, debug=False) as runner:
        runner.run(_background_refresh_case(outcome, cancel_follower, keep_survivor))
